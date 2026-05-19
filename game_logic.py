"""
game_logic.py — Quantum Tic-Tac-Toe (原版規則)
================================================
規則（依 Allan Goff 原版）：
  1. 每回合，玩家在兩個不同的格子放「量子棋子」（spooky mark）
     例如 X 的第 1 步 → X₁ 同時出現在格子 a 和格子 b
  2. 量子棋子形成糾纏圖（entanglement graph）的邊
  3. 若放棋後形成環（cyclic entanglement），觸發「測量」
     由「沒有製造環的那位玩家」決定環棋子要塌縮到哪個格子
  4. 塌縮會級聯 BFS 傳播
  5. 格子確定（classical mark）後不能再放棋子
  6. 三個傳統棋子連線 → 獲勝
  7. 若兩人同時連線，下標最大值較小的人得 1 分（另一人 0.5 分）
"""

from collections import deque
from enum import Enum
from typing import Optional


class GameStatus(Enum):
    PLAYING  = "playing"
    COLLAPSE = "collapse"   # 等待選擇塌縮方向
    FINISHED = "finished"


WIN_LINES = [
    [0, 1, 2], [3, 4, 5], [6, 7, 8],
    [0, 3, 6], [1, 4, 7], [2, 5, 8],
    [0, 4, 8], [2, 4, 6],
]


class QuantumPiece:
    def __init__(self, move_id: int, player: str, c1: int, s1: int, c2: int, s2: int):
        self.id     = move_id
        self.player = player
        self.c1, self.c2 = c1, c2
        self.s1, self.s2 = s1, s2
        self.name   = f"{player}{move_id}"

    def other(self, cell: int) -> int:
        return self.c2 if cell == self.c1 else self.c1

    def get_subcell(self, cell: int) -> int:
        return self.s1 if cell == self.c1 else self.s2


