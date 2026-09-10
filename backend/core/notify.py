import requests
from config import BOT_TOKEN


def send_telegram_message(chat_id, text, reply_markup=None):
    if not BOT_TOKEN or not chat_id:
        return
    try:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=5,
        )
    except Exception:
        pass
