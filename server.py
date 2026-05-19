"""
server.py — Quantum Tic-Tac-Toe 極簡後端
=========================================
本地/線上聯機對戰，開房加房觀戰功能。
"""

import time
import uvicorn
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import random
import json
import datetime

from game_logic import QuantumGame, GameStatus

redis_error = None
try:
    import redis
    kv = redis.from_url(os.environ.get("KV_REST_API_URL"))
    kv.ping()
    USE_REDIS = True
    print("✅ Redis connected")
except Exception as e:
    USE_REDIS = False
    redis_error = str(e)
    print(f"❌ Redis not available: {e}")

app = FastAPI(title="Quantum Tic-Tac-Toe")

@app.get("/api/debug")
def debug():
    return JSONResponse({
        "redis_connected": USE_REDIS,
        "redis_url": os.environ.get("KV_REST_API_URL", "")[:30] + "..." if os.environ.get("KV_REST_API_URL") else "not set",
        "redis_error": redis_error
    })

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
    r = load_room(room_id)
    if r is None:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    if r.x == body.client_id:
        r.x = None
    if r.o == body.client_id:
        r.o = None
    if r.x is None and r.o is None:
        delete_room(room_id)
    else:
        if USE_REDIS:
            save_room(room_id, r)
        else:
            rooms[room_id] = r
    return JSONResponse({"ok": True})

import threading
import urllib.request

class FeedbackBody(BaseModel):
    text: str
    room_id: str | None = None
    game_mode: str | None = None
    client_id: str | None = None
    role: str | None = None

