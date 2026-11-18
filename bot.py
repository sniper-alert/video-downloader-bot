import os
import threading
import time
from flask import Flask
import requests
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
import yt_dlp

# جلب توكن البوت من متغيرات البيئة بأمان
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN is missing in environment variables!")

bot = telebot.TeleBot(TOKEN)

# إعداد سيرفر Flask مصغر للحفاظ على نشاط البوت
app = Flask("")


@app.route("/")
def home():
    return "Snal Bot is running 24/7!"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# رسالة البدء
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(
        message,
        "أهلاً بك يا سِنآل! 🤖\n\nأرسل لي أي رابط (TikTok, Instagram, X/Twitter) وسأعرض لك خيارات التحميل المناسبة.",
    )


# استقبال الروابط وإظهار الأزرار التفاعلية
@bot.message_handler(func=lambda message: True)
def handle_link(message):
    url = message.text.strip()
    print(f"📥 [تتبع] تم استلام رابط من المستخدم {message.chat.id}: {url}")

    if not url.startswith("http"):
        bot.reply_to(message, "الرجاء إرسال رابط صحيح يبدأ بـ http أو https.")
        return

    # فحص ما إذا كان الرابط يتبع لمنصة يوتيوب
    if "youtube.com" in url or "youtu.be" in url:
        bot.reply_to(message, "⚠️ اليوتيوب غير مفعل الآن .. سيفعل مستقبلاً.")
        return

    # بناء أزرار خيارات التحميل
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("🎥 فيديو عالي", callback_data=f"dl_hd_{url}"),
        InlineKeyboardButton("🎥 فيديو متوسط", callback_data=f"dl_sd_{url}"),
        InlineKeyboardButton("🎵 تحويل صوت MP3", callback_data=f"dl_mp3_{url}"),
    )

    bot.reply_to(
        message,
        "🎬 اختر طريقة التحميل المطلوبة:",
        reply_markup=markup,
    )


# معالجة الضغط على الأزرار
@bot.callback_query_handler(func=lambda call: call.data.startswith("dl_"))
def callback_query(call):
    data_parts = call.data.split("_", 2)
    if len(data_parts) < 3:
        return

    action = data_parts[1]  # hd, sd, أو mp3
    url = data_parts[2]
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    # تعديل رسالة الأزرار لإعلام المستخدم ببدء العمل
    bot.edit_message_text(
        "⏳ جاري تجهيز الطلب، انتظر قليلاً...",
        chat_id=chat_id,
        message_id=message_id,
    )

    threading.Thread(
        target=process_download, args=(chat_id, url, action, message_id)
    ).start()


def process_download(chat_id, url, action, processing_msg_id):
    os.makedirs("downloads", exist_ok=True)
    downloaded_file = None

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

        # معالجة خاصة لروابط تيك توك عبر TikWM لتجاوز حظر الـ IP نهائياً
        if "tiktok.com" in url or "douyin.com" in url:
            api_url = f"https://tikwm.com/api/?url={url}&hd=1"
            response = requests.get(api_url, headers=headers, timeout=15)

            if response.status_code == 200:
                res = response.json()
                if res.get("code") == 0:
                    data_info = res.get("data", {})
                    
                    # اختيار الرابط حسب طلب المستخدم (فيديو عالي، متوسط، أو صوت)
                    if action == "mp3":
                        media_url = data_info.get("music")
                        ext = "mp3"
                    else:
                        media_url = data_info.get("hdplay") or data_info.get("play")
                        ext = "mp4"

                    if media_url:
                        media_data = requests.get(media_url, headers=headers, timeout=30).content
                        downloaded_file = f"downloads/tiktok_{chat_id}.{ext}"
                        with open(downloaded_file, "wb") as f:
                            f.write(media_data)
                    else:
                        raise Exception("لم يتم العثور على رابط الوسائط في تيك توك.")
                else:
                    raise Exception("رابط تيك توك غير صالح أو غير موجود.")
            else:
                raise Exception(f"خطأ في خدمة تيك توك ({response.status_code})")

        # معالجة باقي المنصات (إنستغرام، إكس) عبر yt-dlp
        else:
            output_template = f"downloads/file_{chat_id}_%(id)s.%(ext)s"
            ydl_opts = {
                "outtmpl": output_template,
                "max_filesize": 50 * 1024 * 1024,
                "nocheckcertificate": True,
                "ignoreerrors": False,
                "quiet": True,
                "geo_bypass": True,
                "http_headers": headers,
            }

            if action == "hd":
                ydl_opts["format"] = "bestvideo[filesize<50M]+bestaudio/best[filesize<50M]/best"
            elif action == "sd":
                ydl_opts["format"] = "best[height<=360][filesize<50M]/best[filesize<50M]/best"
            elif action == "mp3":
                ydl_opts["format"] = "bestaudio/best"
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)
                if action == "mp3" and not downloaded_file.endswith(".mp3"):
                    downloaded_file = os.path.splitext(downloaded_file)[0] + ".mp3"

        # إرسال الملف بعد التحميل الناجح
        if downloaded_file and os.path.exists(downloaded_file):
            bot.edit_message_text(
                "📤 جاري رفع الملف إليك...",
                chat_id=chat_id,
                message_id=processing_msg_id,
            )

            with open(downloaded_file, "rb") as f:
                if downloaded_file.endswith((".mp3", ".m4a", ".wav", ".aac")):
                    bot.send_audio(chat_id, f)
                else:
                    bot.send_video(chat_id, f, supports_streaming=True)

            bot.delete_message(chat_id, processing_msg_id)
        else:
            bot.edit_message_text(
                "❌ عذراً، لم أتمكن من العثور على ملف قابل للتحميل.",
                chat_id=chat_id,
                message_id=processing_msg_id,
            )

    except Exception as e:
        print(f"Error details: {e}")
        error_msg = f"❌ تعذر التحميل: {str(e)[:70]}"
        try:
            bot.edit_message_text(
                error_msg, chat_id=chat_id, message_id=processing_msg_id
            )
        except Exception:
            bot.send_message(chat_id, error_msg)

    finally:
        if downloaded_file and os.path.exists(downloaded_file):
            try:
                os.remove(downloaded_file)
            except Exception as e:
                print(f"Error removing file: {e}")


# نقطة التشغيل الرئيسية
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print("Flask server started successfully and port is bound.")

    time.sleep(2)

    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
        print("Webhook cleaned successfully!")
    except Exception as e:
        print(f"Webhook remove error: {e}")

    print("Bot is starting polling...")
    while Time_true := True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)
