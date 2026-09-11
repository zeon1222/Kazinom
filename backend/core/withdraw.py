from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import get_db
from core.balance_service import try_deduct_balance, adjust_balance, raise_if_banned
from core.notify import send_telegram_message
from config import MIN_WITHDRAWAL_QEPIK, ADMIN_CHAT_ID, qepik_to_azn

router = APIRouter()


class WithdrawRequest(BaseModel):
    telegram_id: str
    amount_qepik: int = Field(gt=0)
    card_number: str


@router.post("/request")
def request_withdrawal(req: WithdrawRequest):
    raise_if_banned(req.telegram_id)
    if req.amount_qepik < MIN_WITHDRAWAL_QEPIK:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum çıxarış {qepik_to_azn(MIN_WITHDRAWAL_QEPIK)} AZN-dir.",
        )

    new_balance = try_deduct_balance(req.telegram_id, req.amount_qepik)
    if new_balance is None:
        raise HTTPException(status_code=400, detail="Balans kifayət etmir.")

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO withdrawals (telegram_id, amount_qepik, payout_note, status)
               VALUES (%s, %s, %s, 'pending') RETURNING id""",
            (req.telegram_id, req.amount_qepik, req.card_number),
        )
        request_id = cur.fetchone()["id"]

    amount_azn = qepik_to_azn(req.amount_qepik)
    send_telegram_message(
        ADMIN_CHAT_ID,
        f"🔔 Yeni çıxarış tələbi #{request_id}\n"
        f"İstifadəçi: {req.telegram_id}\n"
        f"Məbləğ: {amount_azn} AZN\n"
        f"Kart: {req.card_number}\n\n"
        f"Ödənişi etdikdən sonra təsdiqlə: /confirm {request_id}",
        reply_markup={
            "inline_keyboard": [[
                {"text": "✅ Təsdiqlə", "callback_data": f"confirm_{request_id}"},
                {"text": "❌ Rədd et", "callback_data": f"reject_{request_id}"},
            ]]
        },
    )
    return {
        "request_id": request_id,
        "status": "pending",
        "new_balance_azn": qepik_to_azn(new_balance),
    }


@router.get("/{telegram_id}")
def list_withdrawals(telegram_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM withdrawals WHERE telegram_id = %s ORDER BY id DESC",
            (telegram_id,),
        )
        return cur.fetchall()
