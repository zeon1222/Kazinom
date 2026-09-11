from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import get_db
from core.balance_service import get_or_create_user, adjust_balance
from core.admin_auth import require_admin
from core.notify import send_telegram_message
from config import qepik_to_azn, azn_to_qepik

router = APIRouter()


class AddBalanceRequest(BaseModel):
    admin_key: str
    telegram_id: str
    amount_azn: float
    note: Optional[str] = None


@router.post("/add-balance")
def add_balance(req: AddBalanceRequest):
    require_admin(req.admin_key)
    get_or_create_user(req.telegram_id)
    delta = azn_to_qepik(req.amount_azn)
    new_balance = adjust_balance(req.telegram_id, delta)
    send_telegram_message(
        req.telegram_id,
        f"💰 Balansınıza {req.amount_azn:.2f} AZN {'əlavə olundu' if req.amount_azn > 0 else 'çıxıldı'}."
        + (f"\nQeyd: {req.note}" if req.note else "")
        + f"\nYeni balans: {qepik_to_azn(new_balance):.2f} AZN",
    )
    return {"telegram_id": req.telegram_id, "new_balance_azn": qepik_to_azn(new_balance)}


@router.get("/pending")
def pending_requests(admin_key: str):
    require_admin(admin_key)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY id ASC")
        withdrawals = cur.fetchall()
        cur.execute("SELECT * FROM deposits WHERE status = 'pending' ORDER BY id ASC")
        deposits = cur.fetchall()
    return {"withdrawals": withdrawals, "deposits": deposits}


class RequestAction(BaseModel):
    admin_key: str
    request_id: int
    kind: str  # "withdrawal" | "deposit"


@router.post("/confirm")
def confirm_request(req: RequestAction):
    require_admin(req.admin_key)
    if req.kind not in ("withdrawal", "deposit"):
        raise HTTPException(status_code=400, detail="Yanlış kind.")
    table = "withdrawals" if req.kind == "withdrawal" else "deposits"

    # Atomik: yalnız hələ 'pending' olan sətri 'completed'ə çevirir.
    # Admin eyni tələbi iki dəfə təsdiqləsə, ikinci çağırış heç bir sətr
    # tapmayacaq və xəta qaytaracaq (çift işlem qorunması).
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""UPDATE {table} SET status = 'completed', completed_at = now()
                WHERE id = %s AND status = 'pending' RETURNING *""",
            (req.request_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="Tələb tapılmadı və ya artıq işlənib.")

    amount_azn = qepik_to_azn(row["amount_qepik"])
    if req.kind == "deposit":
        adjust_balance(row["telegram_id"], row["amount_qepik"])
        send_telegram_message(row["telegram_id"], f"✅ Depozitiniz təsdiqləndi! {amount_azn:.2f} AZN balansınıza əlavə olundu.")
    else:
        send_telegram_message(row["telegram_id"], f"✅ Ödənişiniz təsdiqləndi! {amount_azn:.2f} AZN göndərildi.")

    return {"request_id": req.request_id, "status": "completed"}


@router.post("/reject")
def reject_request(req: RequestAction):
    require_admin(req.admin_key)
    if req.kind not in ("withdrawal", "deposit"):
        raise HTTPException(status_code=400, detail="Yanlış kind.")
    table = "withdrawals" if req.kind == "withdrawal" else "deposits"

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""UPDATE {table} SET status = 'rejected', completed_at = now()
                WHERE id = %s AND status = 'pending' RETURNING *""",
            (req.request_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="Tələb tapılmadı və ya artıq işlənib.")

    amount_azn = qepik_to_azn(row["amount_qepik"])
    if req.kind == "withdrawal":
        adjust_balance(row["telegram_id"], row["amount_qepik"])  # geri qaytar
        send_telegram_message(row["telegram_id"], f"❌ Çıxarış tələbiniz rədd edildi. {amount_azn:.2f} AZN balansınıza geri qaytarıldı.")
    else:
        send_telegram_message(row["telegram_id"], "❌ Depozit tələbiniz rədd edildi.")

    return {"request_id": req.request_id, "status": "rejected"}


@router.get("/stats")
def stats(admin_key: str):
    require_admin(admin_key)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) c FROM users")
        users_count = cur.fetchone()["c"]
        cur.execute("SELECT COALESCE(SUM(balance_qepik),0) s FROM users")
        outstanding = cur.fetchone()["s"]
        cur.execute("SELECT COUNT(*) c, COALESCE(SUM(amount_qepik),0) s FROM withdrawals WHERE status='pending'")
        pending_w = cur.fetchone()
        cur.execute("SELECT COUNT(*) c, COALESCE(SUM(amount_qepik),0) s FROM deposits WHERE status='pending'")
        pending_d = cur.fetchone()
        cur.execute("SELECT COUNT(*) c, COALESCE(SUM(amount_qepik),0) s FROM deposits WHERE status='completed'")
        completed_d = cur.fetchone()
        cur.execute("SELECT COUNT(*) c, COALESCE(SUM(amount_qepik),0) s FROM withdrawals WHERE status='completed'")
        completed_w = cur.fetchone()

    return {
        "users_count": users_count,
        "outstanding_balance_azn": qepik_to_azn(outstanding),
        "pending_withdrawals": {"count": pending_w["c"], "amount_azn": qepik_to_azn(pending_w["s"])},
        "pending_deposits": {"count": pending_d["c"], "amount_azn": qepik_to_azn(pending_d["s"])},
        "completed_deposits": {"count": completed_d["c"], "amount_azn": qepik_to_azn(completed_d["s"])},
        "completed_withdrawals": {"count": completed_w["c"], "amount_azn": qepik_to_azn(completed_w["s"])},
    }


@router.get("/user/{telegram_id}")
def user_detail(telegram_id: str, admin_key: str):
    require_admin(admin_key)
    user = get_or_create_user(telegram_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM deposits WHERE telegram_id = %s ORDER BY id DESC LIMIT 10", (telegram_id,))
        deposits = cur.fetchall()
        cur.execute("SELECT * FROM withdrawals WHERE telegram_id = %s ORDER BY id DESC LIMIT 10", (telegram_id,))
        withdrawals = cur.fetchall()
    return {
        "telegram_id": telegram_id,
        "balance_azn": qepik_to_azn(user["balance_qepik"]),
        "created_at": user["created_at"],
        "recent_deposits": deposits,
        "recent_withdrawals": withdrawals,
    }


class BanRequest(BaseModel):
    admin_key: str
    telegram_id: str
    banned: bool


@router.post("/ban")
def ban_user(req: BanRequest):
    require_admin(req.admin_key)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT false")
        cur.execute("UPDATE users SET is_banned = %s WHERE telegram_id = %s", (req.banned, req.telegram_id))
    status_text = "bloklandı 🚫" if req.banned else "blokdan çıxarıldı ✅"
    send_telegram_message(req.telegram_id, f"Hesabınız {status_text}.")
    return {"telegram_id": req.telegram_id, "banned": req.banned}


class BroadcastRequest(BaseModel):
    admin_key: str
    message: str


@router.post("/broadcast")
def broadcast(req: BroadcastRequest):
    require_admin(req.admin_key)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM users")
        users = cur.fetchall()
    for u in users:
        send_telegram_message(u["telegram_id"], f"📢 {req.message}")
    return {"sent_to": len(users)}
