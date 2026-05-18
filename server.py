"""
server.py — Quantum Tic-Tac-Toe 極簡後端
=========================================
本地/線上聯機對戰，開房加房觀戰功能。
"""

import time
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
        self.game = QuantumGame()
        self.mode = mode
        self.x = None
        self.o = None
        # Clean timer: each player has a pool of remaining seconds
        self.x_time_remaining = 60.0
        self.o_time_remaining = 60.0
        # When the current turn started (time.time()), None if not started
        self.turn_start_time: float | None = None
        # Which player's clock is running
        self.last_active_player: str | None = None

    def _active_player(self, st: dict) -> str | None:
        """Who should have their clock running right now."""
        if st["status"] == "collapse":
            return (st.get("pending") or {}).get("choosing_player")
        if st["status"] == "playing":
            return st.get("current_player")
        return None

    def _charge_current_player(self, now: float):
        """Deduct elapsed time from whichever player's clock was running."""
        if self.turn_start_time is None or self.last_active_player is None:
            return
        elapsed = now - self.turn_start_time
        if self.last_active_player == "X":
            self.x_time_remaining = max(0.0, self.x_time_remaining - elapsed)
        else:
            self.o_time_remaining = max(0.0, self.o_time_remaining - elapsed)
        self.turn_start_time = now  # reset so we don't double-charge

    def get_state(self) -> dict:
        st = self.game.get_state()
        players_ready = (self.mode == "pve") or (
            self.x is not None and self.o is not None
        )
        now = time.time()

        active_player = self._active_player(st)

        if not players_ready or st["status"] == "finished":
            # Pause everything
            if st["status"] == "finished":
                self._charge_current_player(now)
                self.turn_start_time = None
            else:
                # Waiting for second player — reset clocks
                self.x_time_remaining = 60.0
                self.o_time_remaining = 60.0
                self.turn_start_time = None
                self.last_active_player = None
        else:
            if active_player != self.last_active_player:
                # Turn changed: charge the previous player
                self._charge_current_player(now)
                self.last_active_player = active_player
                self.turn_start_time = now

        # Compute display values (do NOT mutate remaining here)
        elapsed_now = (now - self.turn_start_time) if self.turn_start_time is not None else 0.0
        if active_player == "X" and players_ready and st["status"] != "finished":
            x_display = max(0.0, self.x_time_remaining - elapsed_now)
            o_display = self.o_time_remaining
        elif active_player == "O" and players_ready and st["status"] != "finished":
            x_display = self.x_time_remaining
            o_display = max(0.0, self.o_time_remaining - elapsed_now)
        else:
            x_display = self.x_time_remaining
            o_display = self.o_time_remaining

        st["x_time_left"] = round(x_display, 3)
        st["o_time_left"] = round(o_display, 3)
        st["players_ready"] = players_ready
        st["mode"] = self.mode
        return st


rooms: dict[str, RoomState] = {}

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
    if room_id not in rooms:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
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
    return JSONResponse({**result, "state": r.get_state()})

class ResolveBody(BaseModel):
    client_id: str
    piece_id: int
    chosen_cell: int

@app.post("/api/resolve/{room_id}")
def resolve(room_id: str, body: ResolveBody):
    if room_id not in rooms:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
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
    return JSONResponse({**result, "state": r.get_state()})

class TimeoutBody(BaseModel):
    client_id: str

@app.post("/api/timeout/{room_id}")
def timeout(room_id: str, body: TimeoutBody):
    if room_id not in rooms:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    game = r.game
    st = r.get_state()  # updates timer state first

    # Determine which player is timed out
    if game.status == GameStatus.FINISHED:
        return JSONResponse({"state": st})

    active_player = r.last_active_player
    timed_out_time = r.x_time_remaining if active_player == "X" else r.o_time_remaining

    if timed_out_time <= 0:
        game.status = GameStatus.FINISHED
        game.winner = "O" if active_player == "X" else "X"
        game.winning_lines = []
        r.turn_start_time = None

    return JSONResponse({"state": r.get_state()})

class ResetBody(BaseModel):
    client_id: str

@app.post("/api/reset/{room_id}")
def reset(room_id: str, body: ResetBody):
    if room_id not in rooms:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    r = rooms[room_id]
    if r.mode == "pvp" and body.client_id not in (r.x, r.o):
        return JSONResponse({"error": "觀戰者無法重新開始"}, status_code=403)
    r.game = QuantumGame()
    r.x_time_remaining = 60.0
    r.o_time_remaining = 60.0
    r.turn_start_time = None
    r.last_active_player = None
    return JSONResponse({"ok": True, "state": r.get_state()})

class AIMoveBody(BaseModel):
    client_id: str

@app.post("/api/ai_move/{room_id}")
def ai_move(room_id: str, body: AIMoveBody):
    if room_id not in rooms:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
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
