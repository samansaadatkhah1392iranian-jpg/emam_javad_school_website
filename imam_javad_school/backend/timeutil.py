"""Tehran local clock helpers (fixed UTC+03:30, compatible with Python 3.8)."""
from datetime import datetime, timezone, timedelta
TEHRAN = timezone(timedelta(hours=3, minutes=30), name='Asia/Tehran')
def tehran_now():
    return datetime.now(TEHRAN)
def tehran_naive():
    return tehran_now().replace(tzinfo=None)
def parse_tehran_datetime(value):
    parsed = datetime.fromisoformat(str(value).replace('T', ' '))
    if parsed.tzinfo is not None:
        return parsed.astimezone(TEHRAN).replace(tzinfo=None)
    return parsed
