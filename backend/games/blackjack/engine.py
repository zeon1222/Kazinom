import hashlib
import hmac
import secrets
import json
from datetime import datetime, timezone

from database import get_db

SUITS = ["S", "H", "D", "C"]
RANKS = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
NUM_DECKS = 6

MIN_BET_QEPIK = 50
MAX_BET_QEPIK = 50000
MAX_SPOTS = 3
BLACKJACK_PAYOUT_MULT = 1.5
SIDE_BET_PAIR_PAYOUT = 11


def init_game_tables():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS blackjack_seeds (
                telegram_id TEXT PRIMARY KEY,
                server_seed TEXT NOT NULL,
                server_seed_hash TEXT NOT NULL,
                client_seed TEXT NOT NULL,
                nonce INTEGER NOT NULL DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS blackjack_active_rounds (
                telegram_id TEXT PRIMARY KEY,
                shoe_json TEXT NOT NULL,
                draw_index INTEGER NOT NULL,
                dealer_hand_json TEXT NOT NULL,
                hands_json TEXT NOT NULL,
                active_hand_index INTEGER NOT NULL,
                side_bets_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress',
                server_seed_hash TEXT NOT NULL,
                client_seed TEXT NOT NULL,
                nonce INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS blackjack_history (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                hands_json TEXT NOT NULL,
                dealer_hand_json TEXT NOT NULL,
                total_bet_qepik INTEGER NOT NULL,
                total_payout_qepik INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)


def new_server_seed_pair():
    seed = secrets.token_hex(32)
    seed_hash = hashlib.sha256(seed.encode()).hexdigest()
    return seed, seed_hash


def get_or_create_seed(telegram_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM blackjack_seeds WHERE telegram_id = %s", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row
        seed, seed_hash = new_server_seed_pair()
        client_seed = secrets.token_hex(8)
        cur.execute(
            """INSERT INTO blackjack_seeds (telegram_id, server_seed, server_seed_hash, client_seed, nonce)
               VALUES (%s, %s, %s, %s, 0) RETURNING *""",
            (telegram_id, seed, seed_hash, client_seed),
        )
        return cur.fetchone()


def build_shoe(server_seed: str, client_seed: str, nonce: int):
    deck = [[r, s] for s in SUITS for r in RANKS] * NUM_DECKS
    msg = f"{client_seed}:{nonce}".encode()
    digest_stream = b""
    counter = 0
    needed = len(deck) * 4 + 4
    while len(digest_stream) < needed:
        digest_stream += hmac.new(server_seed.encode(), msg + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1

    n = len(deck)
    for i in range(n - 1, 0, -1):
        offset = (i * 4) % (len(digest_stream) - 4)
        chunk = digest_stream[offset: offset + 4]
        rand_val = int.from_bytes(chunk, "big")
        j = rand_val % (i + 1)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def card_value(rank: str) -> int:
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_value(cards):
    total = sum(card_value(c[0]) for c in cards)
    aces = sum(1 for c in cards if c[0] == "A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def is_blackjack(cards):
    return len(cards) == 2 and hand_value(cards) == 21


def is_soft_hand(cards):
    total = sum(card_value(c[0]) for c in cards)
    aces = sum(1 for c in cards if c[0] == "A")
    reduced = 0
    t = total
    while t > 21 and aces > reduced:
        t -= 10
        reduced += 1
    return reduced < aces


def dealer_should_hit(dealer_hand):
    total = hand_value(dealer_hand)
    if total < 17:
        return True
    if total == 17 and is_soft_hand(dealer_hand):
        return True
    return False


def _load_round(telegram_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM blackjack_active_rounds WHERE telegram_id = %s", (telegram_id,))
        return cur.fetchone()


def _save_new_round(telegram_id, shoe, draw_index, dealer_hand, hands, active_hand_index, side_bets, status, seed_row, nonce):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO blackjack_active_rounds
               (telegram_id, shoe_json, draw_index, dealer_hand_json, hands_json,
                active_hand_index, side_bets_json, status, server_seed_hash, client_seed, nonce)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (telegram_id, json.dumps(shoe), draw_index, json.dumps(dealer_hand), json.dumps(hands),
             active_hand_index, json.dumps(side_bets), status, seed_row["server_seed_hash"],
             seed_row["client_seed"], nonce),
        )


def _update_round(telegram_id, **fields):
    set_clauses = []
    values = []
    field_map = {
        "hands": ("hands_json", lambda v: json.dumps(v)),
        "draw_index": ("draw_index", lambda v: v),
        "active_hand_index": ("active_hand_index", lambda v: v),
        "status": ("status", lambda v: v),
    }
    for key, val in fields.items():
        col, transform = field_map[key]
        set_clauses.append(f"{col} = %s")
        values.append(transform(val))
    values.append(telegram_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE blackjack_active_rounds SET {', '.join(set_clauses)} WHERE telegram_id = %s",
            values,
        )


def _clear_round(telegram_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM blackjack_active_rounds WHERE telegram_id = %s", (telegram_id,))


def start_round(telegram_id: str, spot_bets: list, side_bets: list):
    from core.balance_service import try_deduct_balance, adjust_balance, raise_if_banned
    from fastapi import HTTPException

    raise_if_banned(telegram_id)

    existing = _load_round(telegram_id)
    if existing and existing["status"] == "in_progress":
        raise HTTPException(status_code=400, detail="Aktiv əl var, əvvəlcə onu bitirin.")

    if len(spot_bets) == 0 or len(spot_bets) > MAX_SPOTS:
        raise HTTPException(status_code=400, detail="1-3 arası spot seçilməlidir.")
    active_spots = [b for b in spot_bets if b != 0]
    if not active_spots:
        raise HTTPException(status_code=400, detail="Ən az bir spota mərc qoyulmalıdır.")
    for b in active_spots:
        if b < MIN_BET_QEPIK or b > MAX_BET_QEPIK:
            raise HTTPException(status_code=400, detail="Mərc həddi aşıldı.")

    side_bets = list(side_bets) if side_bets else [0] * len(spot_bets)
    while len(side_bets) < len(spot_bets):
        side_bets.append(0)

    total_bet = sum(spot_bets) + sum(side_bets)

    balance_after = try_deduct_balance(telegram_id, total_bet)
    if balance_after is None:
        raise HTTPException(status_code=400, detail="Balans kifayət etmir.")

    seed_row = get_or_create_seed(telegram_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE blackjack_seeds SET nonce = nonce + 1 WHERE telegram_id = %s RETURNING nonce",
            (telegram_id,),
        )
        used_nonce = cur.fetchone()["nonce"] - 1

    shoe = build_shoe(seed_row["server_seed"], seed_row["client_seed"], used_nonce)
    draw_index = 0

    def draw():
        nonlocal draw_index
        c = shoe[draw_index]
        draw_index += 1
        return c

    dealer_hand = [draw(), draw()]

    hands = []
    for i, bet in enumerate(spot_bets):
        if bet == 0:
            continue
        cards = [draw(), draw()]
        side_bet = side_bets[i] if i < len(side_bets) else 0
        side_win = 0
        if side_bet > 0 and cards[0][0] == cards[1][0]:
            side_win = side_bet * SIDE_BET_PAIR_PAYOUT
        hands.append({
            "spot_index": i,
            "cards": cards,
            "bet_qepik": bet,
            "side_bet_qepik": side_bet,
            "side_win_qepik": side_win,
            "status": "playing",
            "split_from": None,
            "can_split": (cards[0][0] == cards[1][0]),
            "can_double": True,
        })

    dealer_bj = is_blackjack(dealer_hand)
    for h in hands:
        if is_blackjack(h["cards"]):
            h["status"] = "blackjack"
            h["can_split"] = False
            h["can_double"] = False

    all_resolved = dealer_bj or all(h["status"] == "blackjack" for h in hands)

    if all_resolved:
        return _finish_round(telegram_id, shoe, draw_index, dealer_hand, hands, seed_row, used_nonce)

    _save_new_round(telegram_id, shoe, draw_index, dealer_hand, hands, 0, side_bets, "in_progress", seed_row, used_nonce)
    return _state_response(dealer_hand, hands, 0, hide_dealer=True)


def _first_playable_index(hands, start=0):
    for i in range(start, len(hands)):
        if hands[i]["status"] == "playing":
            return i
    return None


def _advance_or_finish(telegram_id):
    row = _load_round(telegram_id)
    shoe = json.loads(row["shoe_json"])
    draw_index = row["draw_index"]
    dealer_hand = json.loads(row["dealer_hand_json"])
    hands = json.loads(row["hands_json"])
    active_idx = row["active_hand_index"]

    next_idx = _first_playable_index(hands, active_idx)
    if next_idx is not None:
        _update_round(telegram_id, hands=hands, active_hand_index=next_idx)
        return _state_response(dealer_hand, hands, next_idx, hide_dealer=True)

    seed_row = get_or_create_seed(telegram_id)
    return _finish_round(telegram_id, shoe, draw_index, dealer_hand, hands, seed_row, row["nonce"])


def _finish_round(telegram_id, shoe, draw_index, dealer_hand, hands, seed_row, nonce):
    from core.balance_service import adjust_balance

    def draw():
        nonlocal draw_index
        c = shoe[draw_index]
        draw_index += 1
        return c

    any_live = any(h["status"] != "busted" for h in hands)
    if any_live:
        while dealer_should_hit(dealer_hand):
            dealer_hand.append(draw())

    dealer_total = hand_value(dealer_hand)
    dealer_busted = dealer_total > 21
    dealer_bj = is_blackjack(dealer_hand)

    total_bet = 0
    total_payout = 0
    for h in hands:
        bet = h["bet_qepik"]
        total_bet += bet + h["side_bet_qepik"]
        payout = h["side_win_qepik"]

        if h["status"] == "busted":
            pass
        elif h["status"] == "blackjack":
            if dealer_bj:
                payout += bet
            else:
                payout += bet + round(bet * BLACKJACK_PAYOUT_MULT)
        else:
            player_total = hand_value(h["cards"])
            if dealer_busted:
                payout += bet * 2
            elif player_total > dealer_total:
                payout += bet * 2
            elif player_total == dealer_total:
                payout += bet

        h["payout_qepik"] = payout
        total_payout += payout

    new_balance = adjust_balance(telegram_id, total_payout)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO blackjack_history (telegram_id, hands_json, dealer_hand_json, total_bet_qepik, total_payout_qepik)
               VALUES (%s,%s,%s,%s,%s)""",
            (telegram_id, json.dumps(hands), json.dumps(dealer_hand), total_bet, total_payout),
        )

    _clear_round(telegram_id)

    return {
        "finished": True,
        "hands": hands,
        "dealer_hand": dealer_hand,
        "dealer_total": dealer_total,
        "dealer_busted": dealer_busted,
        "dealer_blackjack": dealer_bj,
        "total_bet_qepik": total_bet,
        "total_payout_qepik": total_payout,
        "new_balance_qepik": new_balance,
    }


def _state_response(dealer_hand, hands, active_idx, hide_dealer):
    return {
        "finished": False,
        "hands": hands,
        "active_hand_index": active_idx,
        "dealer_upcard": dealer_hand[0] if hide_dealer else dealer_hand,
    }


def _require_active_round(telegram_id):
    from fastapi import HTTPException
    row = _load_round(telegram_id)
    if not row or row["status"] != "in_progress":
        raise HTTPException(status_code=400, detail="Aktiv əl tapılmadı.")
    return row


def hit(telegram_id: str):
    from fastapi import HTTPException
    row = _require_active_round(telegram_id)

    shoe = json.loads(row["shoe_json"])
    draw_index = row["draw_index"]
    hands = json.loads(row["hands_json"])
    idx = row["active_hand_index"]
    h = hands[idx]
    if h["status"] != "playing":
        raise HTTPException(status_code=400, detail="Bu əl artıq bitib.")

    card = shoe[draw_index]
    draw_index += 1
    h["cards"].append(card)
    h["can_double"] = False
    h["can_split"] = False
    total = hand_value(h["cards"])
    if total > 21:
        h["status"] = "busted"
    elif total == 21:
        h["status"] = "stood"

    _update_round(telegram_id, hands=hands, draw_index=draw_index)

    if h["status"] != "playing":
        return _advance_or_finish(telegram_id)
    dealer_hand = json.loads(row["dealer_hand_json"])
    return _state_response(dealer_hand, hands, idx, hide_dealer=True)


def stand(telegram_id: str):
    row = _require_active_round(telegram_id)
    hands = json.loads(row["hands_json"])
    idx = row["active_hand_index"]
    h = hands[idx]
    if h["status"] != "playing":
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Bu əl artıq bitib.")
    h["status"] = "stood"
    _update_round(telegram_id, hands=hands)
    return _advance_or_finish(telegram_id)


def double_down(telegram_id: str):
    from fastapi import HTTPException
    from core.balance_service import try_deduct_balance

    row = _require_active_round(telegram_id)
    shoe = json.loads(row["shoe_json"])
    draw_index = row["draw_index"]
    hands = json.loads(row["hands_json"])
    idx = row["active_hand_index"]
    h = hands[idx]
    if h["status"] != "playing" or not h["can_double"]:
        raise HTTPException(status_code=400, detail="Double down mümkün deyil.")

    balance_after = try_deduct_balance(telegram_id, h["bet_qepik"])
    if balance_after is None:
        raise HTTPException(status_code=400, detail="Balans kifayət etmir.")

    h["bet_qepik"] *= 2
    card = shoe[draw_index]
    draw_index += 1
    h["cards"].append(card)
    h["can_double"] = False
    h["can_split"] = False
    total = hand_value(h["cards"])
    h["status"] = "busted" if total > 21 else "stood"

    _update_round(telegram_id, hands=hands, draw_index=draw_index)
    return _advance_or_finish(telegram_id)


def split(telegram_id: str):
    from fastapi import HTTPException
    from core.balance_service import try_deduct_balance

    row = _require_active_round(telegram_id)
    shoe = json.loads(row["shoe_json"])
    draw_index = row["draw_index"]
    hands = json.loads(row["hands_json"])
    idx = row["active_hand_index"]
    h = hands[idx]

    if h["status"] != "playing" or not h["can_split"]:
        raise HTTPException(status_code=400, detail="Split mümkün deyil.")
    if h["split_from"] is not None:
        raise HTTPException(status_code=400, detail="Bu əl artıq split edilib.")
    if len(hands) >= MAX_SPOTS + 1:
        raise HTTPException(status_code=400, detail="Maksimum əl sayına çatılıb.")

    balance_after = try_deduct_balance(telegram_id, h["bet_qepik"])
    if balance_after is None:
        raise HTTPException(status_code=400, detail="Balans kifayət etmir.")

    card1, card2 = h["cards"]
    new_card_a = shoe[draw_index]; draw_index += 1
    new_card_b = shoe[draw_index]; draw_index += 1

    h["cards"] = [card1, new_card_a]
    h["can_split"] = False
    h["can_double"] = True
    if hand_value(h["cards"]) == 21:
        h["status"] = "stood"
        h["can_double"] = False

    new_hand = {
        "spot_index": h["spot_index"],
        "cards": [card2, new_card_b],
        "bet_qepik": h["bet_qepik"],
        "side_bet_qepik": 0,
        "side_win_qepik": 0,
        "status": "playing",
        "split_from": idx,
        "can_split": False,
        "can_double": True,
    }
    if hand_value(new_hand["cards"]) == 21:
        new_hand["status"] = "stood"
        new_hand["can_double"] = False

    hands.insert(idx + 1, new_hand)

    _update_round(telegram_id, hands=hands, draw_index=draw_index)

    if h["status"] != "playing":
        return _advance_or_finish(telegram_id)
    dealer_hand = json.loads(row["dealer_hand_json"])
    return _state_response(dealer_hand, hands, idx, hide_dealer=True)
