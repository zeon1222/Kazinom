from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from games.registry import GAMES
from core.deposit import router as deposit_router
from core.withdraw import router as withdraw_router
from core.admin import router as admin_router

app = FastAPI(title="Kazinom API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    for game in GAMES:
        # hər oyun öz cədvəllərini özü yaradır
        module = __import__(f"games.{game['name']}.engine", fromlist=["init_game_tables"])
        if hasattr(module, "init_game_tables"):
            module.init_game_tables()


for game in GAMES:
    app.include_router(game["router"], prefix=game["prefix"])

app.include_router(deposit_router, prefix="/api/deposit")
app.include_router(withdraw_router, prefix="/api/withdraw")
app.include_router(admin_router, prefix="/api/admin")


@app.get("/api/games")
def list_games():
    return {"games": [g["name"] for g in GAMES]}
