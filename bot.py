import os
import threading
import time
from flask import Flask
import requests
import telebot
import yt_dlp

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
      "أهلاً بك يا سِنآل! 🤖\n\nأرسل لي أي رابط (TikTok, YouTube, Instagram,"
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }

    # 1. معالجة روابط تيك توك عبر tikwm (مستقرة وسريعة)
    if "tiktok.com" in url or "douyin.com" in url:
      api_url = f"https://tikwm.com/api/?url={url}&hd=1"
      response = requests.get(api_url, headers=headers, timeout=15)

      if response.status_code == 200:
        res = response.json()
        if res.get("code") == 0:
          video_url = res["data"].get("hdplay") or res["data"].get("play")
          if video_url:
            vid_data = requests.get(
                video_url, headers=headers, timeout=25
            ).content
            downloaded_file = f"downloads/tiktok_{chat_id}.mp4"
            with open(downloaded_file, "wb") as f:
              f.write(vid_data)
          else:
            raise Exception("لم يتم العثور على رابط الفيديو في تيك توك.")
        else:
          raise Exception("رابط تيك توك غير صالح أو غير موجود.")
      else:
        raise Exception(f"خطأ في خدمة تيك توك ({response.status_code})")

    # 2. معالجة روابط يوتيوب عبر واجهة API موثوقة ومحدثة لـ Cobalt
    elif "youtube.com" in url or "youtu.be" in url:
      api_endpoint = "https://api.cobalt.tools/api/json"
      payload = {"url": url, "vQuality": "720"}
      api_headers = {
          "Accept": "application/json",
          "Content-Type": "application/json",
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          ),
      }

      response = requests.post(
          api_endpoint, json=payload, headers=api_headers, timeout=25
      )

      if response.status_code == 200:
        res = response.json()
        status = res.get("status")

        if status == "stream" or status == "redirect":
          video_url = res.get("url")
          if video_url:
            head_resp = requests.head(video_url, headers=headers, timeout=10)
            file_size = int(head_resp.headers.get("Content-Length", 0))

            if file_size > 50 * 1024 * 102
