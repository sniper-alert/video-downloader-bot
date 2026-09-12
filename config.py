import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).parent.resolve()
DOWNLOADS = ROOT / 'downloads'
DOWNLOADS.mkdir(exist_ok=True)

load_dotenv(ROOT / '.env')

BOT_TOKEN = os.getenv('BOT_TOKEN')

if not BOT_TOKEN:
    raise SystemExit(
        'BOT_TOKEN not set. Create .env file with BOT_TOKEN=...'
    )