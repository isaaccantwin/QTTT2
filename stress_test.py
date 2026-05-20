"""
stress_test.py — Quantum Tic-Tac-Toe Automated Stress Test
==========================================================
Simulates N full PvP games between two virtual clients,
exercising place/resolve/cycle/last-cell edge cases.

Usage:
    python stress_test.py                      # 50 games against localhost:8000
    python stress_test.py --games 200          # 200 games
    python stress_test.py --base-url http://example.com  # custom server
"""

import argparse
import random
import sys
import time
import traceback

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package is required.  pip install requests")
    sys.exit(1)


# ── Helpers ─────────────────────────────────────────────────────────────────

def make_client_id():
    return f"stress_{random.randint(100000, 999999)}"


def get_valid_spots(state):
    """Return list of (cell_idx, subcell_idx) that are open for placement."""
    spots = []
    for i, cell in enumerate(state["cells"]):
        if cell["type"] == "classical":
            continue
        for s, mark in enumerate(cell["quantum"]):
            if mark is None:
                spots.append((i, s))
    return spots


def get_non_classical_count(state):
    return sum(1 for c in state["cells"] if c["type"] != "classical")


# ── Main Test Loop ──────────────────────────────────────────────────────────

def run_one_game(base_url, game_num, verbose=False):
    """Run a single full game. Returns (success: bool, error_msg: str|None)."""
    cid_x = make_client_id()
    cid_o = make_client_id()

    # 1. Create room
    r = requests.post(f"{base_url}/api/create_room", json={
        "mode": "pvp", "client_id": cid_x
    })
    if r.status_code != 200:
        return False, f"create_room failed: {r.status_code} {r.text}"
    data = r.json()
    room_id = data["room_id"]

    # 2. Join room
    r = requests.post(f"{base_url}/api/join_room", json={
        "room_id": room_id, "client_id": cid_o
    })
    if r.status_code != 200:
        return False, f"join_room failed: {r.status_code} {r.text}"

    # 3. Play loop
    max_moves = 100  # safety limit
    move = 0
    while move < max_moves:
        # Fetch current state
        r = requests.get(f"{base_url}/api/state/{room_id}")
        if r.status_code != 200:
            return False, f"get_state failed (move {move}): {r.status_code} {r.text}"
        state = r.json()

        if state["status"] == "finished":
            if verbose:
                print(f"  Game #{game_num}: finished after {move} API calls, "
                      f"winner={state.get('winner')}, moves={state['move_count']}")
            return True, None

        if state["status"] == "collapse":
            # Resolve: pick a random cycle piece
            pending = state.get("pending")
            if not pending:
                return False, f"COLLAPSE but no pending data (move {move})"

            chooser = pending["choosing_player"]
            cid = cid_x if chooser == "X" else cid_o
            cycle_pieces = pending.get("cycle_pieces", [pending["piece_id"]])

            # Find a valid piece + cell to resolve
            resolved = False
            random.shuffle(cycle_pieces)
            for pid in cycle_pieces:
                # Find this piece in the board to know which cells it spans
                for i, cell in enumerate(state["cells"]):
                    if cell["type"] != "classical":
                        for s, mark in enumerate(cell["quantum"]):
                            if mark and mark["id"] == pid:
                                # Try resolving to this cell
                                r = requests.post(
                                    f"{base_url}/api/resolve/{room_id}",
                                    json={
                                        "client_id": cid,
                                        "piece_id": pid,
                                        "chosen_cell": i
                                    }
                                )
                                if r.status_code == 500:
                                    return False, (
                                        f"500 on resolve (move {move}): "
                                        f"pid={pid}, cell={i}\n{r.text}"
                                    )
                                resp = r.json()
                                if resp.get("ok"):
                                    resolved = True
                                    break
                        if resolved:
                            break
                if resolved:
                    break

            if not resolved:
                return False, f"Could not resolve any cycle piece (move {move}), pieces={cycle_pieces}"
            move += 1
            continue

        if state["status"] == "playing":
            cp = state["current_player"]
            cid = cid_x if cp == "X" else cid_o
            spots = get_valid_spots(state)
            non_classical = get_non_classical_count(state)

            if len(spots) < 2:
                # Board is nearly full — might be stuck
                if len(spots) == 0:
                    return False, f"No valid spots but game not finished (move {move})"
                # Try c1 == c2 scenario
                if non_classical == 1:
                    c, s1 = spots[0]
                    # Find a second open subcell in the same cell
                    cell_data = state["cells"][c]
                    s2 = None
                    for ss in range(9):
                        if ss != s1 and cell_data["quantum"][ss] is None:
                            s2 = ss
                            break
                    if s2 is None:
                        return False, f"Only 1 spot in last cell, can't find s2 (move {move})"
                    r = requests.post(
                        f"{base_url}/api/place/{room_id}",
                        json={"client_id": cid, "c1": c, "s1": s1, "c2": c, "s2": s2}
                    )
                    if r.status_code == 500:
                        return False, f"500 on place c1==c2 (move {move}): {r.text}"
                    move += 1
                    continue
                else:
                    return False, f"<2 spots but {non_classical} non-classical cells (move {move})"

            # Normal placement: pick two spots in different cells
            random.shuffle(spots)
            c1, s1 = spots[0]
            # Find a spot in a different cell
            c2, s2 = None, None
            for sp in spots[1:]:
                if sp[0] != c1:
                    c2, s2 = sp
                    break

            if c2 is None:
                # All remaining spots are in the same cell — pick two from the same cell
                c2 = c1
                s2 = spots[1][1]

            r = requests.post(
                f"{base_url}/api/place/{room_id}",
                json={"client_id": cid, "c1": c1, "s1": s1, "c2": c2, "s2": s2}
            )
            if r.status_code == 500:
                return False, f"500 on place (move {move}): c1={c1},s1={s1},c2={c2},s2={s2}\n{r.text}"

            resp = r.json()
            if resp.get("error"):
                # Non-fatal: might be a subcell conflict due to race, retry
                if verbose:
                    print(f"  Place rejected (move {move}): {resp['error']}")
                # Don't increment move, just retry with fresh state
                continue

            move += 1
            continue

        return False, f"Unknown status: {state['status']} (move {move})"

    return False, f"Exceeded {max_moves} moves without finishing"


