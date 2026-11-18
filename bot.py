import os
import threading
import time
from flask import Flask
import requests
import telebot

# جلب توكن البوت من متغيرات البيئة بأمان
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
  raise ValueError("BOT_TOKEN is missing in environment variables!")

bot = telebot.TeleBot(TOKEN)

# إعداد سيرفر Flask مصغر للحفاظ على نشاط البوت على Render
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
      "أهلاً بك يا سِنآل! 🤖\n\nأرسل لي أي رابط (YouTube, TikTok, Instagram,"
      " X/Twitter) وسأقوم بتحميله وإرساله لك فوراً بجودة عالية.",
  )


# استقبال الروابط ومعالجتها
@bot.message_handler(func=lambda message: True)
def handle_link(message):
  url = message.text.strip()
  print(f"📥 [تتبع] تم استلام رابط من المستخدم {message.chat.id}: {url}")

  if not url.startswith("http"):
    bot.reply_to(message, "الرجاء إرسال رابط صحيح يبدأ بـ http أو https.")
    return

  msg = bot.reply_to(message, "⏳ جاري التحميل، انتظر قليلاً...")

  threading.Thread(
      target=download_and_send, args=(message.chat.id, url, msg.message_id)
  ).start()


def download_and_send(chat_id, url, processing_msg_id):
  os.makedirs("downloads", exist_ok=True)
  downloaded_file = None

  try:
    # استخدام الخادم النشط والمستقر لخدمة التحميل
    api_url = "https://co.wuk.sh/"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ),
    }
    payload = {"url": url, "videoQuality": "720"}

    response = requests.post(
        api_url, json=payload, headers=headers, timeout=25
    )

    if response.status_code != 200:
      raise Exception(f"خطأ في الاتصال بخدمة التحميل ({response.status_code})")

    res = response.json()
    status = res.get("status")

    download_url = None
    if status in ["redirect", "tunnel"]:
      download_url = res.get("url")
    elif status == "picker":
      pickers = res.get("picker", [])
      if pickers:
        download_url = pickers[0].get("url")

    if not download_url:
      raise Exception("عذراً، لم نتمكن من استخراج رابط التحميل لهذا الفيديو.")

    # تحميل الملف الفعلي
    vid_response = requests.get(
        download_url, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=45
    )
    if vid_response.status_code != 200:
      raise Exception("فشل تحميل الملف من المصدر.")

    # فحص الحجم (تجنب تجاوز 50 ميجابايت لتيليجرام)
    file_size = int(vid_response.headers.get("Content-Length", 0))
    if file_size > 50 * 1024 * 1024:
      raise Exception("حجم الفيديو أكبر من 50 ميجابايت، عذراً لا يمكن إرساله.")

    downloaded_file = f"downloads/media_{chat_id}.mp4"
    with open(downloaded_file, "wb") as f:
      for chunk in vid_response.iter_content(chunk_size=8192):
        if chunk:
          f.write(chunk)

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
          "❌ عذراً، لم أتمكن من حفظ الملف.",
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
  while True:
    try:
      bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
    except Exception as e:
      print(f"Polling error: {e}")
      time.sleep(5)
