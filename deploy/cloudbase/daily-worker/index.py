import os
os.environ.setdefault('FINANCE_DATA_DIR','/tmp/oldpig-daily-sync')
os.environ.setdefault('FINANCE_CLOUD_TIMEOUT','20')
from app.daily_sync import main