@app.post("/api/feedback")
def submit_feedback(body: FeedbackBody):
    feedback_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "text": body.text,
        "client_id": body.client_id,
        "room_id": body.room_id,
        "game_mode": body.game_mode,
        "role": body.role
    }
    
    # 1. 本地備份：Save as JSONL (如果主機支援寫入，例如本地開發時)
    try:
        with open("feedback.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(feedback_data, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 忽略 Vercel 正式機唯讀檔案系統的寫入錯誤

    # 2. Google Sheets 整合：透過 Webhook 傳送至 Google 試算表
    webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL")
    if webhook_url:
        def send_to_sheet():
            try:
                req = urllib.request.Request(
                    webhook_url, 
                    data=json.dumps(feedback_data).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    pass
            except Exception as e:
                print(f"Google Sheet Webhook Error: {e}")
        
        # 使用 Thread 異步發送，不卡住使用者的 API 回應時間
        threading.Thread(target=send_to_sheet).start()
        
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

def save_room(room_id: str, r: RoomState):
    if USE_REDIS:
        data = {
            "game": r.game.get_state(),
            "mode": r.mode,
            "x": r.x,
            "o": r.o,
            "x_time_remaining": r.x_time_remaining,
            "o_time_remaining": r.o_time_remaining,
            "turn_start_time": r.turn_start_time,
            "last_active_player": r.last_active_player
        }
        kv.set(f"room:{room_id}", json.dumps(data), ex=3600)

def load_room(room_id: str) -> RoomState | None:
    if USE_REDIS:
        data = kv.get(f"room:{room_id}")
        if data:
            d = json.loads(data)
            r = RoomState(d["mode"])
            r.game = QuantumGame()
            r.game.restore_state(d["game"])
            r.x = d["x"]
            r.o = d["o"]
            r.x_time_remaining = d["x_time_remaining"]
            r.o_time_remaining = d["o_time_remaining"]
            r.turn_start_time = d["turn_start_time"]
            r.last_active_player = d["last_active_player"]
            return r
    return None

def delete_room(room_id: str):
    if USE_REDIS:
        kv.delete(f"room:{room_id}")

def get_all_room_ids() -> list[str]:
    if USE_REDIS:
        return [k.decode().split(":")[1] for k in kv.keys("room:*")]
    return list(rooms.keys())

def get_lobby_info():
    lobby = []
    room_ids = get_all_room_ids()
    for room_id in room_ids:
        r = load_room(room_id)
        if r:
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
    print(f"create_room: mode={body.mode}, client_id={body.client_id[:8]}...")
    room_id = str(random.randint(1000, 9999))
    while load_room(room_id) is not None:
        room_id = str(random.randint(1000, 9999))
    r = RoomState(body.mode)
    if body.mode == "pve":
        r.o = body.client_id
        role = "O"
    else:
        r.x = body.client_id
        role = "X"
    if USE_REDIS:
        save_room(room_id, r)
        print(f"  saved to Redis: room_id={room_id}, role={role}")
    else:
        rooms[room_id] = r
        print(f"  saved to memory: room_id={room_id}, role={role}")
    return JSONResponse({"room_id": room_id, "role": role, "state": r.get_state()})

class JoinRoomBody(BaseModel):
    room_id: str
    client_id: str

@app.post("/api/join_room")
def join_room(body: JoinRoomBody):
    r = load_room(body.room_id)
    if r is None:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    if r.x == body.client_id:
        role = "X"
    elif r.o == body.client_id:
        role = "O"
    elif r.o is None and r.mode == "pvp" and r.x != body.client_id:
        r.o = body.client_id
        role = "O"
    else:
        role = "Spectator"
    if USE_REDIS:
        save_room(body.room_id, r)
    else:
        rooms[body.room_id] = r
    return JSONResponse({"role": role, "state": r.get_state()})

@app.get("/api/state/{room_id}")
def get_state(room_id: str):
    r = load_room(room_id)
    if r is None:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    return JSONResponse(r.get_state())

class PlaceBody(BaseModel):
    client_id: str
    c1: int
    s1: int
    c2: int
    s2: int

@app.post("/api/place/{room_id}")
def place(room_id: str, body: PlaceBody):
    r = load_room(room_id)
    if r is None:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)

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
    if USE_REDIS:
        save_room(room_id, r)
    else:
        rooms[room_id] = r
    return JSONResponse({**result, "state": r.get_state()})

class ResolveBody(BaseModel):
    client_id: str
    piece_id: int
    chosen_cell: int

@app.post("/api/resolve/{room_id}")
def resolve(room_id: str, body: ResolveBody):
    r = load_room(room_id)
    if r is None:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)

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
    if USE_REDIS:
        save_room(room_id, r)
    else:
        rooms[room_id] = r
    return JSONResponse({**result, "state": r.get_state()})

class TimeoutBody(BaseModel):
    client_id: str

@app.post("/api/timeout/{room_id}")
def timeout(room_id: str, body: TimeoutBody):
    r = load_room(room_id)
    if r is None:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
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

    if USE_REDIS:
        save_room(room_id, r)
    else:
        rooms[room_id] = r
    return JSONResponse({"state": r.get_state()})

class ResetBody(BaseModel):
    client_id: str

@app.post("/api/reset/{room_id}")
def reset(room_id: str, body: ResetBody):
    r = load_room(room_id)
    if r is None:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
    if r.mode == "pvp" and body.client_id not in (r.x, r.o):
        return JSONResponse({"error": "觀戰者無法重新開始"}, status_code=403)
    r.game = QuantumGame()
    r.x_time_remaining = 60.0
    r.o_time_remaining = 60.0
    r.turn_start_time = None
    r.last_active_player = None
    if USE_REDIS:
        save_room(room_id, r)
    else:
        rooms[room_id] = r
    return JSONResponse({"ok": True, "state": r.get_state()})

class AIMoveBody(BaseModel):
    client_id: str

@app.post("/api/ai_move/{room_id}")
def ai_move(room_id: str, body: AIMoveBody):
    r = load_room(room_id)
    if r is None:
        return JSONResponse({"error": "找不到該房間"}, status_code=404)
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

    if USE_REDIS:
        save_room(room_id, r)
    else:
        rooms[room_id] = r
    return JSONResponse({"ok": True, "state": r.get_state()})

# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
