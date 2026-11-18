
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import telebot
import yt_dlp
from flask import Flask, jsonify


# =========================================================
# 1. الإعدادات
# =========================================================

TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Set it in your environment variables."
    )

PORT = int(os.environ.get("PORT", "10000"))

# حد أقصى محافظ لحجم الملف المرسل إلى تيليجرام.
MAX_FILE_SIZE = 49 * 1024 * 1024

# عدد التحميلات التي تعمل في الوقت نفسه.
MAX_WORKERS = 3

# مجلد الملفات المؤقتة.
DOWNLOAD_DIR = Path(
    os.environ.get("DOWNLOAD_DIR", "downloads")
).resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# المنصات المدعومة مبدئيًا.
ALLOWED_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
    "x.com",
    "twitter.com",
)

# إعداد السجلات.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("snal_bot")

bot = telebot.TeleBot(
    TOKEN,
    threaded=True,
    num_threads=MAX_WORKERS,
)

app = Flask(__name__)

# يمنع إنشاء عدد غير محدود من مهام التحميل المتزامنة.
executor = ThreadPoolExecutor(
    max_workers=MAX_WORKERS,
    thread_name_prefix="video-download",
)

# حد احتياطي على مستوى التنزيل.
download_slots = threading.BoundedSemaphore(MAX_WORKERS)


# =========================================================
# 2. خادم Flask
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "service": "Snal Bot",
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


def run_flask():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


# =========================================================
# 3. التحقق من الروابط
# =========================================================

def validate_url(raw_url):
    if not raw_url:
        return False

    try:
        parsed = urlparse(raw_url.strip())

        if parsed.scheme not in ("http", "https"):
            return False

        if not parsed.hostname:
            return False

        # رفض الروابط التي تحتوي على بيانات دخول مضمّنة.
        if parsed.username is not None or parsed.password is not None:
            return False

        hostname = parsed.hostname.lower().rstrip(".")

        # لا نقبل إلا نطاقات المنصات المعروفة.
        if not any(
            hostname == domain
            or hostname.endswith("." + domain)
            for domain in ALLOWED_DOMAINS
        ):
            return False

        return True

    except (ValueError, UnicodeError):
        return False


# =========================================================
# 4. أدوات مساعدة
# =========================================================

def update_status(chat_id, message_id, text):
    """تحديث رسالة الحالة، وإرسال رسالة بديلة عند فشل التحديث."""
    try:
        bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
        )
    except Exception:
        try:
            bot.send_message(chat_id, text)
        except Exception:
            logger.exception("Failed to update Telegram status message")


def check_download_size(progress):
    """إيقاف التنزيل عند تجاوز الحد أثناء تقدم التحميل."""
    if progress.get("status") != "downloading":
        return

    downloaded = progress.get("downloaded_bytes", 0)

    if downloaded > MAX_FILE_SIZE:
        raise yt_dlp.utils.DownloadError(
            "File exceeded the configured size limit."
        )


def find_downloaded_file(folder):
    """
    البحث عن ملف الوسائط الناتج فعليًا بدل افتراض أن
    prepare_filename يعكس اسم الملف النهائي دائمًا.
    """
    ignored_suffixes = (
        ".part",
        ".ytdl",
        ".json",
        ".description",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".vtt",
        ".srt",
        ".ass",
        ".lrc",
    )

    candidates = []

    for path in folder.iterdir():
        if not path.is_file():
            continue

        if path.name.endswith(ignored_suffixes):
            continue

        if path.name.startswith("."):
            continue

        try:
            size = path.stat().st_size
        except OSError:
            continue

        if size > 0:
            candidates.append((size, path))

    if not candidates:
        return None

    # المجلد مخصص لعملية تحميل واحدة، لذا نختار أكبر
    # ملف وسائط مرشح ونتحقق من حجمه قبل الإرسال.
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def send_media(chat_id, file_path):
    """إرسال الملف وفق امتداده."""
    suffix = file_path.suffix.lower()

    with file_path.open("rb") as media:
        if suffix in (".mp3", ".m4a", ".aac", ".wav", ".ogg"):
            bot.send_audio(chat_id, media)

        elif suffix == ".mp4":
            bot.send_video(
                chat_id,
                media,
                supports_streaming=True,
            )

        else:
            # صيغ الفيديو الأخرى قد لا يقبلها send_video.
            bot.send_document(chat_id, media)


# =========================================================
# 5. تحميل الفيديو وإرساله
# =========================================================

