"""FastAPI server for Whisk.

Server responsibilities:
- Accept move reservations for each player.
- Reveal/apply both moves only when both players have moved (commit).
- Keep game-over state once a player reaches 50+ points.
- Broadcast authoritative state snapshots to clients.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from .game import (
    GameState,
    Mark,
    apply_move,
    commit_single_move,
    commit_turn,
    highlight_coords_for_viewer,
    pieces_for_client,
    ready_to_commit,
    scores_for_client,
)

# -------------------------------------------------------------------

@dataclass
class PlayerConn:
    ws: WebSocket
    name: str
    mark: Mark


class GameManager:
    def __init__(self) -> None:
        self.reset()

    def reset_game_state_only(self) -> None:
        """Clear board/scores/turn/pending but keep players + mode."""
        self.state = GameState()
        self.game_over = False
        self.local_next_mark = Mark.O

    def reset(self) -> None:
        self.state = GameState()
        self.players: Dict[Mark, PlayerConn] = {}
        self.ws_to_mark: Dict[str, Mark] = {}
        self.mode: Optional[str] = None  # 'remote' or 'local'
        self.game_over = False
        self.local_next_mark = Mark.O
        self.game_id = str(uuid.uuid4())
        self.reset_on_next_join = False

    def is_full(self) -> bool:
        return Mark.O in self.players and Mark.X in self.players

    async def send(self, ws: WebSocket, payload: dict) -> None:
        await ws.send_text(json.dumps(payload))

    async def broadcast(self, payload: dict) -> None:
        for p in self.players.values():
            await self.send(p.ws, payload)

    def assign_mark(self) -> Mark:
        """First joiner is O, second is X."""
        if Mark.O not in self.players:
            return Mark.O
        return Mark.X

    def prune_disconnected(self) -> None:
        """Remove players whose websocket is no longer connected (crash/refresh ghosts)."""
        for mark, p in list(self.players.items()):
            if p.ws.client_state != WebSocketState.CONNECTED:
                del self.players[mark]
                self.state.pending[mark] = None


# -------------------------------------------------------------------

app = FastAPI(title="Whisk")
manager = GameManager()
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

# Serve frontend assets from repo frontend directory.
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index() -> HTMLResponse:
    with open(FRONTEND_DIR / "index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


async def send_state(to_ws: WebSocket, viewer_mark: Optional[Mark] = None, refresh: bool = False) -> None:
    """Send a full state snapshot to one client."""
    view_scores = scores_for_client(manager.state, viewer_mark)
    payload = {
        "type": "state",
        "turn": manager.state.turn,
        "pieces": pieces_for_client(manager.state, viewer_mark),
        "scores": {
            "O": view_scores[Mark.O],
            "X": view_scores[Mark.X],
        },
        # frontend uses these booleans for messaging
        "pending": {
            "O": manager.state.pending[Mark.O] is not None,
            "X": manager.state.pending[Mark.X] is not None,
        },
        "mode": manager.mode,
        "local_next_mark": manager.local_next_mark.value if manager.mode == "local" else None,
        "players": {
            "O": manager.players[Mark.O].name if Mark.O in manager.players else None,
            "X": manager.players[Mark.X].name if Mark.X in manager.players else None,
        },
        "game_over": manager.game_over,
        "refresh": refresh,
    }
    highlight_coords = highlight_coords_for_viewer(manager.state, viewer_mark)
    payload["highlight"] = [
        {"row": r, "col": c} for r, c in sorted(highlight_coords)
    ]
    await to_ws.send_text(json.dumps(payload))


async def broadcast_state(refresh: bool = False) -> None:
    """Send a full state snapshot to all connected clients."""
    for p in manager.players.values():
        await send_state(p.ws, p.mark, refresh=refresh)


def game_over_message(winner: Optional[str]) -> str:
    if winner == "O":
        return "Game over. O wins!"
    if winner == "X":
        return "Game over. X wins!"
    return "Game over. It's a tie!"


# -------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    ws_id = str(uuid.uuid4())

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            # --------------- JOIN ---------------
            if mtype == "join":
                name = (msg.get("name") or "").strip() or "Player"
                requested_mode = msg.get("mode")
                if requested_mode not in ("remote", "local"):
                    requested_mode = None

                manager.prune_disconnected()

                if manager.is_full():
                    await manager.send(ws, {
                        "type": "error",
                        "message": "A game is already in progress with two players.",
                    })
                    continue

                if manager.reset_on_next_join:
                    manager.reset_game_state_only()
                    manager.reset_on_next_join = False

                mark = manager.assign_mark()

                # First player (O) may request a mode (or leave None)
                if mark == Mark.O:
                    if requested_mode in ("remote", "local"):
                        manager.mode = requested_mode
                else:
                    # Local mode is single-device; block a second join immediately.
                    if manager.mode == "local":
                        await manager.send(ws, {
                            "type": "error",
                            "message": "This game is in Local mode. A second player cannot join.",
                        })
                        continue
                    # If second joiner selected a conflicting mode, fail fast with a clear message.
                    if (
                        requested_mode in ("remote", "local")
                        and manager.mode in ("remote", "local")
                        and requested_mode != manager.mode
                    ):
                        await manager.send(ws, {
                            "type": "error",
                            "message": f"Mode mismatch. This game is set to {manager.mode}.",
                        })
                        continue

                manager.players[mark] = PlayerConn(ws=ws, name=name, mark=mark)
                manager.ws_to_mark[ws_id] = mark

                await manager.send(ws, {"type": "joined", "mark": mark.value})
                await broadcast_state()

                if mark == Mark.O and manager.mode is None:
                    await manager.send(ws, {"type": "need_mode", "message": "Choose Local or Remote."})

            # --------------- SET MODE ---------------
            elif mtype == "set_mode":
                mark = manager.ws_to_mark.get(ws_id)
                if mark is None:
                    await manager.send(ws, {"type": "error", "message": "You must join first."})
                    continue
                if mark != Mark.O:
                    await manager.send(ws, {"type": "error", "message": "Only O can set mode."})
                    continue

                mode = msg.get("mode")
                if mode not in ("remote", "local"):
                    await manager.send(ws, {"type": "error", "message": "Mode must be local or remote."})
                    continue

                manager.mode = mode
                await manager.broadcast({"type": "mode", "mode": mode})
                if mode == "local":
                    manager.local_next_mark = Mark.O
                await broadcast_state()

            # --------------- MOVE ---------------
            elif mtype == "move":
                mark = manager.ws_to_mark.get(ws_id)
                if mark is None or mark not in manager.players:
                    await manager.send(ws, {"type": "error", "message": "You must join first."})
                    continue

                if manager.game_over:
                    await manager.send(ws, {"type": "error", "message": "Game is over. Start a new game."})
                    continue

                row = int(msg.get("row"))
                col = int(msg.get("col"))

                if manager.mode == "local":
                    if mark != Mark.O:
                        await manager.send(ws, {"type": "error", "message": "Only Player 1 can move in local mode."})
                        continue
                    moving_mark = manager.local_next_mark
                    try:
                        apply_move(manager.state, moving_mark, row, col)
                        summary = commit_single_move(manager.state, moving_mark)
                    except ValueError as e:
                        await manager.send(ws, {"type": "invalid_move", "message": str(e)})
                        continue

                    manager.local_next_mark = Mark.X if moving_mark == Mark.O else Mark.O
                    await broadcast_state(refresh=True)
                    added = summary.get("added", {})
                    add_o = int(added.get("O", 0)) if isinstance(added, dict) else 0
                    add_x = int(added.get("X", 0)) if isinstance(added, dict) else 0
                    if add_o > 0 and Mark.O in manager.players:
                        await manager.send(manager.players[Mark.O].ws, {"type": "score_event", "mark": "O", "added": add_o})
                    if add_x > 0 and Mark.O in manager.players:
                        await manager.send(manager.players[Mark.O].ws, {"type": "score_event", "mark": "X", "added": add_x})
                    await manager.broadcast({"type": "turn_committed", "turn": manager.state.turn})
                    if summary["done"]:
                        manager.game_over = True
                        winner = summary.get("winner")
                        winner_mark = winner if isinstance(winner, str) else None
                        await manager.broadcast({
                            "type": "game_over",
                            "message": game_over_message(winner_mark),
                        })
                    continue

                try:
                    apply_move(manager.state, mark, row, col)
                except ValueError as e:
                    await manager.send(ws, {"type": "invalid_move", "message": str(e)})
                    continue

                # Show the mover their private preview immediately.
                await send_state(ws, mark)

                # Update both players' "who has moved?" message logic.
                await manager.broadcast({
                    "type": "pending_flags",
                    "pending": {
                        "O": manager.state.pending[Mark.O] is not None,
                        "X": manager.state.pending[Mark.X] is not None,
                    },
                })

                # Reveal/apply when both players have reserved a move.
                if ready_to_commit(manager.state):
                    summary = commit_turn(manager.state)
                    await broadcast_state(refresh=True)
                    added = summary.get("added", {})
                    add_o = int(added.get("O", 0)) if isinstance(added, dict) else 0
                    add_x = int(added.get("X", 0)) if isinstance(added, dict) else 0
                    if add_o > 0 and Mark.O in manager.players:
                        await manager.send(manager.players[Mark.O].ws, {"type": "score_event", "mark": "O", "added": add_o})
                    if add_x > 0 and Mark.X in manager.players:
                        await manager.send(manager.players[Mark.X].ws, {"type": "score_event", "mark": "X", "added": add_x})
                    await manager.broadcast({"type": "turn_committed", "turn": manager.state.turn})
                    if summary["done"]:
                        manager.game_over = True
                        winner = summary.get("winner")
                        winner_mark = winner if isinstance(winner, str) else None
                        await manager.broadcast({
                            "type": "game_over",
                            "message": game_over_message(winner_mark),
                        })

            # --------------- NEW GAME ---------------
            elif mtype == "new_game":
                mark = manager.ws_to_mark.get(ws_id)
                if mark is None or mark not in manager.players:
                    await manager.send(ws, {"type": "error", "message": "You must join first."})
                    continue

                resetter_name = manager.players[mark].name
                manager.reset_game_state_only()
                manager.reset_on_next_join = False

                # Inform both players
                for m, p in manager.players.items():
                    if m == mark:
                        await manager.send(p.ws, {"type": "info", "message": "You have reset the game."})
                    else:
                        await manager.send(p.ws, {"type": "info", "message": f"{resetter_name} has reset the game."})

                await broadcast_state()

            else:
                await manager.send(ws, {"type": "error", "message": f"Unknown message type: {mtype}"})

    except WebSocketDisconnect:
        mark = manager.ws_to_mark.pop(ws_id, None)
        if mark and mark in manager.players:
            del manager.players[mark]
            manager.state.pending[mark] = None
            await broadcast_state()
            manager.reset_on_next_join = True

        if not manager.players:
            manager.reset()
