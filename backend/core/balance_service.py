from database import get_db
from config import STARTING_BALANCE_QEPIK


def get_or_create_user(telegram_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row
        cur.execute(
            "INSERT INTO users (telegram_id, balance_qepik) VALUES (%s, %s) RETURNING *",
            (telegram_id, STARTING_BALANCE_QEPIK),
        )
        return cur.fetchone()


def get_balance(telegram_id: str) -> int:
    user = get_or_create_user(telegram_id)
    return user["balance_qepik"]


def adjust_balance(telegram_id: str, delta_qepik: int) -> int:
    """Balansı dəyişir (mənfi ola bilər). Yalnız artırma və ya artıq
    yoxlanmış düşürmə üçün istifadə et. Bahis/çıxarış kimi kifayət
    qədər balans varmı yoxlaması lazım olan hallarda try_deduct_balance
    istifadə et (aşağıda) — yoxsa iki paralel sorğu balansı mənfiyə sala bilər."""
    get_or_create_user(telegram_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET balance_qepik = balance_qepik + %s WHERE telegram_id = %s RETURNING balance_qepik",
            (delta_qepik, telegram_id),
        )
        return cur.fetchone()["balance_qepik"]


def try_deduct_balance(telegram_id: str, amount_qepik: int):
    """Balansdan amount_qepik qədər ATOMİK şəkildə düşür — yalnız kifayət
    qədər balans varsa. Eyni istifadəçidən eyni anda 2 sorğu gəlsə belə
    (məs. düyməyə iki dəfə basanda) balansın mənfiyə düşməsinin qarşısını
    alır: yoxlama və düşürmə tək bir SQL sorğusunda baş verir.
    Returns: yeni balans (uğurlu olsa), None (balans kifayət etməsə)."""
    if amount_qepik <= 0:
        raise ValueError("amount_qepik must be positive")
    get_or_create_user(telegram_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE users SET balance_qepik = balance_qepik - %s
               WHERE telegram_id = %s AND balance_qepik >= %s
               RETURNING balance_qepik""",
            (amount_qepik, telegram_id, amount_qepik),
        )
        row = cur.fetchone()
        return row["balance_qepik"] if row else None


def is_banned(telegram_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT is_banned FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
            return bool(row and row.get("is_banned"))
        except Exception:
            return False


def raise_if_banned(telegram_id: str):
    from fastapi import HTTPException
    if is_banned(telegram_id):
        raise HTTPException(status_code=403, detail="Hesabınız bloklanıb.")
