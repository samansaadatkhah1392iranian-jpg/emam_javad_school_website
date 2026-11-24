from pathlib import Path
import os
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'database' / 'school.db'
UPLOAD_DIR = BASE_DIR / 'uploads'
BACKUP_DIR = BASE_DIR / 'backups'
LOG_DIR = BASE_DIR / 'logs'
HOST = os.environ.get('SCHOOL_HOST', '0.0.0.0')
PORT = int(os.environ.get('SCHOOL_PORT', '8080'))
SESSION_HOURS = 8
MAX_BODY = 7 * 1024 * 1024
