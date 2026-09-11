"""
Kazinom Telegram Bot
---------------------
1) İstifadəçilər üçün: /start -> Mini App-ı açır (oyun seçimi ekranı orada olur)
2) Admin üçün: 8 idarəetmə əmri.

Bu fayl Termux-da ayrıca sessiyada işə salınır:
    python3 bot.py

Bot HEÇ BİR real ödəniş GÖNDƏRMİR. Depozit/çıxarış təsdiqi sadəcə
"mən bu ödənişi əl ilə etdim/aldım" işarəsidir - real bank/kart
köçürməsini sən özün, botdan kənarda edirsən.
"""

import time
import os
import sys
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://kazinom.example.com")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def tg_call(method, **params):
    r = requests.post(f"{TG_API}/{method}", json=params, timeout=30)
    return r.json()


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", **payload)


def answer_callback(callback_id, text=""):
    return tg_call("answerCallbackQuery", callback_query_id=callback_id, text=text)


def backend_get(path, params=None):
    params = dict(params or {})
    params["admin_key"] = ADMIN_API_KEY
    r = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=10)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"detail": r.text}


def backend_post(path, body):
    body = dict(body)
    body["admin_key"] = ADMIN_API_KEY
    r = requests.post(f"{BACKEND_URL}{path}", json=body, timeout=10)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"detail": r.text}


def is_admin(user_id):
    return ADMIN_CHAT_ID and int(user_id) == int(ADMIN_CHAT_ID)


def handle_start(chat_id):
    send_message(
        chat_id,
        "Kazinom-a xoş gəldin!\nAşağıdaki düymə ilə oyunları aç.",
        reply_markup={
            "inline_keyboard": [[
                {"text": "Oyna", "web_app": {"url": FRONTEND_URL}}
            ]]
        },
    )


def handle_help(chat_id):
    send_message(
        chat_id,
        "Admin əmrləri:\n"
        "/balans <telegram_id> <məbləğ> [qeyd] - balans əlavə/çıxar\n"
        "/pending - gözləyən depozit və çıxarış tələbləri\n"
        "/confirm <id> <deposit|withdrawal> - təsdiqlə\n"
        "/reject <id> <deposit|withdrawal> - rədd et\n"
        "/stats - ümumi biznes statistikası\n"
        "/user <telegram_id> - istifadəçi detalı\n"
        "/ban <telegram_id> - hesabı blokla\n"
        "/unban <telegram_id> - blok qaldır\n"
        "/broadcast <mesaj> - bütün istifadəçilərə mesaj",
    )


def handle_balans(chat_id, args):
    if len(args) < 2:
        send_message(chat_id, "İstifadə: /balans <telegram_id> <məbləğ> [qeyd]")
        return
    target_id, amount_str = args[0], args[1]
    note = " ".join(args[2:]) if len(args) > 2 else None
    try:
        amount = float(amount_str)
    except ValueError:
        send_message(chat_id, "Məbləğ düzgün deyil.")
        return

    status, data = backend_post("/api/admin/add-balance", {
        "telegram_id": target_id,
        "amount_azn": amount,
        "note": note,
    })
    if status == 200:
        send_message(
            chat_id,
            f"{target_id} istifadəçisinə {amount:.2f} AZN əlavə olundu.\n"
            f"Yeni balans: {data['new_balance_azn']:.2f} AZN",
        )
    else:
        send_message(chat_id, f"Xəta: {data.get('detail', 'naməlum')}")


