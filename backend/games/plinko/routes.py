from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Literal

from games.plinko import engine
from core.balance_service import get_or_create_user
from config import qepik_to_azn

router = APIRouter()

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


class BetRequest(BaseModel):
    telegram_id: str
    bet_qepik: int = Field(gt=0)
    risk: RiskLevel = engine.DEFAULT_RISK


class ClientSeedRequest(BaseModel):
    telegram_id: str
    client_seed: str


@router.get("/user/{telegram_id}")
def get_user_info(telegram_id: str):
    user = get_or_create_user(telegram_id)
    seed_row = engine.get_or_create_seed(telegram_id)
    return {
        "telegram_id": telegram_id,
        "balance_azn": qepik_to_azn(user["balance_qepik"]),
        "server_seed_hash": seed_row["server_seed_hash"],
        "client_seed": seed_row["client_seed"],
        "nonce": seed_row["nonce"],
        "risk_tables": engine.RISK_TABLES,
        "min_bet_azn": qepik_to_azn(engine.MIN_BET_QEPIK),
        "max_bet_azn": qepik_to_azn(engine.MAX_BET_QEPIK),
    }


@router.post("/bet")
def bet(req: BetRequest):
    result = engine.place_bet(req.telegram_id, req.bet_qepik, req.risk)
    return {
        "bin_index": result["bin_index"],
        "path": result["path"],
        "multiplier": result["multiplier"],
        "payout_azn": qepik_to_azn(result["payout_qepik"]),
        "new_balance_azn": qepik_to_azn(result["new_balance_qepik"]),
        "server_seed_hash": result["server_seed_hash"],
        "client_seed": result["client_seed"],
        "nonce": result["nonce"],
    }


@router.get("/history/{telegram_id}")
def history(telegram_id: str):
    rows = engine.get_history(telegram_id)
    return [
        {
            "risk": r["risk"],
            "bet_azn": qepik_to_azn(r["bet_qepik"]),
            "bin_index": r["bin_index"],
            "multiplier": r["multiplier"],
            "payout_azn": qepik_to_azn(r["payout_qepik"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.post("/seed/rotate")
def rotate_seed(req: ClientSeedRequest):
    return engine.rotate_seed(req.telegram_id, req.client_seed)
