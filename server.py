"""
server.py — Quantum Tic-Tac-Toe 極簡後端
=========================================
本地/線上聯機對戰，開房加房觀戰功能。
"""

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import random

from game_logic import QuantumGame, GameStatus

app = FastAPI(title="Quantum Tic-Tac-Toe")

class RoomState:
    def __init__(self, mode):
        import time
        self.game = QuantumGame()
        self.mode = mode
        self.x = None
        self.o = None
        self.last_move_count = 0
        self.last_status = "playing"
        self.turn_start_time = time.time()

    def get_state(self):
        import time
        st = self.game.get_state()
        # Check if turn changed to reset timer
        if st["move_count"] != self.last_move_count or st["status"] != self.last_status:
            self.last_move_count = st["move_count"]
            self.last_status = st["status"]
            self.turn_start_time = time.time()
        
        elapsed = time.time() - self.turn_start_time
        time_left = max(0, int(60 - elapsed))
        if st["status"] == "finished":
            time_left = 0
            
        st["time_left"] = time_left
        return st

rooms = {}

# ── HTTP ──────────────────────────────────────────────────────────────────────

class CreateRoomBody(BaseModel):
    mode: str = "pvp"
    client_id: str

@app.post("/api/create_room")
def create_room(body: CreateRoomBody):
    room_id = str(random.randint(1000, 9999))
    while room_id in rooms:
        room_id = str(random.randint(1000, 9999))
    r = RoomState(body.mode)
    r.x = body.client_id
    rooms[room_id] = r
    return JSONResponse({"room_id": room_id, "role": "X", "state": r.get_state()})

class JoinRoomBody(BaseModel):
    room_id: str
    client_id: str

@app.post("/api/join_room")
def join_room(body: JoinRoomBody):
    if body.room_id not in rooms:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[body.room_id]
    if r.x == body.client_id:
        role = "X"
    elif r.o == body.client_id:
        role = "O"
    elif r.o is None and r.mode == "pvp" and r.x != body.client_id:
        r.o = body.client_id
        role = "O"
    else:
        role = "Spectator"
    return JSONResponse({"role": role, "state": r.get_state()})

@app.get("/api/state/{room_id}")
def get_state(room_id: str):
    if room_id not in rooms:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    return JSONResponse(rooms[room_id].get_state())

class PlaceBody(BaseModel):
    client_id: str
    c1: int
    s1: int
    c2: int
    s2: int

@app.post("/api/place/{room_id}")
def place(room_id: str, body: PlaceBody):
    if room_id not in rooms: return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    
    if r.mode == "pvp":
        cp = r.game.current_player
        expected = r.x if cp == "X" else r.o
        if expected is None:
            return JSONResponse({"error": "等待對手加入..."}, status_code=403)
        if expected != body.client_id:
            return JSONResponse({"error": "不是你的回合或是觀戰者"}, status_code=403)
            
    result = r.game.place(body.c1, body.s1, body.c2, body.s2)
    return JSONResponse({**result, "state": r.get_state()})

class ResolveBody(BaseModel):
    client_id: str
    piece_id: int
    chosen_cell: int

@app.post("/api/resolve/{room_id}")
def resolve(room_id: str, body: ResolveBody):
    if room_id not in rooms: return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    
    if r.mode == "pvp":
        cp = r.game.pending.get("choosing_player") if r.game.pending else None
        if cp:
            expected = r.x if cp == "X" else r.o
            if expected is None:
                return JSONResponse({"error": "等待對手加入..."}, status_code=403)
            if expected != body.client_id:
                return JSONResponse({"error": "不是你的回合或是觀戰者"}, status_code=403)
                
    result = r.game.resolve(body.piece_id, body.chosen_cell)
    return JSONResponse({**result, "state": r.get_state()})

class TimeoutBody(BaseModel):
    client_id: str

@app.post("/api/timeout/{room_id}")
def timeout(room_id: str, body: TimeoutBody):
    if room_id not in rooms: return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    game = r.game
    if game.status != GameStatus.FINISHED:
        timed_out = game.pending["choosing_player"] if game.status == GameStatus.COLLAPSE else game.current_player
        game.status = GameStatus.FINISHED
        game.winner = "O" if timed_out == "X" else "X"
        game.winning_lines = []
    return JSONResponse({"state": r.get_state()})

class ResetBody(BaseModel):
    client_id: str

@app.post("/api/reset/{room_id}")
def reset(room_id: str, body: ResetBody):
    if room_id not in rooms: return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    if r.mode == "pvp" and body.client_id not in (r.x, r.o):
        return JSONResponse({"error": "觀戰者無法重新開始"}, status_code=403)
    r.game = QuantumGame()
    return JSONResponse({"ok": True, "state": r.get_state()})

class AIMoveBody(BaseModel):
    client_id: str

@app.post("/api/ai_move/{room_id}")
def ai_move(room_id: str, body: AIMoveBody):
    if room_id not in rooms: return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    game = r.game
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

    return JSONResponse({"ok": True, "state": r.get_state()})

# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
