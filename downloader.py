import asyncio
import re
import traceback
from pathlib import Path

import yt_dlp

from config import DOWNLOADS

URL_RE = re.compile(r'https?://\S+')
ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

SUPPORTED = (
    'youtube.com', 'youtu.be',
    'tiktok.com',
    'instagram.com',
    'twitter.com', 'x.com',
)

MAX_HEIGHT = 720
MAX_FILESIZE = 49 * 1024 * 1024


class DownloadError(Exception):
    pass


def strip_ansi(s):
    return ANSI_RE.sub('', s)


def extract_url(text):
    if not text:
        return None
    m = URL_RE.search(text)
    return m.group(0) if m else None


def is_supported(url):
    low = url.lower()
    return any(d in low for d in SUPPORTED)


def _format_selector(max_height):
    return (
        f'bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/'
        f'best[height<={max_height}][ext=mp4]/'
        f'best[height<={max_height}]/best'
    )


def _download_sync(url, out_dir, prefix, max_height):
    outtmpl = str(Path(out_dir) / f'{prefix}.%(ext)s')

    opts = {
        'format': _format_selector(max_height),
        'outtmpl': outtmpl,
        'quiet': False,
        'no_warnings': False,
        'noplaylist': True,
        'merge_output_format': 'mp4',
        'max_filesize': MAX_FILESIZE,
        'retries': 2,
        'socket_timeout': 20,
        'restrictfilenames': True,
        'nocheckcertificate': True,
                'no_color': True,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/145.0.0.0 Safari/537.36'
            ),
        },
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise DownloadError('no info returned')
        path = Path(ydl.prepare_filename(info))
        if not path.exists():
            for f in Path(out_dir).glob(f'{prefix}.*'):
                path = f
                break
        if not path.exists():
            raise DownloadError('file not found after download')
        return path, info


async def download(url, user_id, message_id, max_height=MAX_HEIGHT):
    prefix = f'{user_id}_{message_id}'
    try:
        path, info = await asyncio.to_thread(
            _download_sync, url, DOWNLOADS, prefix, max_height
        )
    except yt_dlp.utils.DownloadError as e:
        msg = strip_ansi(str(e)).strip()
        print('[yt-dlp error]', repr(msg))
        first = msg.split('\n')[0].strip() if msg else ''
        first = re.sub(r'^ERROR:\s*', '', first)
        if 'max-filesize' in first.lower() or 'larger than' in first.lower():
            raise DownloadError(
                f'video is larger than {MAX_FILESIZE // 1024 // 1024} MB'
            )
        if not first:
            first = 'download failed (check console)'
        raise DownloadError(first[:200])
    except DownloadError:
        raise
    except Exception as e:
        print('[download exception]')
        traceback.print_exc()
        raise DownloadError(strip_ansi(str(e))[:200] or 'unknown error')

    title = info.get('title') or 'video'
    duration = info.get('duration') or 0
    uploader = info.get('uploader') or ''

    return {
        'path': path,
        'title': title,
        'duration': duration,
        'uploader': uploader,
        'size': path.stat().st_size,
    }


def cleanup(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def fmt_duration(seconds):
    if not seconds:
        return '—'
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


def fmt_size(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f} {u}' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024
    return f'{n:.1f} TB'
