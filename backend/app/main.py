"""FastAPI server for Whisk.

Server responsibilities:
- Accept move reservations for each player.
- Reveal/apply both moves only when both players have moved (commit).
- Keep game-over state once a player reaches 50+ points.
- Broadcast authoritative state snapshots to clients.
"""

from __future__ import annotations

import asyncio
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
from .agents.human_adapter import HumanVsAgentSession

# -------------------------------------------------------------------
MODE_REMOTE = "remote"
MODE_LOCAL = "local"
MODE_HUMAN_VS_BOT = "human_vs_bot"
MODE_BOT_VS_BOT = "bot_vs_bot"
LEGACY_MODE_BOT = "bot"
VALID_MODES = {MODE_REMOTE, MODE_LOCAL, MODE_HUMAN_VS_BOT, MODE_BOT_VS_BOT}


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
        if self.bot_task is not None and not self.bot_task.done():
            self.bot_task.cancel()
        self.bot_task = None
        self.state = GameState()
        self.game_over = False
        self.local_next_mark = Mark.O
        if self.mode in (MODE_HUMAN_VS_BOT, MODE_BOT_VS_BOT):
            self.bot_session = HumanVsAgentSession(seed=self.bot_seed)

    def reset(self) -> None:
        if getattr(self, "bot_task", None) is not None and not self.bot_task.done():
            self.bot_task.cancel()
        self.state = GameState()
        self.players: Dict[Mark, PlayerConn] = {}
        self.ws_to_mark: Dict[str, Mark] = {}
        self.lobby_clients: Dict[str, WebSocket] = {}
        self.mode: Optional[str] = None
        self.game_over = False
        self.local_next_mark = Mark.O
        self.game_id = str(uuid.uuid4())
        self.reset_on_next_join = False
        self.bot_name = "WhiskBot"
        self.bot_seed = 0
        self.bot_session: Optional[HumanVsAgentSession] = None
        self.bot_task: Optional[asyncio.Task] = None
        self.bot_turn_delay_ms = 600

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
            "X": (
                manager.players[Mark.X].name
                if Mark.X in manager.players
                else (
                    manager.bot_name
                    if manager.mode in (MODE_HUMAN_VS_BOT, MODE_BOT_VS_BOT) and Mark.O in manager.players
                    else None
                )
            ),
        },
        "game_over": manager.game_over,
        "refresh": refresh,
    }
    highlight_coords = highlight_coords_for_viewer(manager.state, viewer_mark)
    payload["highlight"] = [
        {"row": r, "col": c} for r, c in sorted(highlight_coords)
    ]
    await to_ws.send_text(json.dumps(payload))


async def send_lobby(to_ws: WebSocket) -> None:
    """Send pre-join lobby info so clients can shape join UI."""
    await to_ws.send_text(json.dumps({
        "type": "lobby",
        "mode": manager.mode,
        "players": {
            "O": manager.players[Mark.O].name if Mark.O in manager.players else None,
            "X": (
                manager.players[Mark.X].name
                if Mark.X in manager.players
                else (
                    manager.bot_name
                    if manager.mode in (MODE_HUMAN_VS_BOT, MODE_BOT_VS_BOT) and Mark.O in manager.players
                    else None
                )
            ),
        },
    }))


async def broadcast_lobby() -> None:
    """Push lobby updates to clients that are connected but not yet joined."""
    for ws in list(manager.lobby_clients.values()):
        try:
            await send_lobby(ws)
        except Exception:
            # stale sockets are cleaned up on disconnect; ignore send races
            pass


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


def parse_bot_seed(raw: object, default: int = 0) -> int:
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def parse_bot_tick_ms(default: int = 600) -> int:
    import os

    raw = os.getenv("WHISK_BVB_TICK_MS")
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(20, value)


def normalize_mode(raw: object) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    mode = raw.strip()
    if mode == LEGACY_MODE_BOT:
        return MODE_HUMAN_VS_BOT
    if mode in VALID_MODES:
        return mode
    return None


async def send_bot_explanation_to_o(mark: Mark, decision: object) -> None:
    if Mark.O not in manager.players:
        return
    await manager.send(
        manager.players[Mark.O].ws,
        {
            "type": "bot_explanation",
            "turn": manager.state.turn,
            "mark": mark.value,
            "source": decision.source,
            "chosen": {"row": decision.row, "col": decision.col},
            "candidates": [
                {"row": c.row, "col": c.col, "score": c.score}
                for c in decision.candidates
            ],
        },
    )