class QuantumGame:
    def __init__(self):
        # quantum[i] = 在格子 i 的量子棋子列表
        self.quantum:   dict[int, list[QuantumPiece]] = {i: [] for i in range(9)}
        # classical[i] = 塌縮到格子 i 的棋子
        self.classical: dict[int, QuantumPiece]       = {}
        # 糾纏圖鄰接表
        self.adj:       dict[int, list[QuantumPiece]] = {i: [] for i in range(9)}
        # 所有棋子 id → piece
        self.pieces:    dict[int, QuantumPiece]       = {}

        self.move_count     = 0
        self.status         = GameStatus.PLAYING
        self.current_player = "X"
        self.pending        : Optional[dict] = None   # 待塌縮資訊
        self.winner         : Optional[str]  = None
        self.winning_lines  : list           = []
        self.history        : list           = []

    # ── 放量子棋子 ────────────────────────────────────────────────────────────

    def place(self, c1: int, s1: int, c2: int, s2: int) -> dict:
        if self.status != GameStatus.PLAYING:
            return {"ok": False, "error": "不是放棋子的時機"}
        if c1 == c2:
            non_classical = [i for i in range(9) if i not in self.classical]
            if len(non_classical) == 1 and non_classical[0] == c1:
                self.move_count += 1
                p = QuantumPiece(self.move_count, self.current_player, c1, s1, c1, s2)
                self.pieces[p.id] = p
                self.classical[c1] = p
                self.history.append({
                    "type": "place_classical",
                    "move": self.move_count,
                    "piece": p.name,
                    "cell": c1
                })
                game_over, winner, lines = self._check_winner()
                if game_over:
                    self.status = GameStatus.FINISHED
                    self.winner = winner
                    self.winning_lines = lines
                    return {"ok": True, "cycle": False, "game_over": True}
                self.current_player = "O" if self.current_player == "X" else "X"
                return {"ok": True, "cycle": False}
            else:
                return {"ok": False, "error": "兩格不能相同"}
        if not (0 <= c1 <= 8 and 0 <= c2 <= 8):
            return {"ok": False, "error": "格子編號無效"}
        if not (0 <= s1 <= 8 and 0 <= s2 <= 8):
            return {"ok": False, "error": "小格編號無效"}
        if c1 in self.classical or c2 in self.classical:
            return {"ok": False, "error": "已塌縮的格子不能放棋"}
            
        for p in self.quantum[c1]:
            if p.get_subcell(c1) == s1:
                return {"ok": False, "error": f"格子 {c1} 的小格 {s1} 已有棋子"}
        for p in self.quantum[c2]:
            if p.get_subcell(c2) == s2:
                return {"ok": False, "error": f"格子 {c2} 的小格 {s2} 已有棋子"}

        path_pieces = self._creates_cycle(c1, c2)

        self.move_count += 1
        p = QuantumPiece(self.move_count, self.current_player, c1, s1, c2, s2)
        self.pieces[p.id] = p
        self.quantum[c1].append(p)
        self.quantum[c2].append(p)
        self.adj[c1].append(p)
        self.adj[c2].append(p)
        
        self.history.append({
            "type": "place",
            "move": self.move_count,
            "piece": p.name,
            "c1": c1,
            "c2": c2
        })

        if path_pieces is not None:
            cycle_pieces = path_pieces + [p.id]
            cycle_cells = list({self.pieces[pid].c1 for pid in cycle_pieces} | {self.pieces[pid].c2 for pid in cycle_pieces})
            other = "O" if self.current_player == "X" else "X"
            self.pending = {"piece_id": p.id, "c1": c1, "c2": c2,
                            "choosing_player": other,
                            "cycle_pieces": cycle_pieces,
                            "cycle_cells": cycle_cells}
            self.status = GameStatus.COLLAPSE
            return {"ok": True, "cycle": True, "piece_name": p.name,
                    "choosing_player": other, "c1": c1, "c2": c2}

        self.current_player = "O" if self.current_player == "X" else "X"
        return {"ok": True, "cycle": False}

    # ── 解決塌縮 ──────────────────────────────────────────────────────────────

    def resolve(self, piece_id: int, chosen_cell: int) -> dict:
        if self.status != GameStatus.COLLAPSE:
            return {"ok": False, "error": "沒有待塌縮的棋子"}
        pd = self.pending
        cycle_pieces = pd.get("cycle_pieces", [pd["piece_id"]])
        if piece_id not in cycle_pieces:
            return {"ok": False, "error": "只能選擇循環糾纏內的棋子"}
            
        piece = self.pieces.get(piece_id)
        if piece is None:
            return {"ok": False, "error": "棋子不存在"}
            
        if chosen_cell not in (piece.c1, piece.c2):
            return {"ok": False, "error": "該棋子不在所選的格子內"}

        self.history.append({
            "type": "resolve",
            "chooser": pd['choosing_player'],
            "piece": piece.name,
            "cell": chosen_cell
        })

        events: list[dict] = []
        self._cascade(piece, chosen_cell, events)
        self.pending = None

        game_over, winner, lines = self._check_winner()
        if game_over:
            self.status        = GameStatus.FINISHED
            self.winner        = winner
            self.winning_lines = lines
            return {"ok": True, "events": events, "game_over": True,
                    "winner": winner, "winning_lines": lines}

        self.current_player = "O" if self.current_player == "X" else "X"
        self.status         = GameStatus.PLAYING
        return {"ok": True, "events": events, "game_over": False}

    # ── BFS 環偵測 ────────────────────────────────────────────────────────────

    def _creates_cycle(self, c1: int, c2: int) -> Optional[list[int]]:
        visited = {c1: None}
        q = deque([c1])
        while q:
            node = q.popleft()
            if node == c2:
                path = []
                curr = node
                while curr != c1:
                    p_node, p_id = visited[curr]
                    path.append(p_id)
                    curr = p_node
                return path
            for piece in self.adj[node]:
                nb = piece.other(node)
                if nb not in visited:
                    visited[nb] = (node, piece.id)
                    q.append(nb)
        return None

    # ── BFS 級聯塌縮 ──────────────────────────────────────────────────────────

    def _cascade(self, piece: QuantumPiece, dest: int, events: list):
        queue     = deque([(piece, dest)])
        processed = set()

        while queue:
            p, d = queue.popleft()
            if p.id in processed:
                continue
            processed.add(p.id)

            if d in self.classical:
                d = p.other(d)
                if d in self.classical:
                    continue

            # 從圖和量子列表中移除
            for c in (p.c1, p.c2):
                if p in self.adj[c]:    self.adj[c].remove(p)
                if p in self.quantum[c]: self.quantum[c].remove(p)
            self.pieces.pop(p.id, None)

            # 寫入傳統棋子
            self.classical[d] = p
            events.append({"name": p.name, "player": p.player, "cell": d})

            # 級聯：d 格上剩餘的量子棋子往另一端
            for other in list(self.quantum.get(d, [])):
                if other.id not in processed:
                    queue.append((other, other.other(d)))

    # ── 勝負判斷 ──────────────────────────────────────────────────────────────

    def _check_winner(self):
        x_lines, o_lines = [], []
        for line in WIN_LINES:
            if all(c in self.classical for c in line):
                ps = [self.classical[c].player for c in line]
                if ps[0] == ps[1] == ps[2]:
                    (x_lines if ps[0] == "X" else o_lines).append(line)

        if x_lines and o_lines:
            # 同時連線 → 下標最大值較小的玩家優先獲勝
            x_max = max(self.classical[c].id for l in x_lines for c in l)
            o_max = max(self.classical[c].id for l in o_lines for c in l)
            w = "X" if x_max < o_max else "O"
            return True, w, x_lines + o_lines

        if x_lines:
            return True, "X", x_lines
        if o_lines:
            return True, "O", o_lines
        if all(c in self.classical for c in range(9)):
            x_ids = [self.classical[c].id for c in range(9) if self.classical[c].player == "X"]
            o_ids = [self.classical[c].id for c in range(9) if self.classical[c].player == "O"]
            x_max = max(x_ids) if x_ids else 0
            o_max = max(o_ids) if o_ids else 0
            w = "X" if x_max < o_max else "O"
            return True, w, []
        return False, None, []

    # ── 狀態序列化 ────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        cells = []
        for i in range(9):
            if i in self.classical:
                p = self.classical[i]
                cells.append({"type": "classical", "player": p.player,
                               "id": p.id, "name": p.name, "quantum": []})
            else:
                marks = { s: None for s in range(9) }
                for q in self.quantum[i]:
                    marks[q.get_subcell(i)] = {
                        "name": q.name, "player": q.player, "id": q.id,
                        "partner": q.other(i),
                        "c1": q.c1, "s1": q.s1, "c2": q.c2, "s2": q.s2
                    }
                cells.append({
                    "type": "quantum",
                    "player": None,
                    "quantum": [marks[s] for s in range(9)],
                })
        return {
            "cells":          cells,
            "current_player": self.current_player,
            "move_count":     self.move_count,
            "status":         self.status.value,
            "pending":        self.pending,
            "winner":         self.winner,
            "winning_lines":  self.winning_lines,
            "history":        self.history,
        }

    def restore_state(self, st: dict):
        self.quantum = {i: [] for i in range(9)}
        self.classical = {}
        self.adj = {i: [] for i in range(9)}
        self.pieces = {}

        for i, c in enumerate(st["cells"]):
            if c["type"] == "classical":
                p = QuantumPiece(c["id"], c["player"], i, 0, i, 0)
                self.classical[i] = p
                self.pieces[p.id] = p
            else:
                for s, m in enumerate(c["quantum"]):
                    if m:
                        p = QuantumPiece(m["id"], m["player"], m["c1"], m["s1"], m["c2"], m["s2"])
                        self.quantum[i].append(p)
                        self.adj[i].append(p)
                        self.pieces[p.id] = p

        self.move_count = st["move_count"]
        self.status = GameStatus(st["status"])
        self.current_player = st["current_player"]
        self.pending = st.get("pending")
        self.winner = st.get("winner")
        self.winning_lines = st.get("winning_lines", [])
        self.history = st.get("history", [])
