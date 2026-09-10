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
    """Bakiyəni artırır/azaldır. Hər oyun bahis/qazanc üçün bunu çağırır."""
    get_or_create_user(telegram_id)  # istifadəçi mövcud olmasa yaradır
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET balance_qepik = balance_qepik + %s WHERE telegram_id = %s RETURNING balance_qepik",
            (delta_qepik, telegram_id),
        )
        return cur.fetchone()["balance_qepik"]
