from contextlib import contextmanager
import psycopg2
import psycopg2.extras
from config import DATABASE_URL


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Bütün oyunların ortaq cədvəllərini yaradır. Hər oyun öz cədvəlini
    öz engine.py-də əlavə edir (init_game_tables funksiyası ilə)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id TEXT PRIMARY KEY,
                balance_qepik INTEGER NOT NULL DEFAULT 0,
                last_bonus_claim TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                amount_qepik INTEGER NOT NULL,
                receipt_note TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                amount_qepik INTEGER NOT NULL,
                payout_note TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ
            )
        """)
