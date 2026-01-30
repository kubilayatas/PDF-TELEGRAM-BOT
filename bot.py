import os
import time
import logging
import threading
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from flask import Flask

# --- WEB SUNUCUSU (Render için Gerekli) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Kutuphane Botu Calisiyor!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- AYARLAR ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PDF_KLASORU = "pdfs"  # PDF'lerin olduğu klasör adı

# --- LOGLAMA ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- GEMINI KURULUMU ---
genai.configure(api_key=GOOGLE_API_KEY)

# --- KULLANICI DURUMLARI ---
# Her kullanıcının hangi dosyayı seçtiğini ve sohbet geçmişini burada tutacağız
# Yapı: { user_id: { 'session': chat_session_objesi, 'filename': 'dosya_adi.pdf' } }
user_sessions = {}

def get_pdf_files():
    """PDF klasöründeki dosyaları listeler."""
    if not os.path.exists(PDF_KLASORU):
        os.makedirs(PDF_KLASORU)
        return []
    files = [f for f in os.listdir(PDF_KLASORU) if f.lower().endswith('.pdf')]
    return files

async def show_file_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcıya dosya seçim menüsünü gösterir."""
    files = get_pdf_files()
    
    if not files:
        await update.message.reply_text("Henüz 'pdfs' klasöründe hiç dosya yok.")
        return

    keyboard = []
    for file_name in files:
        # Butonun üzerinde dosya adı yazar, arkada verisi gönderilir
        keyboard.append([InlineKeyboardButton(file_name, callback_data=file_name)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = "📚 **Kütüphaneye Hoş Geldin!**\n\nLütfen incelemek istediğin dökümanı seç:"
    
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        # Eğer bir butona basıldıysa ve menü tekrar çağrılıyorsa
        await update.callback_query.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Kullanıcı başlat dediğinde veya reset attığında mevcut oturumu sil
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        
    await show_file_menu(update, context)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dosya seçildiğinde çalışır."""
    query = update.callback_query
    user_id = query.from_user.id
    selected_file = query.data
    
    await query.answer() # Bekleme ikonunu kaldır
    await query.edit_message_text(text=f"📂 **{selected_file}** seçildi. Dosya Gemini'ye yükleniyor, lütfen bekle...")

    file_path = os.path.join(PDF_KLASORU, selected_file)
    
    try:
        # 1. Dosyayı Gemini'ye yükle
        sample_file = genai.upload_file(path=file_path, display_name=selected_file)
        
        # 2. İşlenmesini bekle
        while sample_file.state.name == "PROCESSING":
            time.sleep(2)
            sample_file = genai.get_file(sample_file.name)
            
        if sample_file.state.name == "FAILED":
            await query.message.reply_text("❌ Dosya yüklenirken hata oluştu.")
            return

        # 3. Sohbet Oturumunu Başlat
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=f"Sen uzman bir asistansın. Şu an kullanıcının seçtiği '{selected_file}' dökümanını analiz ediyorsun. Sadece bu dökümana göre cevap ver."
        )

        chat_session = model.start_chat(
            history=[{"role": "user", "parts": [sample_file, "Bu dökümanı analiz et ve hazır ol."]}]
        )
        
        # Oturumu kaydet
        user_sessions[user_id] = {
            'session': chat_session,
            'filename': selected_file
        }
        
        await query.message.reply_text(f"✅ **{selected_file}** hazır!\n\nSorularını sorabilirsin.\n\n🔄 Başka dosyaya geçmek için /reset yaz.")
        
    except Exception as e:
        await query.message.reply_text(f"Hata oluştu: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Kullanıcı dosya seçmiş mi kontrol et
    if user_id not in user_sessions:
        await update.message.reply_text("⚠️ Lütfen önce bir dosya seçin. Menüyü görmek için /start yazın.")
        return

    # Seçili oturumu al
    session_data = user_sessions[user_id]
    chat_session = session_data['session']
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    try:
        response = chat_session.send_message(user_text)
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"Bir hata oluştu: {e}")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Oturumu kapatır ve menüye döner."""
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id] # Hafızadan sil
    
    await update.message.reply_text("🔄 Oturum sıfırlandı.")
    await show_file_menu(update, context)

if __name__ == '__main__':
    # Web sunucusunu başlat
    t = threading.Thread(target=run_web_server)
    t.start()

    if TELEGRAM_TOKEN:
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('reset', reset)) # Reset komutu eklendi
        application.add_handler(CallbackQueryHandler(button_click)) # Buton tıklamalarını yakalar
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print("Bot Polling Başlıyor...")
        application.run_polling()
    else:
        print("TELEGRAM_TOKEN eksik!")