def main():
    parser = argparse.ArgumentParser(description="Quantum Tic-Tac-Toe Stress Test")
    parser.add_argument("--games", type=int, default=50, help="Number of games to simulate")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL")
    parser.add_argument("--verbose", action="store_true", help="Print per-game details")
    args = parser.parse_args()

    print(f"🧪 Quantum Tic-Tac-Toe Stress Test")
    print(f"   Server:  {args.base_url}")
    print(f"   Games:   {args.games}")
    print(f"{'─' * 60}")

    # Verify server is reachable
    try:
        r = requests.get(f"{args.base_url}/api/debug", timeout=5)
        if r.status_code != 200:
            print(f"❌ Server returned {r.status_code} on /api/debug")
            sys.exit(1)
        print(f"✅ Server reachable: {r.json()}")
    except requests.ConnectionError:
        print(f"❌ Cannot connect to {args.base_url}. Is the server running?")
        sys.exit(1)

    successes = 0
    failures = 0
    errors = []
    start_time = time.time()

    for i in range(1, args.games + 1):
        try:
            ok, err = run_one_game(args.base_url, i, verbose=args.verbose)
            if ok:
                successes += 1
                if not args.verbose and i % 10 == 0:
                    print(f"  ✅ {i}/{args.games} games completed...")
            else:
                failures += 1
                errors.append((i, err))
                print(f"  ❌ Game #{i}: {err}")
        except Exception as e:
            failures += 1
            tb = traceback.format_exc()
            errors.append((i, f"Exception: {e}\n{tb}"))
            print(f"  💥 Game #{i}: {e}")

    elapsed = time.time() - start_time
    print(f"{'─' * 60}")
    print(f"📊 Results:")
    print(f"   Total:     {args.games}")
    print(f"   Success:   {successes}")
    print(f"   Failures:  {failures}")
    print(f"   Time:      {elapsed:.1f}s ({elapsed / args.games:.2f}s/game)")

    if errors:
        print(f"\n🔴 Failure Details:")
        for game_num, err in errors:
            print(f"   Game #{game_num}: {err[:200]}")

    if failures == 0:
        print(f"\n🎉 All {args.games} games passed! No errors detected.")
        sys.exit(0)
    else:
        print(f"\n⚠️  {failures} game(s) had errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()
