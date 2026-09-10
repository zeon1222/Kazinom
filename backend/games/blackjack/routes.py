from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

from games.blackjack import engine
from core.balance_service import get_or_create_user
from config import qepik_to_azn

router = APIRouter()


class StartRequest(BaseModel):
    telegram_id: str
    spot_bets_qepik: List[int]
    side_bets_qepik: List[int] = []


class ActionRequest(BaseModel):
    telegram_id: str


def _format_card(c):
    return f"{c[0]}{c[1]}"


def _format_hands(hands):
    out = []
    for h in hands:
        out.append({
            "spot_index": h["spot_index"],
            "cards": [_format_card(c) for c in h["cards"]],
            "total": engine.hand_value(h["cards"]),
            "status": h["status"],
            "can_split": h.get("can_split", False),
            "can_double": h.get("can_double", False),
            "bet_azn": qepik_to_azn(h["bet_qepik"]),
            "side_bet_azn": qepik_to_azn(h.get("side_bet_qepik", 0)),
            "payout_azn": qepik_to_azn(h["payout_qepik"]) if "payout_qepik" in h else None,
        })
    return out


def _format_response(result):
    if result["finished"]:
        return {
            "finished": True,
            "hands": _format_hands(result["hands"]),
            "dealer_hand": [_format_card(c) for c in result["dealer_hand"]],
            "dealer_total": result["dealer_total"],
            "dealer_busted": result["dealer_busted"],
            "dealer_blackjack": result["dealer_blackjack"],
            "total_bet_azn": qepik_to_azn(result["total_bet_qepik"]),
            "total_payout_azn": qepik_to_azn(result["total_payout_qepik"]),
            "new_balance_azn": qepik_to_azn(result["new_balance_qepik"]),
        }
    dealer_upcard = result["dealer_upcard"]
    dealer_display = _format_card(dealer_upcard) if isinstance(dealer_upcard[0], str) else [_format_card(c) for c in dealer_upcard]
    return {
        "finished": False,
        "hands": _format_hands(result["hands"]),
        "active_hand_index": result["active_hand_index"],
        "dealer_upcard": dealer_display,
    }


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
        "max_spots": engine.MAX_SPOTS,
    }


@router.post("/start")
def start(req: StartRequest):
    result = engine.start_round(req.telegram_id, req.spot_bets_qepik, req.side_bets_qepik)
    return _format_response(result)


@router.post("/hit")
def action_hit(req: ActionRequest):
    result = engine.hit(req.telegram_id)
    return _format_response(result)


@router.post("/stand")
def action_stand(req: ActionRequest):
    result = engine.stand(req.telegram_id)
    return _format_response(result)


@router.post("/double")
def action_double(req: ActionRequest):
    result = engine.double_down(req.telegram_id)
    return _format_response(result)


@router.post("/split")
def action_split(req: ActionRequest):
    result = engine.split(req.telegram_id)
    return _format_response(result)
