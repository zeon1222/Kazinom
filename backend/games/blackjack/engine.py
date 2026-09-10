import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from database import get_db

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
NUM_DECKS = 6  # 6 desteli shoe, house-a bir az üstünlük verir (kart sayımını da çətinləşdirir)

MIN_BET_QEPIK = 50      # 0.50 AZN
MAX_BET_QEPIK = 50000   # 500 AZN
MAX_SPOTS = 3
BLACKJACK_PAYOUT_MULT = 1.5  # 3:2
SIDE_BET_PAIR_PAYOUT = 11    # eyni rütbədə cüt gəlsə 11:1 (Perfect Pairs tərzi, sadələşdirilmiş)


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
            CREATE TABLE IF NOT EXISTS blackjack_rounds (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                spots_json TEXT NOT NULL,
                dealer_hand_json TEXT NOT NULL,
                total_bet_qepik INTEGER NOT NULL,
                total_payout_qepik INTEGER NOT NULL,
                server_seed_hash TEXT NOT NULL,
                client_seed TEXT NOT NULL,
                nonce INTEGER NOT NULL,
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
    """HMAC əsaslı deterministik qarışdırma (provably-fair Fisher-Yates)."""
    deck = [(r, s) for s in SUITS for r in RANKS] * NUM_DECKS
    msg = f"{client_seed}:{nonce}".encode()
    digest_stream = b""
    counter = 0
    while len(digest_stream) < len(deck) * 4:
        digest_stream += hmac.new(server_seed.encode(), msg + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1

    n = len(deck)
    for i in range(n - 1, 0, -1):
        chunk = digest_stream[i * 4 % len(digest_stream): i * 4 % len(digest_stream) + 4]
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
    total = sum(card_value(r) for r, s in cards)
    aces = sum(1 for r, s in cards if r == "A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def is_blackjack(cards):
    return len(cards) == 2 and hand_value(cards) == 21


def play_round(telegram_id: str, spot_bets: list, side_bets: list):
    """
    spot_bets: [bet_qepik, bet_qepik, bet_qepik] (1-3 arası, 0 = oynamır)
    side_bets: eyni uzunluqda, hər spot üçün side bet miqdarı (0 = yox)
    Sadələşdirilmiş: hər spot üçün avtomatik strategiya YOX — hit/stand
    ayrı bir /action endpoint ilə idarə olunacaq (round içi state saxlanmır,
    v1-də hər spot tək əl olaraq avtomatik "dealer qaydası" ilə oynanır:
    16 və aşağı hit, 17+ stand). Double down üçün ayrı sadə flaq var.
    """
    from core.balance_service import get_or_create_user, adjust_balance
    from fastapi import HTTPException

    if len(spot_bets) == 0 or len(spot_bets) > MAX_SPOTS:
        raise HTTPException(status_code=400, detail="1-3 arası spot seçilməlidir.")

    total_bet = sum(spot_bets) + sum(side_bets)
    for b in spot_bets:
        if b != 0 and (b < MIN_BET_QEPIK or b > MAX_BET_QEPIK):
            raise HTTPException(status_code=400, detail="Mərc həddi aşıldı.")

    user = get_or_create_user(telegram_id)
    if user["balance_qepik"] < total_bet:
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
        card = shoe[draw_index]
        draw_index += 1
        return card

    spots_result = []
    dealer_hand = [draw(), draw()]

    for i, bet in enumerate(spot_bets):
        if bet == 0:
            spots_result.append(None)
            continue
        player_hand = [draw(), draw()]

        side_win = 0
        side_bet = side_bets[i] if i < len(side_bets) else 0
        if side_bet > 0 and player_hand[0][0] == player_hand[1][0]:
            side_win = side_bet * SIDE_BET_PAIR_PAYOUT

        player_bj = is_blackjack(player_hand)

        # Sadə avtomatik strategiya: 16 və aşağı hit, 17+ stand (v1 üçün)
        while hand_value(player_hand) < 17:
            player_hand.append(draw())

        player_total = hand_value(player_hand)
        busted = player_total > 21

        spots_result.append({
            "bet_qepik": bet,
            "side_bet_qepik": side_bet,
            "side_win_qepik": side_win,
            "cards": player_hand,
            "total": player_total,
            "busted": busted,
            "blackjack": player_bj,
        })

    dealer_bj = is_blackjack(dealer_hand)
    if not all(s is None for s in spots_result):
        while hand_value(dealer_hand) < 17:
            dealer_hand.append(draw())
    dealer_total = hand_value(dealer_hand)
    dealer_busted = dealer_total > 21

    total_payout = 0
    for spot in spots_result:
        if spot is None:
            continue
        payout = spot["side_win_qepik"]  # side bet qazancı əlavə olunur
        if spot["busted"]:
            pass  # uduzdu, heç nə əlavə olunmur
        elif spot["blackjack"] and not dealer_bj:
            payout += spot["bet_qepik"] + round(spot["bet_qepik"] * BLACKJACK_PAYOUT_MULT)
        elif spot["blackjack"] and dealer_bj:
            payout += spot["bet_qepik"]  # push
        elif dealer_busted:
            payout += spot["bet_qepik"] * 2
        elif spot["total"] > dealer_total:
            payout += spot["bet_qepik"] * 2
        elif spot["total"] == dealer_total:
            payout += spot["bet_qepik"]  # push
        # spot["total"] < dealer_total -> uduzdu, heç nə

        total_payout += payout
        spot["payout_qepik"] = payout

    net_delta = total_payout - total_bet
    new_balance = adjust_balance(telegram_id, net_delta)

    import json
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO blackjack_rounds
               (telegram_id, spots_json, dealer_hand_json, total_bet_qepik, total_payout_qepik,
                server_seed_hash, client_seed, nonce, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (telegram_id, json.dumps(spots_result), json.dumps(dealer_hand),
             total_bet, total_payout, seed_row["server_seed_hash"], seed_row["client_seed"],
             used_nonce, datetime.now(timezone.utc)),
        )

    return {
        "spots": spots_result,
        "dealer_hand": dealer_hand,
        "dealer_total": dealer_total,
        "dealer_busted": dealer_busted,
        "dealer_blackjack": dealer_bj,
        "total_bet_qepik": total_bet,
        "total_payout_qepik": total_payout,
        "new_balance_qepik": new_balance,
        "server_seed_hash": seed_row["server_seed_hash"],
        "client_seed": seed_row["client_seed"],
        "nonce": used_nonce,
      }
