import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://kazinom.example.com")

MIN_DEPOSIT_QEPIK = 1000      # 10 AZN
MIN_WITHDRAWAL_QEPIK = 1000   # 10 AZN
STARTING_BALANCE_QEPIK = 200  # 2 AZN

def qepik_to_azn(q: int) -> float:
    return round(q / 100, 2)

def azn_to_qepik(a: float) -> int:
    return round(a * 100)