def download_and_send(chat_id, url, status_message_id):
    acquired_slot = False

    try:
        # انتظار توفر مكان للتحميل.
        download_slots.acquire()
        acquired_slot = True

        update_status(
            chat_id,
            status_message_id,
            "⏳ جارٍ تحميل الفيديو، يرجى الانتظار...",
        )

        # مجلد مستقل لكل عملية لمنع تضارب أسماء الملفات.
        with tempfile.TemporaryDirectory(
            prefix="snal_",
            dir=str(DOWNLOAD_DIR),
        ) as temp_dir:

            output_template = os.path.join(
                temp_dir,
                "%(id)s.%(ext)s",
            )

            ydl_opts = {
                # اختيار ملف واحد لتقليل الحاجة إلى دمج
                # الصوت والفيديو باستخدام FFmpeg.
                "format": (
                    f"best[filesize<={MAX_FILE_SIZE}]"
                    f"/best[filesize_approx<={MAX_FILE_SIZE}]"
                    "/best"
                ),

                "outtmpl": output_template,
                "max_filesize": MAX_FILE_SIZE,

                # لا تحمّل قوائم التشغيل كاملة.
                "noplaylist": True,

                # مهلات وإعادات محاولة للشبكة.
                "socket_timeout": 20,
                "retries": 2,
                "fragment_retries": 2,
                "file_access_retries": 2,

                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": False,

                # لا نعطّل التحقق من شهادات HTTPS.
                "nocheckcertificate": False,

                # إيقاف التنزيل عند تجاوز الحجم.
                "progress_hooks": [check_download_size],

                # لا تحفظ ملفات وصف أو صور مصغرة إضافية.
                "writedescription": False,
                "writeinfojson": False,
                "writethumbnail": False,
                "writesubtitles": False,
                "writeautomaticsub": False,

                # رؤوس HTTP الافتراضية؛ بعض المنصات قد
                # تحتاج إلى إعدادات إضافية أو تسجيل دخول.
                "http_headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                },
            }

            logger.info(
                "Download started | chat_id=%s | host=%s",
                chat_id,
                urlparse(url).hostname,
            )

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)

            downloaded_file = find_downloaded_file(
                Path(temp_dir)
            )

            if downloaded_file is None:
                raise RuntimeError(
                    "لم يتم العثور على ملف وسائط بعد التحميل."
                )

            # فحص الحجم الفعلي النهائي، وليس الحجم المتوقع فقط.
            actual_size = downloaded_file.stat().st_size

            if actual_size > MAX_FILE_SIZE:
                raise RuntimeError(
                    "حجم الفيديو يتجاوز الحد المسموح للإرسال."
                )

            if actual_size <= 0:
                raise RuntimeError("الملف الناتج فارغ.")

            update_status(
                chat_id,
                status_message_id,
                "📤 اكتمل التحميل، جارٍ الإرسال إلى تيليجرام...",
            )

            send_media(chat_id, downloaded_file)

            logger.info(
                "Download and send completed | chat_id=%s",
                chat_id,
            )

            update_status(
                chat_id,
                status_message_id,
                "✅ تم تحميل الملف وإرساله بنجاح.",
            )

    except yt_dlp.utils.DownloadError as exc:
        logger.warning(
            "yt-dlp failed | chat_id=%s | error=%s",
            chat_id,
            exc,
        )

        update_status(
            chat_id,
            status_message_id,
            (
                "❌ تعذر تحميل الفيديو. قد يكون الرابط غير مدعوم، "
                "أو أن المنصة تمنع التحميل، أو أن حجم الملف كبير."
            ),
        )

    except Exception:
        logger.exception(
            "Download/send failed | chat_id=%s",
            chat_id,
        )

        update_status(
            chat_id,
            status_message_id,
            (
                "❌ حدث خطأ أثناء التحميل أو الإرسال. "
                "تحقق من الرابط وحاول مجددًا لاحقًا."
            ),
        )

    finally:
        if acquired_slot:
            download_slots.release()


# =========================================================
# 6. استقبال رسائل تيليجرام
# =========================================================

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(
        message,
        (
            "أهلًا بك في Snal Bot 🤖\n\n"
            "أرسل رابط فيديو من إحدى المنصات المدعومة:\n"
            "• YouTube\n"
            "• TikTok\n"
            "• Instagram\n"
            "• X / Twitter\n\n"
            "سيحاول البوت تحميل الفيديو وإرساله لك، "
            "وفق قيود المنصة وحجم الملف المسموح."
        ),
    )


@bot.message_handler(content_types=["text"])
def handle_link(message):
    url = (message.text or "").strip()

    # تجاهل الأوامر غير المعروفة.
    if url.startswith("/"):
        return

    if not validate_url(url):
        bot.reply_to(
            message,
            (
                "⚠️ أرسل رابطًا صحيحًا من إحدى المنصات "
                "المدعومة: YouTube أو TikTok أو Instagram أو X."
            ),
        )
        return

    try:
        status_message = bot.reply_to(
            message,
            "⏳ تم استلام الرابط، جارٍ تجهيز التحميل...",
        )

        executor.submit(
            download_and_send,
            message.chat.id,
            url,
            status_message.message_id,
        )

    except Exception:
        logger.exception("Failed to schedule download")

        try:
            bot.reply_to(
                message,
                "❌ تعذر بدء التحميل الآن. حاول مرة أخرى.",
            )
        except Exception:
            logger.exception("Failed to notify user")


@bot.message_handler(
    content_types=[
        "photo",
        "video",
        "audio",
        "voice",
        "document",
        "sticker",
        "animation",
        "video_note",
        "contact",
        "location",
    ]
)
def handle_non_text(message):
    bot.reply_to(
        message,
        "أرسل رابط الفيديو كنص حتى أتمكن من معالجته.",
    )


# =========================================================
# 7. تشغيل البوت
# =========================================================

def main():
    flask_thread = threading.Thread(
        target=run_flask,
        name="flask-server",
        daemon=True,
    )
    flask_thread.start()

    logger.info("Flask server started on port %s", PORT)

    # نتأكد من إزالة Webhook قبل بدء Long Polling.
    try:
        bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook removed successfully")
    except Exception:
        logger.exception("Could not remove webhook")

    while True:
        try:
            logger.info("Starting Telegram polling")

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True,
                allowed_updates=["message"],
            )

        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            break

        except Exception:
            logger.exception(
                "Polling failed; retrying in 5 seconds"
            )
            time.sleep(5)

    executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
