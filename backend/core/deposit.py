from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import get_db
from core.balance_service import get_or_create_user
from core.notify import send_telegram_message
from config import MIN_DEPOSIT_QEPIK, ADMIN_CHAT_ID, qepik_to_azn

router = APIRouter()


class DepositRequest(BaseModel):
    telegram_id: str
    amount_qepik: int = Field(gt=0)
    receipt_note: Optional[str] = None  # dekont linki / qeyd


@router.post("/request")
def request_deposit(req: DepositRequest):
    if req.amount_qepik < MIN_DEPOSIT_QEPIK:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum depozit {qepik_to_azn(MIN_DEPOSIT_QEPIK)} AZN-dir.",
        )
    get_or_create_user(req.telegram_id)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO deposits (telegram_id, amount_qepik, receipt_note, status)
               VALUES (%s, %s, %s, 'pending') RETURNING id, requested_at""",
            (req.telegram_id, req.amount_qepik, req.receipt_note),
        )
        row = cur.fetchone()
        request_id = row["id"]

    amount_azn = qepik_to_azn(req.amount_qepik)
    send_telegram_message(
        ADMIN_CHAT_ID,
        f"💰 Yeni depozit tələbi #{request_id}\n"
        f"İstifadəçi: {req.telegram_id}\n"
        f"Məbləğ: {amount_azn} AZN\n"
        f"Qeyd: {req.receipt_note or '-'}\n\n"
        f"Ödənişi aldıqdan sonra təsdiqlə: /dconfirm {request_id}",
        reply_markup={
            "inline_keyboard": [[
                {"text": "✅ Təsdiqlə", "callback_data": f"dconfirm_{request_id}"},
                {"text": "❌ Rədd et", "callback_data": f"dreject_{request_id}"},
            ]]
        },
    )
    return {
        "request_id": request_id,
        "status": "pending",
        "note": "Depozit tələbiniz qeydə alındı, ödəniş təsdiqləndikdən sonra balansınıza əlavə olunacaq.",
    }


@router.get("/{telegram_id}")
def list_deposits(telegram_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM deposits WHERE telegram_id = %s ORDER BY id DESC",
            (telegram_id,),
        )
        return cur.fetchall()
