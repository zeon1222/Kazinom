from fastapi import APIRouter
from pydantic import BaseModel, Field

from games.slot import engine
from core.balance_service import get_or_create_user
from config import qepik_to_azn

router = APIRouter()


class SpinRequest(BaseModel):
    telegram_id: str
    bet_qepik: int = Field(gt=0)


@router.get("/user/{telegram_id}")
def get_user_info(telegram_id: str):
    user = get_or_create_user(telegram_id)
    seed_row = engine.get_or_create_seed(telegram_id)
    return {
        "telegram_id": telegram_id,
        "balance_azn": qepik_to_azn(user["balance_qepik"]),
        "server_seed_hash": seed_row["server_seed_hash"],
        "min_bet_azn": qepik_to_azn(engine.MIN_BET_QEPIK),
        "max_bet_azn": qepik_to_azn(engine.MAX_BET_QEPIK),
        "symbols": list(engine.SYMBOLS.keys()),
        "paylines": engine.PAYLINES,
    }


@router.post("/spin")
def spin(req: SpinRequest):
    result = engine.spin(req.telegram_id, req.bet_qepik)
    return {
        "grid": result["grid"],
        "winning_lines": [
            {
                "line_index": wl["line_index"],
                "positions": wl["positions"],
                "symbol": wl["symbol"],
                "payout_azn": qepik_to_azn(wl["payout_qepik"]),
            }
            for wl in result["winning_lines"]
        ],
        "total_payout_azn": qepik_to_azn(result["payout_qepik"]),
        "new_balance_azn": qepik_to_azn(result["new_balance_qepik"]),
    }


@router.get("/history/{telegram_id}")
def history(telegram_id: str):
    rows = engine.get_history(telegram_id)
    return [
        {
            "bet_azn": qepik_to_azn(r["bet_qepik"]),
            "grid": r["grid_json"],
            "payout_azn": qepik_to_azn(r["payout_qepik"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
