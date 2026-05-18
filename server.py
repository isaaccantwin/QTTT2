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

@app.get("/")
def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/api/lobby")
def lobby():
    return JSONResponse({"lobby": get_lobby_info()})

class LeaveBody(BaseModel):
    client_id: str

@app.post("/api/leave/{room_id}")
def leave_room(room_id: str, body: LeaveBody):
    if room_id not in rooms:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    if r.x == body.client_id:
        r.x = None
    if r.o == body.client_id:
        r.o = None
    if r.x is None and r.o is None:
        del rooms[room_id]
    return JSONResponse({"ok": True})

class RoomState:
    def __init__(self, mode):
        import time
        self.game = QuantumGame()
        self.mode = mode
        self.x = None
        self.o = None
        self.last_move_count = 0
        self.last_status = "playing"
        self.last_current_player = None
        self.x_time_spent = 0
        self.o_time_spent = 0
        self.x_last_update = None
        self.o_last_update = None
        self.x_time_left = 60
        self.o_time_left = 60
        self.x_paused = False
        self.o_paused = False
        self.x_paused_at = None
        self.o_paused_at = None
        self.x_increment = 0
        self.o_increment = 0

    def get_state(self):
        import time
        st = self.game.get_state()
        players_ready = (self.mode == "pve") or (self.x is not None and self.o is not None)

        now = time.time()

        if st["status"] == "finished":
            self.x_time_left = 0
            self.o_time_left = 0
            self.x_paused = True
            self.o_paused = True
        elif not players_ready:
            self.x_time_left = 60
            self.o_time_left = 60
            self.x_time_spent = 0
            self.o_time_spent = 0
            self.x_last_update = None
            self.o_last_update = None
            self.last_current_player = None
            self.x_paused = False
            self.o_paused = False
        else:
            if st["status"] == "collapse":
                active_player = st.get("pending", {}).get("choosing_player")
            else:
                active_player = st.get("current_player")

            if active_player != self.last_current_player and self.last_current_player is not None:
                if self.last_current_player == "X" and self.x_last_update is not None and not self.x_paused:
                    self.x_time_spent += now - self.x_last_update
                    self.x_paused = True
                    self.x_paused_at = now
                elif self.last_current_player == "O" and self.o_last_update is not None and not self.o_paused:
                    self.o_time_spent += now - self.o_last_update
                    self.o_paused = True
                    self.o_paused_at = now

            if active_player == "X":
                if self.x_paused and self.x_paused_at is not None:
                    self.x_time_spent += now - self.x_paused_at
                    self.x_paused = False
                    self.x_paused_at = None
                self.x_last_update = now
                self.x_time_left = max(0, 60 - self.x_time_spent)
                time_left = self.x_time_left
            elif active_player == "O":
                if self.o_paused and self.o_paused_at is not None:
                    self.o_time_spent += now - self.o_paused_at
                    self.o_paused = False
                    self.o_paused_at = None
                self.o_last_update = now
                self.o_time_left = max(0, 60 - self.o_time_spent)
                time_left = self.o_time_left
            else:
                time_left = 0

            if active_player != self.last_current_player:
                self.last_current_player = active_player

        st["time_left"] = time_left if 'time_left' in dir() else 0
        st["x_time_left"] = self.x_time_left
        st["o_time_left"] = self.o_time_left
        st["players_ready"] = players_ready
        st["mode"] = self.mode
        return st

rooms = {}

def get_lobby_info():
    lobby = []
    for room_id, r in rooms.items():
        state = r.get_state()
        if not state["players_ready"]:
            lobby.append({
                "room_id": room_id,
                "mode": r.mode,
                "players": {
                    "X": r.x[:8] + "..." if r.x else None,
                    "O": r.o[:8] + "..." if r.o else None
                }
            })
    return lobby

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
    if body.mode == "pve":
        r.o = body.client_id
        role = "O"
    else:
        r.x = body.client_id
        role = "X"
    rooms[room_id] = r
    return JSONResponse({"room_id": room_id, "role": role, "state": r.get_state()})

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
    elif r.mode == "pve":
        if r.game.current_player != "O":
            return JSONResponse({"error": "等待 AI 思考中..."}, status_code=403)
        if r.o != body.client_id:
            return JSONResponse({"error": "你不是玩家 O"}, status_code=403)

    result = r.game.place(body.c1, body.s1, body.c2, body.s2)
    r.get_state()
    if r.game.current_player == "X":
        r.o_time_left += r.o_increment
        r.o_time_spent = max(0, 60 - r.o_time_left)
    elif r.game.current_player == "O":
        r.x_time_left += r.x_increment
        r.x_time_spent = max(0, 60 - r.x_time_left)
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
    elif r.mode == "pve":
        cp = r.game.pending.get("choosing_player") if r.game.pending else None
        if cp == "O" and r.o != body.client_id:
            return JSONResponse({"error": "還沒輪到你選擇"}, status_code=403)

    result = r.game.resolve(body.piece_id, body.chosen_cell)
    r.get_state()
    if r.game.current_player == "X":
        r.o_time_left += r.o_increment
        r.o_time_spent = max(0, 60 - r.o_time_left)
    elif r.game.current_player == "O":
        r.x_time_left += r.x_increment
        r.x_time_spent = max(0, 60 - r.x_time_left)
    return JSONResponse({**result, "state": r.get_state()})

class TimeoutBody(BaseModel):
    client_id: str

@app.post("/api/timeout/{room_id}")
def timeout(room_id: str, body: TimeoutBody):
    if room_id not in rooms: return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    game = r.game
    if game.status != GameStatus.FINISHED and r.game_time_left <= 0:
        game.status = GameStatus.FINISHED
        timed_out = game.pending["choosing_player"] if game.status == GameStatus.COLLAPSE else game.current_player
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
    r.x_time_left = 60
    r.o_time_left = 60
    r.x_time_spent = 0
    r.o_time_spent = 0
    r.x_last_update = None
    r.o_last_update = None
    r.last_current_player = None
    r.x_increment = 0
    r.o_increment = 0
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

    r.get_state()
    if game.current_player == "X":
        r.o_time_left += r.o_increment
        r.o_time_spent = max(0, 60 - r.o_time_left)
    elif game.current_player == "O":
        r.x_time_left += r.x_increment
        r.x_time_spent = max(0, 60 - r.x_time_left)

    return JSONResponse({"ok": True, "state": r.get_state()})

# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
