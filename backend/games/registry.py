"""
Oyun qeydiyyat sistemi.
Yeni oyun əlavə etmək üçün: öz qovluğunu yarat (games/yeni_oyun/),
routes.py-də bir APIRouter yaz, sonra aşağıya 2 sətir əlavə et. Bu qədər.
main.py-ə HEÇ TOXUNMURSAN.
"""

from games.plinko.routes import router as plinko_router
from games.blackjack.routes import router as blackjack_router
from games.slot.routes import router as slot_router

GAMES = [
    {"name": "plinko", "router": plinko_router, "prefix": "/api/plinko"},
    {"name": "blackjack", "router": blackjack_router, "prefix": "/api/blackjack"},
    {"name": "slot", "router": slot_router, "prefix": "/api/slot"},
]