async def maybe_start_bot_vs_bot_loop() -> None:
    if manager.mode != MODE_BOT_VS_BOT:
        return
    if Mark.O not in manager.players:
        return
    if manager.game_over:
        return
    if manager.bot_task is not None and not manager.bot_task.done():
        return

    if manager.bot_session is None:
        manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
    manager.bot_turn_delay_ms = parse_bot_tick_ms()
    game_id = manager.game_id
    manager.bot_task = asyncio.create_task(run_bot_vs_bot_loop(game_id))


async def run_bot_vs_bot_loop(game_id: str) -> None:
    while True:
        await asyncio.sleep(manager.bot_turn_delay_ms / 1000.0)

        if game_id != manager.game_id:
            return
        if manager.mode != MODE_BOT_VS_BOT:
            return
        if manager.game_over:
            return
        if Mark.O not in manager.players:
            return

        try:
            if manager.bot_session is None:
                manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
            o_decision = manager.bot_session.choose_decision(manager.state, Mark.O)
            apply_move(manager.state, Mark.O, o_decision.row, o_decision.col)
            x_decision = manager.bot_session.choose_decision(manager.state, Mark.X)
            apply_move(manager.state, Mark.X, x_decision.row, x_decision.col)
            summary = commit_turn(manager.state)
        except ValueError:
            # If one chosen move became illegal due to a race, retry next tick.
            continue
        except asyncio.CancelledError:
            return

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
            return