def handle_pending(chat_id):
    status, data = backend_get("/api/admin/pending")
    if status != 200:
        send_message(chat_id, f"Xəta: {data.get('detail', 'naməlum')}")
        return

    withdrawals = data.get("withdrawals", [])
    deposits = data.get("deposits", [])

    if not withdrawals and not deposits:
        send_message(chat_id, "Gözləyən tələb yoxdur.")
        return

    for d in deposits:
        send_message(
            chat_id,
            f"DEPOZİT #{d['id']}\n"
            f"İstifadəçi: {d['telegram_id']}\n"
            f"Məbləğ: {d['amount_qepik']/100:.2f} AZN\n"
            f"Qeyd: {d.get('receipt_note') or '-'}\n"
            f"Tarix: {d['requested_at']}",
            reply_markup={
                "inline_keyboard": [[
                    {"text": "Təsdiqlə", "callback_data": f"dconfirm_{d['id']}"},
                    {"text": "Rədd et", "callback_data": f"dreject_{d['id']}"},
                ]]
            },
        )

    for w in withdrawals:
        send_message(
            chat_id,
            f"ÇIXARIŞ #{w['id']}\n"
            f"İstifadəçi: {w['telegram_id']}\n"
            f"Məbləğ: {w['amount_qepik']/100:.2f} AZN\n"
            f"Kart: {w.get('payout_note') or '-'}\n"
            f"Tarix: {w['requested_at']}",
            reply_markup={
                "inline_keyboard": [[
                    {"text": "Təsdiqlə", "callback_data": f"confirm_{w['id']}"},
                    {"text": "Rədd et", "callback_data": f"reject_{w['id']}"},
                ]]
            },
        )


def handle_confirm(chat_id, request_id, kind):
    status, data = backend_post("/api/admin/confirm", {
        "request_id": request_id,
        "kind": kind,
    })
    if status == 200:
        send_message(chat_id, f"Tələb #{request_id} ({kind}) təsdiqləndi.")
    else:
        send_message(chat_id, f"Xəta: {data.get('detail', 'naməlum')}")


def handle_reject(chat_id, request_id, kind):
    status, data = backend_post("/api/admin/reject", {
        "request_id": request_id,
        "kind": kind,
    })
    if status == 200:
        send_message(chat_id, f"Tələb #{request_id} ({kind}) rədd edildi.")
    else:
        send_message(chat_id, f"Xəta: {data.get('detail', 'naməlum')}")


def handle_stats(chat_id):
    status, data = backend_get("/api/admin/stats")
    if status != 200:
        send_message(chat_id, f"Xəta: {data.get('detail', 'naməlum')}")
        return
    send_message(
        chat_id,
        "Kazinom statistikası\n\n"
        f"İstifadəçi sayı: {data['users_count']}\n"
        f"İstifadəçilərdə qalan balans: {data['outstanding_balance_azn']:.2f} AZN\n\n"
        f"Gözləyən çıxarışlar: {data['pending_withdrawals']['count']} ədəd "
        f"({data['pending_withdrawals']['amount_azn']:.2f} AZN)\n"
        f"Gözləyən depozitlər: {data['pending_deposits']['count']} ədəd "
        f"({data['pending_deposits']['amount_azn']:.2f} AZN)\n\n"
        f"Tamamlanmış çıxarışlar: {data['completed_withdrawals']['count']} ədəd "
        f"({data['completed_withdrawals']['amount_azn']:.2f} AZN)\n"
        f"Tamamlanmış depozitlər: {data['completed_deposits']['count']} ədəd "
        f"({data['completed_deposits']['amount_azn']:.2f} AZN)",
    )


def handle_user(chat_id, args):
    if len(args) < 1:
        send_message(chat_id, "İstifadə: /user <telegram_id>")
        return
    target_id = args[0]
    status, data = backend_get(f"/api/admin/user/{target_id}")
    if status != 200:
        send_message(chat_id, f"Xəta: {data.get('detail', 'naməlum')}")
        return
    send_message(
        chat_id,
        f"İstifadəçi: {data['telegram_id']}\n"
        f"Balans: {data['balance_azn']:.2f} AZN\n"
        f"Qeydiyyat: {data['created_at']}\n"
        f"Son depozitlər: {len(data['recent_deposits'])} ədəd\n"
        f"Son çıxarışlar: {len(data['recent_withdrawals'])} ədəd",
    )


def handle_ban(chat_id, args, banned: bool):
    if len(args) < 1:
        send_message(chat_id, f"İstifadə: /{'ban' if banned else 'unban'} <telegram_id>")
        return
    target_id = args[0]
    status, data = backend_post("/api/admin/ban", {
        "telegram_id": target_id,
        "banned": banned,
    })
    if status == 200:
        send_message(chat_id, f"{target_id} {'bloklandı' if banned else 'blokdan çıxarıldı'}.")
    else:
        send_message(chat_id, f"Xəta: {data.get('detail', 'naməlum')}")


