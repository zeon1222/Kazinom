import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from database import get_db

ROWS = 9

RISK_TABLES = {
    # Multiplerler binomial(9,0.5) bin ehtimallarına görə HESABLANIB ki
    # RTP tam olaraq ~95%-ə çıxsın (simulyasiya ilə təsdiqlənib:
    # LOW≈94.9%, MEDIUM≈94.7%, HIGH≈94.5%)
    "LOW":    [2.78, 1.56, 1.22, 1.0, 0.78, 0.78, 1.0, 1.22, 1.56, 2.78],
    "MEDIUM": [11.02, 3.31, 1.76, 0.99, 0.44, 0.44, 0.99, 1.76, 3.31, 11.02],
    "HIGH":   [63.39, 9.22, 1.61, 0.29, 0.12, 0.12, 0.29, 1.61, 9.22, 63.39],
}
DEFAULT_RISK = "MEDIUM"
MIN_BET_QEPIK = 5
MAX_BET_QEPIK = 10000
HISTORY_LIMIT = 25


def init_game_tables():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plinko_seeds (
                telegram_id TEXT PRIMARY KEY,
                server_seed TEXT NOT NULL,
                server_seed_hash TEXT NOT NULL,
                client_seed TEXT NOT NULL,
                nonce INTEGER NOT NULL DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plinko_bets (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                risk TEXT NOT NULL,
                bet_qepik INTEGER NOT NULL,
                bin_index INTEGER NOT NULL,
                multiplier REAL NOT NULL,
                payout_qepik INTEGER NOT NULL,
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
        cur.execute("SELECT * FROM plinko_seeds WHERE telegram_id = %s", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row
        seed, seed_hash = new_server_seed_pair()
        client_seed = secrets.token_hex(8)
        cur.execute(
            """INSERT INTO plinko_seeds (telegram_id, server_seed, server_seed_hash, client_seed, nonce)
               VALUES (%s, %s, %s, %s, 0) RETURNING *""",
            (telegram_id, seed, seed_hash, client_seed),
        )
        return cur.fetchone()


def compute_drop(server_seed: str, client_seed: str, nonce: int):
    msg = f"{client_seed}:{nonce}".encode()
    digest = hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
    path = []
    rights = 0
    for i in range(ROWS):
        byte = digest[i % len(digest)]
        step_right = 1 if byte % 2 == 1 else 0
        path.append(step_right)
        rights += step_right
    return rights, path


def place_bet(telegram_id: str, bet_qepik: int, risk: str):
    from core.balance_service import try_deduct_balance, adjust_balance, raise_if_banned
    from fastapi import HTTPException

    raise_if_banned(telegram_id)

    if risk not in RISK_TABLES:
        raise HTTPException(status_code=400, detail="Yanlış risk səviyyəsi.")
    if bet_qepik < MIN_BET_QEPIK:
        raise HTTPException(status_code=400, detail="Minimum mərc çox aşağıdır.")
    if bet_qepik > MAX_BET_QEPIK:
        raise HTTPException(status_code=400, detail="Maksimum mərc həddi aşıldı.")

    balance_after_bet = try_deduct_balance(telegram_id, bet_qepik)
    if balance_after_bet is None:
        raise HTTPException(status_code=400, detail="Balans kifayət etmir.")

    seed_row = get_or_create_seed(telegram_id)
    multipliers = RISK_TABLES[risk]

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE plinko_seeds SET nonce = nonce + 1 WHERE telegram_id = %s RETURNING nonce",
            (telegram_id,),
        )
        new_nonce = cur.fetchone()["nonce"]
        used_nonce = new_nonce - 1

        bin_index, path = compute_drop(seed_row["server_seed"], seed_row["client_seed"], used_nonce)
        multiplier = multipliers[bin_index]
        payout_qepik = round(bet_qepik * multiplier)

        cur.execute(
            """INSERT INTO plinko_bets
               (telegram_id, risk, bet_qepik, bin_index, multiplier, payout_qepik,
                server_seed_hash, client_seed, nonce, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (telegram_id, risk, bet_qepik, bin_index, multiplier, payout_qepik,
             seed_row["server_seed_hash"], seed_row["client_seed"], used_nonce,
             datetime.now(timezone.utc)),
        )

    new_balance = adjust_balance(telegram_id, payout_qepik)

    return {
        "bin_index": bin_index,
        "path": path,
        "multiplier": multiplier,
        "payout_qepik": payout_qepik,
        "new_balance_qepik": new_balance,
        "server_seed_hash": seed_row["server_seed_hash"],
        "client_seed": seed_row["client_seed"],
        "nonce": used_nonce,
    }


def get_history(telegram_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT risk, bet_qepik, bin_index, multiplier, payout_qepik, created_at
               FROM plinko_bets WHERE telegram_id = %s ORDER BY id DESC LIMIT %s""",
            (telegram_id, HISTORY_LIMIT),
        )
        return cur.fetchall()


def rotate_seed(telegram_id: str, new_client_seed: str):
    seed_row = get_or_create_seed(telegram_id)
    old_seed = seed_row["server_seed"]
    old_hash = seed_row["server_seed_hash"]
    new_seed, new_hash = new_server_seed_pair()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE plinko_seeds SET server_seed = %s, server_seed_hash = %s,
               client_seed = %s, nonce = 0 WHERE telegram_id = %s""",
            (new_seed, new_hash, new_client_seed, telegram_id),
        )
    return {
        "revealed_previous_server_seed": old_seed,
        "previous_server_seed_hash": old_hash,
        "new_server_seed_hash": new_hash,
        "new_client_seed": new_client_seed,
}