# -------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    ws_id = str(uuid.uuid4())
    manager.lobby_clients[ws_id] = ws
    await send_lobby(ws)

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            # --------------- JOIN ---------------
            if mtype == "join":
                name = (msg.get("name") or "").strip() or "Player"
                requested_bot_seed = parse_bot_seed(msg.get("bot_seed"), manager.bot_seed)
                requested_mode = normalize_mode(msg.get("mode"))

                manager.prune_disconnected()

                if manager.is_full():
                    await manager.send(ws, {
                        "type": "error",
                        "message": "A game is already in progress with two players.",
                    })
                    continue
                if manager.mode in (MODE_HUMAN_VS_BOT, MODE_BOT_VS_BOT) and Mark.O in manager.players:
                    await manager.send(ws, {
                        "type": "error",
                        "message": "A computer game is already in progress.",
                    })
                    continue

                if manager.reset_on_next_join:
                    manager.reset_game_state_only()
                    manager.reset_on_next_join = False

                mark = manager.assign_mark()

                # First player (O) may request a mode (or leave None)
                if mark == Mark.O:
                    if requested_mode in VALID_MODES:
                        manager.mode = requested_mode
                    if manager.mode in (MODE_HUMAN_VS_BOT, MODE_BOT_VS_BOT):
                        manager.bot_seed = requested_bot_seed
                        manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
                else:
                    # If current host is in local mode and joiner chooses local, hand off
                    # local ownership so the joiner can run their own local game.
                    if manager.mode == MODE_LOCAL and requested_mode == MODE_LOCAL:
                        # Move the existing local owner back to lobby (same socket stays connected).
                        for old_ws_id, old_mark in list(manager.ws_to_mark.items()):
                            if old_mark == Mark.O and Mark.O in manager.players:
                                manager.lobby_clients[old_ws_id] = manager.players[Mark.O].ws
                                manager.ws_to_mark.pop(old_ws_id, None)
                        manager.players.pop(Mark.O, None)
                        manager.reset_game_state_only()
                        manager.mode = MODE_LOCAL
                        mark = Mark.O

                    # Local mode is single-device; block a second join immediately.
                    if manager.mode == MODE_LOCAL and Mark.O in manager.players:
                        host_name = manager.players[Mark.O].name if Mark.O in manager.players else "Player 1"
                        await manager.send(ws, {
                            "type": "error",
                            "message": f"{host_name} is playing Whisk in local mode so you can't join at this time.",
                        })
                        continue
                    # If second joiner selected a conflicting mode, fail fast with a clear message.
                    if (
                        requested_mode in VALID_MODES
                        and manager.mode in VALID_MODES
                        and requested_mode != manager.mode
                    ):
                        await manager.send(ws, {
                            "type": "error",
                            "message": f"Mode mismatch. This game is set to {manager.mode}.",
                        })
                        continue

                manager.players[mark] = PlayerConn(ws=ws, name=name, mark=mark)
                manager.ws_to_mark[ws_id] = mark
                manager.lobby_clients.pop(ws_id, None)

                await manager.send(ws, {"type": "joined", "mark": mark.value})
                await broadcast_state()
                await broadcast_lobby()
                if manager.mode == MODE_BOT_VS_BOT and mark == Mark.O:
                    await maybe_start_bot_vs_bot_loop()

                if mark == Mark.O and manager.mode is None:
                    await manager.send(
                        ws,
                        {
                            "type": "need_mode",
                            "message": "Choose Local, Remote, Play Against Computer, or Watch Computer Self-Play.",
                        },
                    )

            # --------------- LOBBY ---------------
            elif mtype == "lobby":
                await send_lobby(ws)

            # --------------- SET MODE ---------------
            elif mtype == "set_mode":
                mark = manager.ws_to_mark.get(ws_id)
                if mark is None:
                    await manager.send(ws, {"type": "error", "message": "You must join first."})
                    continue
                if mark != Mark.O:
                    await manager.send(ws, {"type": "error", "message": "Only O can set mode."})
                    continue

                mode = normalize_mode(msg.get("mode"))
                if mode not in VALID_MODES:
                    await manager.send(
                        ws,
                        {
                            "type": "error",
                            "message": "Mode must be local, remote, human_vs_bot, or bot_vs_bot.",
                        },
                    )
                    continue

                if manager.bot_task is not None and not manager.bot_task.done() and mode != MODE_BOT_VS_BOT:
                    manager.bot_task.cancel()
                    manager.bot_task = None
                manager.mode = mode
                if mode in (MODE_HUMAN_VS_BOT, MODE_BOT_VS_BOT):
                    manager.bot_seed = parse_bot_seed(msg.get("bot_seed"), manager.bot_seed)
                    manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
                await manager.broadcast({"type": "mode", "mode": mode})
                if mode == MODE_LOCAL:
                    manager.local_next_mark = Mark.O
                await broadcast_state()
                await broadcast_lobby()
                if mode == MODE_BOT_VS_BOT:
                    await maybe_start_bot_vs_bot_loop()

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

                if manager.mode == MODE_LOCAL:
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

                if manager.mode == MODE_HUMAN_VS_BOT:
                    if mark != Mark.O:
                        await manager.send(ws, {"type": "error", "message": "Only Player 1 can move in Play Against Computer mode."})
                        continue

                    try:
                        apply_move(manager.state, Mark.O, row, col)
                        if manager.bot_session is None:
                            manager.bot_session = HumanVsAgentSession()
                        bot_decision = manager.bot_session.choose_decision(manager.state, Mark.X)
                        bot_row, bot_col = bot_decision.row, bot_decision.col
                        apply_move(manager.state, Mark.X, bot_row, bot_col)
                        summary = commit_turn(manager.state)
                    except ValueError as e:
                        await manager.send(ws, {"type": "invalid_move", "message": str(e)})
                        continue

                    await broadcast_state(refresh=True)
                    await send_bot_explanation_to_o(Mark.X, bot_decision)
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

                if manager.mode == MODE_BOT_VS_BOT:
                    await manager.send(
                        ws,
                        {
                            "type": "error",
                            "message": "Moves are disabled while watching computer self-play.",
                        },
                    )
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
                if manager.mode == MODE_BOT_VS_BOT:
                    await maybe_start_bot_vs_bot_loop()

            else:
                await manager.send(ws, {"type": "error", "message": f"Unknown message type: {mtype}"})

    except WebSocketDisconnect:
        manager.lobby_clients.pop(ws_id, None)
        mark = manager.ws_to_mark.pop(ws_id, None)
        if manager.bot_task is not None and not manager.bot_task.done():
            manager.bot_task.cancel()
            manager.bot_task = None
        if mark and mark in manager.players:
            del manager.players[mark]
            manager.state.pending[mark] = None
            await broadcast_state()
            await broadcast_lobby()
            manager.reset_on_next_join = True

        if not manager.players:
            manager.reset()
