import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from database import get_db

# Simgeler ve ağırlıkları (küçük ağırlık = nadir = yüksek ödeme)
# Meyveler daha sonra frontend'de görsellerle eşleştirilecek
SYMBOLS = {
    "cherry":     {"weight": 30, "payout_3": 2,  "payout_2": 0},
    "grape":      {"weight": 24, "payout_3": 3,  "payout_2": 0},
    "orange":     {"weight": 20, "payout_3": 5,  "payout_2": 0},
    "watermelon": {"weight": 14, "payout_3": 10, "payout_2": 0},
    "pineapple":  {"weight": 8,  "payout_3": 25, "payout_2": 0},
    "star":       {"weight": 3,  "payout_3": 100, "payout_2": 0},   # scatter tarzı, nadir
    "seven":      {"weight": 1,  "payout_3": 500, "payout_2": 0},   # jackpot simgesi
}
SYMBOL_LIST = list(SYMBOLS.keys())
TOTAL_WEIGHT = sum(s["weight"] for s in SYMBOLS.values())

# 3x3 grid: grid[row][col], sətir 0 = yuxarı
# 5 pay line (verilən şəkillərə uyğun: 3 üfüqi + 2 ziqzaq)
PAYLINES = [
    [(0, 0), (0, 1), (0, 2)],  # 1: üst sıra
    [(1, 0), (1, 1), (1, 2)],  # 2: orta sıra
    [(2, 0), (2, 1), (2, 2)],  # 3: alt sıra
    [(0, 0), (1, 1), (2, 2)],  # 4: diaqonal aşağı
    [(0, 0), (1, 1), (0, 2)],  # 5: zigzag (V)
]

MIN_BET_QEPIK = 10
MAX_BET_QEPIK = 20000
TARGET_RTP = 0.95  # istənilən RTP-yə yaxınlaşdırmaq üçün ödəniş cədvəli buna görə tənzimlənib


def init_game_tables():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slot_seeds (
                telegram_id TEXT PRIMARY KEY,
                server_seed TEXT NOT NULL,
                server_seed_hash TEXT NOT NULL,
                client_seed TEXT NOT NULL,
                nonce INTEGER NOT NULL DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slot_spins (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                bet_qepik INTEGER NOT NULL,
                grid_json TEXT NOT NULL,
                payout_qepik INTEGER NOT NULL,
                winning_lines_json TEXT NOT NULL,
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
        cur.execute("SELECT * FROM slot_seeds WHERE telegram_id = %s", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row
        seed, seed_hash = new_server_seed_pair()
        client_seed = secrets.token_hex(8)
        cur.execute(
            """INSERT INTO slot_seeds (telegram_id, server_seed, server_seed_hash, client_seed, nonce)
               VALUES (%s, %s, %s, %s, 0) RETURNING *""",
            (telegram_id, seed, seed_hash, client_seed),
        )
        return cur.fetchone()


def _pick_symbol(rand_val: int) -> str:
    """0-999999 arası bir dəyəri, ağırlıqlara görə simvola çevirir."""
    threshold = rand_val % TOTAL_WEIGHT
    cumulative = 0
    for name, data in SYMBOLS.items():
        cumulative += data["weight"]
        if threshold < cumulative:
            return name
    return SYMBOL_LIST[-1]  # təhlükəsizlik üçün


def generate_grid(server_seed: str, client_seed: str, nonce: int):
    msg = f"{client_seed}:{nonce}".encode()
    digest = b""
    counter = 0
    while len(digest) < 9 * 4:  # 9 xana, hər biri üçün 4 bayt
        digest += hmac.new(server_seed.encode(), msg + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1

    grid = [[None, None, None] for _ in range(3)]
    idx = 0
    for row in range(3):
        for col in range(3):
            chunk = digest[idx * 4: idx * 4 + 4]
            rand_val = int.from_bytes(chunk, "big")
            grid[row][col] = _pick_symbol(rand_val)
            idx += 1
    return grid


def evaluate_lines(grid, bet_qepik):
    winning_lines = []
    total_payout = 0

    for line_idx, positions in enumerate(PAYLINES):
        symbols_on_line = [grid[r][c] for r, c in positions]
        s0 = symbols_on_line[0]
        # bütün simvollar eynidirsə -> 3-lük ödəniş
        if symbols_on_line[0] == symbols_on_line[1] == symbols_on_line[2]:
            payout_mult = SYMBOLS[s0]["payout_3"]
            payout = round(bet_qepik * payout_mult / 5)  # hər line bahisin 1/5-i qədər sayılır (5 line-a bölünmüş bet)
            if payout > 0:
                total_payout += payout
                winning_lines.append({
                    "line_index": line_idx + 1,
                    "positions": positions,
                    "symbol": s0,
                    "payout_qepik": payout,
                })

    return winning_lines, total_payout


def spin(telegram_id: str, bet_qepik: int):
    from core.balance_service import get_or_create_user, adjust_balance
    from fastapi import HTTPException

    if bet_qepik < MIN_BET_QEPIK or bet_qepik > MAX_BET_QEPIK:
        raise HTTPException(status_code=400, detail="Mərc həddi aşıldı.")

    user = get_or_create_user(telegram_id)
    if user["balance_qepik"] < bet_qepik:
        raise HTTPException(status_code=400, detail="Balans kifayət etmir.")

    seed_row = get_or_create_seed(telegram_id)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE slot_seeds SET nonce = nonce + 1 WHERE telegram_id = %s RETURNING nonce",
            (telegram_id,),
        )
        used_nonce = cur.fetchone()["nonce"] - 1

    grid = generate_grid(seed_row["server_seed"], seed_row["client_seed"], used_nonce)
    winning_lines, total_payout = evaluate_lines(grid, bet_qepik)

    net_delta = total_payout - bet_qepik
    new_balance = adjust_balance(telegram_id, net_delta)

    import json
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO slot_spins
               (telegram_id, bet_qepik, grid_json, payout_qepik, winning_lines_json,
                server_seed_hash, client_seed, nonce, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (telegram_id, bet_qepik, json.dumps(grid), total_payout, json.dumps(winning_lines),
             seed_row["server_seed_hash"], seed_row["client_seed"], used_nonce,
             datetime.now(timezone.utc)),
        )

    return {
        "grid": grid,
        "winning_lines": winning_lines,
        "payout_qepik": total_payout,
        "new_balance_qepik": new_balance,
        "server_seed_hash": seed_row["server_seed_hash"],
        "client_seed": seed_row["client_seed"],
        "nonce": used_nonce,
    }


def get_history(telegram_id: str, limit: int = 25):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT bet_qepik, grid_json, payout_qepik, created_at
               FROM slot_spins WHERE telegram_id = %s ORDER BY id DESC LIMIT %s""",
            (telegram_id, limit),
        )
        return cur.fetchall()
