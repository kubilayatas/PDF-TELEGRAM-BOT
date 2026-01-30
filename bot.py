import os
import time
import logging
import threading
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from flask import Flask

# --- WEB SUNUCUSU (Render için) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Gemini 2.5 Bot Calisiyor!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- AYARLAR ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PDF_KLASORU = "pdfs"
# Listendeki en uygun hızlı model:
MODEL_ISMI = "gemini-2.5-flash" 

# --- LOGLAMA ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- GEMINI CLIENT KURULUMU ---
# Yeni SDK'da 'configure' yerine Client nesnesi kullanılıyor
client = genai.Client(api_key=GOOGLE_API_KEY)

# --- KULLANICI DURUMLARI ---
user_sessions = {}

def get_pdf_files():
    """PDF klasöründeki dosyaları listeler."""
    if not os.path.exists(PDF_KLASORU):
        os.makedirs(PDF_KLASORU)
        return []
    files = [f for f in os.listdir(PDF_KLASORU) if f.lower().endswith('.pdf')]
    return files

async def show_file_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = get_pdf_files()
    if not files:
        await update.message.reply_text("📂 'pdfs' klasöründe dosya bulunamadı.")
        return

    keyboard = []
    for file_name in files:
        keyboard.append([InlineKeyboardButton(file_name, callback_data=file_name)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"🤖 **Gemini 2.5 Asistanı**\n\nAnaliz etmek istediğin dökümanı seç:"
    
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
    await show_file_menu(update, context)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    selected_file = query.data
    
    await query.answer()
    await query.edit_message_text(text=f"⏳ **{selected_file}** yükleniyor... (Model: {MODEL_ISMI})")

    file_path = os.path.join(PDF_KLASORU, selected_file)
    
    try:
        # --- YENİ SDK İLE DOSYA YÜKLEME ---
        # 1. Dosyayı Yükle
        uploaded_file = client.files.upload(file=file_path, config={'display_name': selected_file})
        
        # 2. İşlenmesini Bekle
        while uploaded_file.state == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
            
        if uploaded_file.state == "FAILED":
            await query.message.reply_text("❌ Dosya Google tarafından işlenemedi.")
            return

        # 3. Sohbeti Başlat (Yeni SDK Sözdizimi)
        chat = client.chats.create(
            model=MODEL_ISMI,
            history=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(
                            file_uri=uploaded_file.uri,
                            mime_type=uploaded_file.mime_type
                        ),
                        types.Part.from_text(text="Bu dökümanı analiz et ve sorularıma cevap vermeye hazır ol.")
                    ]
                )
            ]
        )
        
        user_sessions[user_id] = {'chat': chat, 'filename': selected_file}
        
        await query.message.reply_text(f"✅ **{selected_file}** analize hazır!\n\nSorularını sorabilirsin.\n🔄 Menüye dönmek için /reset yaz.")
        
    except Exception as e:
        await query.message.reply_text(f"⚠️ Hata: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    if user_id not in user_sessions:
        await update.message.reply_text("⚠️ Önce bir dosya seçmelisin. /start yaz.")
        return

    session_data = user_sessions[user_id]
    chat = session_data['chat']
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    try:
        # --- YENİ SDK İLE MESAJ GÖNDERME ---
        response = chat.send_message(user_text)
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"Bir hata oluştu: {e}")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
    await update.message.reply_text("🔄 Oturum kapatıldı.")
    await show_file_menu(update, context)

if __name__ == '__main__':
    t = threading.Thread(target=run_web_server)
    t.start()

    if TELEGRAM_TOKEN:
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('reset', reset))
        application.add_handler(CallbackQueryHandler(button_click))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print(f"Bot {MODEL_ISMI} modeli ile başlatılıyor...")
        application.run_polling()
    else:
        print("TELEGRAM_TOKEN bulunamadı!")