def handle_broadcast(chat_id, args):
    if not args:
        send_message(chat_id, "İstifadə: /broadcast <mesaj>")
        return
    message = " ".join(args)
    status, data = backend_post("/api/admin/broadcast", {"message": message})
    if status == 200:
        send_message(chat_id, f"Mesaj {data['sent_to']} istifadəçiyə göndərildi.")
    else:
        send_message(chat_id, f"Xəta: {data.get('detail', 'naməlum')}")


def _parse_confirm_reject_args(args, chat_id, action_name):
    if len(args) < 2 or args[1] not in ("deposit", "withdrawal"):
        send_message(chat_id, f"İstifadə: /{action_name} <id> <deposit|withdrawal>")
        return None, None
    try:
        request_id = int(args[0])
    except ValueError:
        send_message(chat_id, "ID rəqəm olmalıdır.")
        return None, None
    return request_id, args[1]


def handle_text_message(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = (msg.get("text") or "").strip()

    if text.startswith("/start"):
        handle_start(chat_id)
        return

    if not is_admin(user_id):
        if text.startswith("/"):
            send_message(chat_id, "Bu əmr yalnız admin üçündür.")
        return

    parts = text.split()
    cmd = parts[0] if parts else ""
    args = parts[1:]

    if cmd == "/balans":
        handle_balans(chat_id, args)
    elif cmd == "/pending":
        handle_pending(chat_id)
    elif cmd == "/confirm":
        request_id, kind = _parse_confirm_reject_args(args, chat_id, "confirm")
        if request_id is not None:
            handle_confirm(chat_id, request_id, kind)
    elif cmd == "/reject":
        request_id, kind = _parse_confirm_reject_args(args, chat_id, "reject")
        if request_id is not None:
            handle_reject(chat_id, request_id, kind)
    elif cmd == "/stats":
        handle_stats(chat_id)
    elif cmd == "/user":
        handle_user(chat_id, args)
    elif cmd == "/ban":
        handle_ban(chat_id, args, banned=True)
    elif cmd == "/unban":
        handle_ban(chat_id, args, banned=False)
    elif cmd == "/broadcast":
        handle_broadcast(chat_id, args)
    elif cmd == "/help":
        handle_help(chat_id)


def handle_callback_query(cb):
    user_id = cb["from"]["id"]
    data = cb.get("data", "")
    callback_id = cb["id"]
    chat_id = cb["message"]["chat"]["id"]

    if not is_admin(user_id):
        answer_callback(callback_id, "Yalnız admin üçün.")
        return

    if data.startswith("dconfirm_"):
        request_id = int(data.split("_", 1)[1])
        handle_confirm(chat_id, request_id, "deposit")
        answer_callback(callback_id, "Təsdiqləndi")
    elif data.startswith("dreject_"):
        request_id = int(data.split("_", 1)[1])
        handle_reject(chat_id, request_id, "deposit")
        answer_callback(callback_id, "Rədd edildi")
    elif data.startswith("confirm_"):
        request_id = int(data.split("_", 1)[1])
        handle_confirm(chat_id, request_id, "withdrawal")
        answer_callback(callback_id, "Təsdiqləndi")
    elif data.startswith("reject_"):
        request_id = int(data.split("_", 1)[1])
        handle_reject(chat_id, request_id, "withdrawal")
        answer_callback(callback_id, "Rədd edildi")
    else:
        answer_callback(callback_id)


def main():
    print("Kazinom bot işə düşdü. Dayandırmaq üçün Ctrl+C.")
    offset = 0
    while True:
        try:
            r = requests.get(
                f"{TG_API}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=40,
            )
            updates = r.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_text_message(update["message"])
                elif "callback_query" in update:
                    handle_callback_query(update["callback_query"])
        except Exception as e:
            print("Xəta:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
