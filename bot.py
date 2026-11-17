import os

# تحديث أداة التحميل لأحدث إصدار تلقائياً
os.system("pip install --upgrade yt-dlp")

import threading
from flask import Flask
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
import yt_dlp

# --- بداية السيرفر الوهمي (لحل مشكلة Render) ---
app = Flask("")


@app.route("/")
def home():
  return "I am alive!"


def run():
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


def keep_alive():
  t = threading.Thread(target=run)
  t.start()


# ---------------------------------------------

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN", "9239041330:AAEk8woAvKhdslKCpPZUs9V4n2vs8Br3jB4"
)
bot = telebot.TeleBot(BOT_TOKEN)

user_links = {}


@bot.message_handler(commands=["start"])
def send_welcome(message):
  print(
      f"📥 [تتبع] تم استلام أمر start من المستخدم:"
      f" {message.from_user.first_name}"
  )
  bot.reply_to(
      message,
      "أهلاً بك معك سِنآل! 👋\nأرسل لي رابط الفيديو (تيك توك، يوتيوب، إنستغرام،"
      " X) وسأقوم بتحميله لك.",
  )


# التقاط أي رسالة تحتوي على رابط للتأكد من الاستلام
@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
  text = message.text if message.text else ""
  print(f"📥 [تتبع عام] وصلت رسالة من {message.chat.id}: {text}")

  if text.startswith("http"):
    chat_id = message.chat.id
    user_links[chat_id] = text.strip()

    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("🎬 تحميل الفيديو", callback_data="vid_best"),
        InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data="audio"),
    )

    bot.reply_to(message, "اختر صيغة التحميل المطلوبة:", reply_markup=markup)
  else:
    if not text.startswith("/"):
      bot.reply_to(
          message, "الرجاء إرسال رابط صحيح يبدأ بـ http أو https لتحميله."
      )


@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
  chat_id = call.message.chat.id
  url = user_links.get(chat_id)

  if not url:
    bot.answer_callback_query(call.id, "انتهت صلاحية الرابط، أرسله مجدداً.")
    return

  bot.edit_message_text(
      "⏳ جاري التحميل والمعالجة... يرجى الانتظار.",
      chat_id,
      call.message.message_id,
  )

  file_prefix = f"download_{chat_id}"

  ydl_opts = {
      "outtmpl": f"{file_prefix}.%(ext)s",
      "quiet": True,
      "no_warnings": True,
      "format": "best[filesize<50M]/worst[ext=mp4]/best",
      "geo_bypass": True,
      "nocheckcertificate": True,
      "extractor_args": {"youtube": {"player_client": ["android", "mweb"]}},
      "http_headers": {
          "User-Agent": (
              "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML,"
              " like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"
          )
      },
  }

  if call.data == "audio":
    ydl_opts["format"] = "bestaudio/best"
    ydl_opts["postprocessors"] = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }
    ]

  try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
      info = ydl.extract_info(url, download=True)
      filename = ydl.prepare_filename(info)
      if call.data == "audio":
        filename = os.path.splitext(filename)[0] + ".mp3"

    if not os.path.exists(filename):
      raise Exception("فشل حفظ الملف على الخادم.")

    file_size_mb = os.path.getsize(filename) / (1024 * 1024)
    if file_size_mb > 50:
      bot.edit_message_text(
          f"❌ عذراً، حجم الفيديو ({file_size_mb:.1f}MB) كبير جداً ويتجاوز حد"
          " تيليجرام.",
          chat_id,
          call.message.message_id,
      )
      os.remove(filename)
      return

    bot.edit_message_text(
        "📤 جاري إرسال الملف إليك...", chat_id, call.message.message_id
    )

    with open(filename, "rb") as file:
      if call.data == "audio":
        bot.send_audio(chat_id, file)
      else:
        bot.send_video(chat_id, file)

    os.remove(filename)
    bot.delete_message(chat_id, call.message.message_id)

  except Exception as e:
    bot.edit_message_text(
        f"❌ حدث خطأ أثناء التحميل.\nالتفاصيل: {str(e)[:100]}",
        chat_id,
        call.message.message_id,
    )
    for f in os.listdir():
      if f.startswith(file_prefix):
        try:
          os.remove(f)
        except:
          pass


# --- تشغيل السيرفر الوهمي والبوت معاً ---
if __name__ == "__main__":
  keep_alive()
  print("البوت يعمل الآن ويقوم بتنظيف الويب هوك...")

  try:
    bot.remove_webhook()
    bot.delete_webhook(drop_pending_updates=True)
  except Exception as e:
    print(f"Webhook error: {e}")

  print("بدء استقبال الرسائل (Polling)...")
  while True:
    try:
      bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
    except Exception as e:
      print(f"Polling error: {e}")
      import time

      time.sleep(5)
