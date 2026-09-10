from fastapi import HTTPException
from config import ADMIN_API_KEY


def require_admin(admin_key: str):
    if not admin_key or admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Admin icazəsi yoxdur.")
