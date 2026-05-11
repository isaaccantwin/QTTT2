"""
server.py — Quantum Tic-Tac-Toe 極簡後端
=========================================
本地兩人對戰（同一台電腦），不需要 WebSocket。
透過 REST API 傳遞遊戲狀態。
"""

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from game_logic import QuantumGame

app  = FastAPI(title="Quantum Tic-Tac-Toe")
game = QuantumGame()


# ── HTTP ──────────────────────────────────────────────────────────────────────


@app.get("/api/state")
def state():
    return JSONResponse(game.get_state())


class PlaceBody(BaseModel):
    c1: int
    s1: int
    c2: int
    s2: int


@app.post("/api/place")
def place(body: PlaceBody):
    result = game.place(body.c1, body.s1, body.c2, body.s2)
    return JSONResponse({**result, "state": game.get_state()})


class ResolveBody(BaseModel):
    piece_id: int
    chosen_cell: int


@app.post("/api/resolve")
def resolve(body: ResolveBody):
    result = game.resolve(body.piece_id, body.chosen_cell)
    return JSONResponse({**result, "state": game.get_state()})


@app.post("/api/timeout")
def timeout():
    from game_logic import GameStatus
    if game.status != GameStatus.FINISHED:
        timed_out = game.pending["choosing_player"] if game.status == GameStatus.COLLAPSE else game.current_player
        game.status = GameStatus.FINISHED
        game.winner = "O" if timed_out == "X" else "X"
        game.winning_lines = []
class ResetBody(BaseModel):
    mode: str = "pvp"

@app.post("/api/reset")
def reset(body: ResetBody):
    global game, game_mode
    game = QuantumGame()
    game_mode = body.mode
    return JSONResponse({"ok": True, "state": game.get_state()})

@app.post("/api/ai_move")
def ai_move():
    from game_logic import GameStatus
    import random
    
    if game.status == GameStatus.PLAYING:
        valid_spots = []
        for i in range(9):
            if i not in game.classical:
                occupied = {p.get_subcell(i) for p in game.quantum[i]}
                for s in range(9):
                    if s not in occupied:
                        valid_spots.append((i, s))
        
        if len(valid_spots) >= 2:
            c1s1 = random.choice(valid_spots)
            valid_c2 = [x for x in valid_spots if x[0] != c1s1[0]]
            if valid_c2:
                c2s2 = random.choice(valid_c2)
                game.place(c1s1[0], c1s1[1], c2s2[0], c2s2[1])
            else:
                c2s2 = random.choice([x for x in valid_spots if x != c1s1])
                game.place(c1s1[0], c1s1[1], c1s1[0], c2s2[1])

    elif game.status == GameStatus.COLLAPSE:
        pd = game.pending
        cycle_pieces = pd.get("cycle_pieces", [pd["piece_id"]])
        pid = random.choice(cycle_pieces)
        if pid in game.pieces:
            piece = game.pieces[pid]
            chosen_cell = random.choice([piece.c1, piece.c2])
            game.resolve(pid, chosen_cell)

    return JSONResponse({"ok": True, "state": game.get_state()})


# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
