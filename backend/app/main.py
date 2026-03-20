"""FastAPI server for Whisk.

Server responsibilities:
- Accept move reservations for each player.
- Reveal/apply both moves only when both players have moved (commit).
- Keep game-over state once a player reaches 50+ points.
- Broadcast authoritative state snapshots to clients.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import math
import os
import secrets
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse
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
from .agents.env import WhiskEnv
from .agents.encoding import ActionCodec, StateEncoder
from .agents.human_adapter import BotCandidate, BotDecision, HumanVsAgentSession

# -------------------------------------------------------------------
MODE_REMOTE = "remote"
MODE_LOCAL = "local"
MODE_HUMAN_VS_BOT = "human_vs_bot"
MODE_DEMO = "demo"
LEGACY_MODE_BOT = "bot"
VALID_MODES = {MODE_REMOTE, MODE_LOCAL, MODE_HUMAN_VS_BOT, MODE_DEMO}
BOT_SEED_MOD = 2_147_483_647
logger = logging.getLogger("whiskbot")


def fresh_bot_seed() -> int:
    return secrets.randbelow(BOT_SEED_MOD - 1) + 1


def advance_bot_seed(seed: int) -> int:
    nxt = (int(seed) + 1) % BOT_SEED_MOD
    return nxt if nxt != 0 else 1


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
        self.cancel_bot_move_task()
        self.bot_pending_decision = None
        self.bot_turn_nonce += 1
        self.clear_demo_move_log(reason="reset_game_state_only")
        self.state = GameState()
        self.game_over = False
        self.local_next_mark = Mark.O
        if self.mode in (MODE_HUMAN_VS_BOT, MODE_DEMO):
            self.bot_seed = advance_bot_seed(self.bot_seed)
            self.bot_session = HumanVsAgentSession(seed=self.bot_seed)
        else:
            self.bot_session = None

    def reset(self) -> None:
        if hasattr(self, "bot_move_task"):
            self.cancel_bot_move_task()
        if hasattr(self, "demo_move_log"):
            self.clear_demo_move_log(reason="reset")
        else:
            self.demo_move_log: List[str] = []
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
        self.bot_seed = fresh_bot_seed()
        self.bot_session: Optional[HumanVsAgentSession] = None
        self.bot_pending_decision: Optional[object] = None
        self.bot_move_task: Optional[asyncio.Task] = None
        self.bot_turn_nonce = 0
        self.local_player_names: Dict[Mark, str] = {
            Mark.O: "Player O",
            Mark.X: "Player X",
        }

    def clear_demo_move_log(self, *, reason: str) -> None:
        entry_count = len(self.demo_move_log)
        if entry_count > 0:
            logger.info("demo_move_log_cleared entries=%s reason=%s", entry_count, reason)
            print(f"demo_move_log_cleared entries={entry_count} reason={reason}", flush=True)
        self.demo_move_log.clear()

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

    def cancel_bot_move_task(self) -> None:
        if self.bot_move_task is not None and not self.bot_move_task.done():
            self.bot_move_task.cancel()
        self.bot_move_task = None


# -------------------------------------------------------------------

app = FastAPI(title="Whisk")
manager = GameManager()
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
ROOT_DIR = Path(__file__).resolve().parents[2]
INSTRUCTIONS_PATHS = {
    MODE_LOCAL: ROOT_DIR / "local instructions.txt",
    MODE_REMOTE: ROOT_DIR / "remote instructions.txt",
    MODE_HUMAN_VS_BOT: ROOT_DIR / "human_vs_bot.txt",
}

# Serve frontend assets from repo frontend directory.
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index() -> HTMLResponse:
    with open(FRONTEND_DIR / "index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/instructions/{mode}")
async def instructions(mode: str) -> PlainTextResponse:
    normalized = normalize_mode(mode)
    if normalized not in INSTRUCTIONS_PATHS:
        raise HTTPException(status_code=404, detail="Unknown mode")
    path = INSTRUCTIONS_PATHS[normalized]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Instructions file missing")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


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
        "local_next_mark": (
            manager.local_next_mark.value
            if manager.mode in (MODE_LOCAL, MODE_DEMO)
            else None
        ),
        "players": {
            "O": (
                manager.local_player_names.get(Mark.O)
                if manager.mode == MODE_LOCAL and Mark.O in manager.players
                else (manager.players[Mark.O].name if Mark.O in manager.players else None)
            ),
            "X": (
                manager.players[Mark.X].name
                if Mark.X in manager.players
                else (
                    manager.bot_name
                    if manager.mode in (MODE_HUMAN_VS_BOT, MODE_DEMO) and Mark.O in manager.players
                    else (
                        manager.local_player_names.get(Mark.X)
                        if manager.mode == MODE_LOCAL and Mark.O in manager.players
                        else None
                    )
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
            "O": (
                manager.local_player_names.get(Mark.O)
                if manager.mode == MODE_LOCAL and Mark.O in manager.players
                else (manager.players[Mark.O].name if Mark.O in manager.players else None)
            ),
            "X": (
                manager.players[Mark.X].name
                if Mark.X in manager.players
                else (
                    manager.bot_name
                    if manager.mode in (MODE_HUMAN_VS_BOT, MODE_DEMO) and Mark.O in manager.players
                    else (
                        manager.local_player_names.get(Mark.X)
                        if manager.mode == MODE_LOCAL and Mark.O in manager.players
                        else None
                    )
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


def evict_leftover_for_single_player_mode(host_ws_id: str) -> None:
    """Keep only the joining host tracked when starting a single-player session."""
    for stale_ws_id, mark in list(manager.ws_to_mark.items()):
        if stale_ws_id == host_ws_id:
            continue
        if mark == Mark.X:
            manager.ws_to_mark.pop(stale_ws_id, None)

    if Mark.X in manager.players:
        del manager.players[Mark.X]
        manager.state.pending[Mark.X] = None

    for lobby_ws_id in list(manager.lobby_clients.keys()):
        if lobby_ws_id != host_ws_id:
            manager.lobby_clients.pop(lobby_ws_id, None)


async def broadcast_state(refresh: bool = False) -> None:
    """Send a full state snapshot to all connected clients."""
    for p in manager.players.values():
        await send_state(p.ws, p.mark, refresh=refresh)


def pending_payload() -> Dict[str, bool]:
    return {
        "O": manager.state.pending[Mark.O] is not None,
        "X": manager.state.pending[Mark.X] is not None,
    }


async def broadcast_pending_flags() -> None:
    await manager.broadcast({"type": "pending_flags", "pending": pending_payload()})


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


def parse_player_name(raw: object, default: str) -> str:
    if not isinstance(raw, str):
        return default
    name = raw.strip()
    return name or default


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def _sample_action_from_scores(
    scores: Dict[int, float],
    *,
    rng,
    temperature: float,
    top_k: int,
) -> int:
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        raise RuntimeError("No candidate scores")
    shortlist = ranked[: min(len(ranked), max(1, top_k))]
    if len(shortlist) == 1:
        return shortlist[0][0]
    max_score = shortlist[0][1]
    temp = max(0.01, temperature)
    weights = [math.exp((score - max_score) / temp) for _, score in shortlist]
    total = sum(weights)
    if total <= 0:
        return rng.choice([action_id for action_id, _ in shortlist])
    target = rng.random() * total
    accum = 0.0
    for (action_id, _), weight in zip(shortlist, weights):
        accum += weight
        if accum >= target:
            return action_id
    return shortlist[-1][0]


def _demo_rebalance_decision(mark: Mark, base_decision: BotDecision) -> BotDecision:
    """Reduce demo-mode spatial collapse while still using model guidance."""
    if manager.bot_session is None:
        return base_decision

    env = WhiskEnv(mode="remote")
    env.state = deepcopy(manager.state)
    legal = env.legal_actions(mark)
    if not legal:
        return base_decision

    opponent = Mark.X if mark == Mark.O else Mark.O
    priors = None
    if manager.bot_session.model is not None:
        obs = StateEncoder.encode_observation(env.state, mark)
        priors, _ = manager.bot_session.model.predict(obs)

    # Tactical guardrails first: never sacrifice immediate points, and proactively block
    # large immediate opponent threats when a direct block exists.
    immediate_by_action: Dict[int, int] = {}
    for r, c in legal:
        action_id = ActionCodec.coord_to_action(r, c)
        immediate_by_action[action_id] = manager.bot_session._immediate_move_score(env, mark, (r, c))
    own_best = max(immediate_by_action.values()) if immediate_by_action else 0
    if own_best > 0:
        best_ids = [action_id for action_id, score in immediate_by_action.items() if score == own_best]
        base_id = ActionCodec.coord_to_action(base_decision.row, base_decision.col)
        chosen_id = base_id if base_id in best_ids else best_ids[0]
        chosen_row, chosen_col = ActionCodec.action_to_coord(chosen_id)
        return BotDecision(
            row=chosen_row,
            col=chosen_col,
            source=f"{base_decision.source}_demo_tactical_own",
            candidates=base_decision.candidates,
        )

    opp_legal = env.legal_actions(opponent)
    if opp_legal:
        opp_immediate = {
            coord: manager.bot_session._immediate_move_score(env, opponent, coord)
            for coord in opp_legal
        }
        opp_best = max(opp_immediate.values()) if opp_immediate else 0
        if opp_best >= 4:
            threat_coords = {coord for coord, score in opp_immediate.items() if score == opp_best}
            blocking = [coord for coord in legal if coord in threat_coords]
            if blocking:
                base_coord = (base_decision.row, base_decision.col)
                chosen_coord = base_coord if base_coord in blocking else blocking[0]
                return BotDecision(
                    row=chosen_coord[0],
                    col=chosen_coord[1],
                    source=f"{base_decision.source}_demo_tactical_block",
                    candidates=base_decision.candidates,
                )

    occ = manager.state.board_occupancy()
    row_counts = [0] * 8
    col_counts = [0] * 8
    for r, c in occ:
        row_counts[r] += 1
        col_counts[c] += 1
    total_occ = max(1, len(occ))
    max_row = max(row_counts) if row_counts else 0
    max_col = max(col_counts) if col_counts else 0
    min_row = min(row_counts) if row_counts else 0
    min_col = min(col_counts) if col_counts else 0
    base_coord = (base_decision.row, base_decision.col)
    candidate_hint = {
        ActionCodec.coord_to_action(c.row, c.col): float(c.score)
        for c in base_decision.candidates
    }
    hint_min = min(candidate_hint.values()) if candidate_hint else 0.0
    hint_max = max(candidate_hint.values()) if candidate_hint else 1.0

    # When occupancy is imbalanced, force consideration of underused rows/cols.
    filtered_legal = list(legal)
    if total_occ >= 6:
        scarce = [
            (r, c)
            for r, c in legal
            if row_counts[r] <= (min_row + 1) and col_counts[c] <= (min_col + 1)
        ]
        if len(scarce) >= 6:
            filtered_legal = scarce

    scores: Dict[int, float] = {}
    for r, c in filtered_legal:
        action_id = ActionCodec.coord_to_action(r, c)
        prior = float(priors[action_id]) if priors is not None else (1.0 / len(filtered_legal))
        if hint_max > hint_min:
            hint = (candidate_hint.get(action_id, hint_min) - hint_min) / (hint_max - hint_min)
        elif action_id in candidate_hint:
            hint = 1.0
        else:
            hint = 0.0
        center = 1.0 - (((r - 3.5) ** 2 + (c - 3.5) ** 2) / 24.5)
        crowd = (row_counts[r] + col_counts[c]) / (2.0 * total_occ)
        row_underuse = ((max_row - row_counts[r]) / max(1, max_row)) if max_row > 0 else 1.0
        col_underuse = ((max_col - col_counts[c]) / max(1, max_col)) if max_col > 0 else 1.0
        local_neighbors = 0
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr = r + dr
                cc = c + dc
                if 0 <= rr < 8 and 0 <= cc < 8 and (rr, cc) in occ:
                    local_neighbors += 1
        density = local_neighbors / 8.0
        base_bonus = 0.04 if (r, c) == base_coord else 0.0
        scores[action_id] = (
            (0.58 * prior)
            + (0.24 * hint)
            + (0.10 * center)
            + (0.08 * row_underuse)
            + (0.08 * col_underuse)
            - (0.10 * crowd)
            - (0.08 * density)
            + base_bonus
        )
    if not scores:
        return base_decision
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    chosen_id = ranked[0][0]
    base_id = ActionCodec.coord_to_action(base_decision.row, base_decision.col)
    base_score = scores.get(base_id)
    best_score = ranked[0][1]
    # Only override the model when the rebalance signal is meaningfully stronger.
    if base_score is not None and best_score <= base_score + 0.08:
        chosen_id = base_id
    chosen_row, chosen_col = ActionCodec.action_to_coord(chosen_id)
    top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return BotDecision(
        row=chosen_row,
        col=chosen_col,
        source=f"{base_decision.source}_demo",
        candidates=[
            BotCandidate(
                row=ActionCodec.action_to_coord(i)[0],
                col=ActionCodec.action_to_coord(i)[1],
                score=float(score),
            )
            for i, score in top
        ],
    )


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


async def finalize_bot_commit(
    summary: Dict[str, object],
    decision: Optional[object],
    *,
    schedule_next: bool = True,
) -> bool:
    await broadcast_state(refresh=True)
    if decision is not None:
        await send_bot_explanation_to_o(Mark.X, decision)

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
            "winner": winner_mark,
        })
        return False

    manager.bot_turn_nonce += 1
    if schedule_next:
        await maybe_schedule_bot_move()
    return True


async def maybe_schedule_bot_move() -> None:
    if manager.mode != MODE_HUMAN_VS_BOT or manager.game_over:
        return
    if Mark.O not in manager.players:
        return
    if manager.state.pending[Mark.X] is not None:
        return
    if manager.bot_move_task is not None and not manager.bot_move_task.done():
        return
    if manager.bot_session is None:
        manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)

    delay_min = _env_float("WHISK_BOT_DELAY_MIN_SEC", 1.0, min_value=0.0, max_value=10.0)
    delay_max = _env_float("WHISK_BOT_DELAY_MAX_SEC", 5.0, min_value=0.0, max_value=30.0)
    if delay_max < delay_min:
        delay_max = delay_min
    decision_timeout_sec = _env_float(
        "WHISK_BOT_DECISION_TIMEOUT_SEC", 12.0, min_value=0.2, max_value=120.0
    )

    expected_turn = manager.state.turn
    expected_nonce = manager.bot_turn_nonce
    delay_sec = manager.bot_session.rng.uniform(delay_min, delay_max)

    def _fallback_bot_decision() -> BotDecision:
        if manager.bot_session is None:
            manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
        env = WhiskEnv(mode="remote")
        env.state = deepcopy(manager.state)
        row, col = manager.bot_session.greedy.select_action(env, Mark.X, manager.bot_session.rng)
        return BotDecision(
            row=row,
            col=col,
            source="fallback_greedy",
            candidates=[BotCandidate(row=row, col=col, score=1.0)],
        )

    async def _run_bot_move() -> None:
        should_retry = False
        should_schedule_next = False
        try:
            await asyncio.sleep(delay_sec)
            if manager.mode != MODE_HUMAN_VS_BOT or manager.game_over:
                return
            if Mark.O not in manager.players:
                return
            if manager.bot_turn_nonce != expected_nonce or manager.state.turn != expected_turn:
                return
            if manager.state.pending[Mark.X] is not None:
                return
            if manager.bot_session is None:
                manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)

            state_snapshot = deepcopy(manager.state)
            try:
                decision = await asyncio.wait_for(
                    asyncio.to_thread(manager.bot_session.choose_decision, state_snapshot, Mark.X),
                    timeout=decision_timeout_sec,
                )
            except Exception:
                decision = _fallback_bot_decision()

            # Re-check state after background compute in case the turn changed.
            if manager.mode != MODE_HUMAN_VS_BOT or manager.game_over:
                return
            if Mark.O not in manager.players:
                return
            if manager.bot_turn_nonce != expected_nonce or manager.state.turn != expected_turn:
                return
            if manager.state.pending[Mark.X] is not None:
                return

            try:
                apply_move(manager.state, Mark.X, decision.row, decision.col)
            except ValueError:
                # Recover from rare stale/invalid proposals by immediately retrying.
                manager.bot_turn_nonce += 1
                should_retry = True
                return
            manager.bot_pending_decision = decision
            await broadcast_pending_flags()

            if ready_to_commit(manager.state):
                summary = commit_turn(manager.state)
                committed_decision = manager.bot_pending_decision
                manager.bot_pending_decision = None
                should_schedule_next = await finalize_bot_commit(
                    summary,
                    committed_decision,
                    schedule_next=False,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            manager.bot_turn_nonce += 1
            should_retry = True
        finally:
            current_task = asyncio.current_task()
            if manager.bot_move_task is current_task:
                manager.bot_move_task = None
            if should_retry or should_schedule_next:
                await maybe_schedule_bot_move()

    manager.bot_move_task = asyncio.create_task(_run_bot_move())


async def maybe_schedule_demo_move() -> None:
    if manager.mode != MODE_DEMO or manager.game_over:
        return
    if Mark.O not in manager.players:
        return
    if manager.state.pending[Mark.O] is not None or manager.state.pending[Mark.X] is not None:
        return
    if manager.bot_move_task is not None and not manager.bot_move_task.done():
        return
    if manager.bot_session is None:
        manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)

    delay_min = _env_float("WHISK_DEMO_DELAY_MIN_SEC", 0.5, min_value=0.0, max_value=10.0)
    delay_max = _env_float("WHISK_DEMO_DELAY_MAX_SEC", 1.5, min_value=0.0, max_value=30.0)
    if delay_max < delay_min:
        delay_max = delay_min
    decision_timeout_sec = _env_float(
        "WHISK_BOT_DECISION_TIMEOUT_SEC", 12.0, min_value=0.2, max_value=120.0
    )

    moving_mark = manager.local_next_mark
    expected_turn = manager.state.turn
    expected_nonce = manager.bot_turn_nonce
    delay_sec = manager.bot_session.rng.uniform(delay_min, delay_max)

    def _fallback_bot_decision(mark: Mark) -> BotDecision:
        if manager.bot_session is None:
            manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
        env = WhiskEnv(mode="remote")
        env.state = deepcopy(manager.state)
        row, col = manager.bot_session.greedy.select_action(env, mark, manager.bot_session.rng)
        return BotDecision(
            row=row,
            col=col,
            source="fallback_greedy",
            candidates=[BotCandidate(row=row, col=col, score=1.0)],
        )

    def _random_demo_decision(mark: Mark) -> BotDecision:
        if manager.bot_session is None:
            manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
        env = WhiskEnv(mode="remote")
        env.state = deepcopy(manager.state)
        legal = env.legal_actions(mark)
        if not legal:
            raise RuntimeError("Demo player has no legal actions")
        row, col = manager.bot_session.rng.choice(legal)
        return BotDecision(
            row=row,
            col=col,
            source="demo_opening_random",
            candidates=[BotCandidate(row=row, col=col, score=1.0)],
        )

    async def _run_demo_move() -> None:
        should_retry = False
        should_schedule_next = False
        try:
            await asyncio.sleep(delay_sec)
            if manager.mode != MODE_DEMO or manager.game_over:
                return
            if Mark.O not in manager.players:
                return
            if manager.bot_turn_nonce != expected_nonce or manager.state.turn != expected_turn:
                return
            if manager.local_next_mark != moving_mark:
                return
            if manager.state.pending[moving_mark] is not None:
                return
            if manager.bot_session is None:
                manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)

            if expected_turn < 2:
                decision = _random_demo_decision(moving_mark)
            else:
                state_snapshot = deepcopy(manager.state)
                try:
                    decision = await asyncio.wait_for(
                        asyncio.to_thread(manager.bot_session.choose_decision, state_snapshot, moving_mark),
                        timeout=decision_timeout_sec,
                    )
                except Exception:
                    decision = _fallback_bot_decision(moving_mark)
                decision = _demo_rebalance_decision(moving_mark, decision)

            if manager.mode != MODE_DEMO or manager.game_over:
                return
            if Mark.O not in manager.players:
                return
            if manager.bot_turn_nonce != expected_nonce or manager.state.turn != expected_turn:
                return
            if manager.local_next_mark != moving_mark:
                return
            if manager.state.pending[moving_mark] is not None:
                return

            try:
                apply_move(manager.state, moving_mark, decision.row, decision.col)
                summary = commit_single_move(manager.state, moving_mark)
            except ValueError:
                manager.bot_turn_nonce += 1
                should_retry = True
                return

            added = summary.get("added", {})
            add_o = int(added.get("O", 0)) if isinstance(added, dict) else 0
            add_x = int(added.get("X", 0)) if isinstance(added, dict) else 0
            move_log_entry = (
                f"turn={manager.state.turn} mark={moving_mark.value} row={decision.row} "
                f"col={decision.col} source={decision.source} add_o={add_o} add_x={add_x} "
                f"score_o={manager.state.scores[Mark.O]} score_x={manager.state.scores[Mark.X]}"
            )
            manager.demo_move_log.append(move_log_entry)
            logger.warning("demo_move %s", move_log_entry)
            print(f"demo_move {move_log_entry}", flush=True)
            print(f"demo_move {move_log_entry}", file=sys.stderr, flush=True)
            await manager.broadcast(
                {
                    "type": "demo_move",
                    "turn": manager.state.turn,
                    "mark": moving_mark.value,
                    "row": decision.row,
                    "col": decision.col,
                    "source": decision.source,
                    "added": {"O": add_o, "X": add_x},
                    "scores": {
                        "O": int(manager.state.scores[Mark.O]),
                        "X": int(manager.state.scores[Mark.X]),
                    },
                }
            )

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
                    "winner": winner_mark,
                })
                manager.clear_demo_move_log(reason="game_over")
                return

            manager.bot_turn_nonce += 1
            should_schedule_next = True
        except asyncio.CancelledError:
            return
        except Exception:
            manager.bot_turn_nonce += 1
            should_retry = True
        finally:
            current_task = asyncio.current_task()
            if manager.bot_move_task is current_task:
                manager.bot_move_task = None
            if should_retry or should_schedule_next:
                await maybe_schedule_demo_move()

    manager.bot_move_task = asyncio.create_task(_run_demo_move())


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
                requested_local_x_name = parse_player_name(msg.get("x_name"), "Player X")

                manager.prune_disconnected()
                if requested_mode in (MODE_HUMAN_VS_BOT, MODE_DEMO) and Mark.O not in manager.players:
                    evict_leftover_for_single_player_mode(ws_id)

                if manager.is_full():
                    await manager.send(ws, {
                        "type": "error",
                        "message": "A game is already in progress with two players.",
                    })
                    continue
                if manager.mode == MODE_HUMAN_VS_BOT and Mark.O in manager.players:
                    await manager.send(ws, {
                        "type": "error",
                        "message": "A computer game is already in progress.",
                    })
                    continue
                if manager.mode == MODE_DEMO and Mark.O in manager.players:
                    await manager.send(ws, {
                        "type": "error",
                        "message": "A demo is already in progress.",
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
                    if manager.mode in (MODE_HUMAN_VS_BOT, MODE_DEMO):
                        manager.bot_seed = requested_bot_seed
                        manager.bot_turn_nonce += 1
                        manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
                        manager.bot_pending_decision = None
                    else:
                        manager.cancel_bot_move_task()
                        manager.bot_pending_decision = None
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
                if mark == Mark.O and manager.mode == MODE_LOCAL:
                    manager.local_player_names[Mark.O] = name
                    manager.local_player_names[Mark.X] = requested_local_x_name
                manager.ws_to_mark[ws_id] = mark
                manager.lobby_clients.pop(ws_id, None)

                await manager.send(ws, {"type": "joined", "mark": mark.value})
                await broadcast_state()
                await broadcast_lobby()
                if mark == Mark.O and manager.mode == MODE_HUMAN_VS_BOT:
                    await maybe_schedule_bot_move()
                if mark == Mark.O and manager.mode == MODE_DEMO:
                    await maybe_schedule_demo_move()

                if mark == Mark.O and manager.mode is None:
                    await manager.send(
                        ws,
                        {
                            "type": "need_mode",
                            "message": "Choose Local, Remote, Play Against Computer, or Demo.",
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
                            "message": "Mode must be local, remote, human_vs_bot, or demo.",
                        },
                    )
                    continue

                manager.mode = mode
                if mode in (MODE_HUMAN_VS_BOT, MODE_DEMO):
                    manager.bot_seed = parse_bot_seed(msg.get("bot_seed"), manager.bot_seed)
                    manager.bot_turn_nonce += 1
                    manager.bot_session = HumanVsAgentSession(seed=manager.bot_seed)
                    manager.bot_pending_decision = None
                else:
                    manager.cancel_bot_move_task()
                    manager.bot_pending_decision = None
                await manager.broadcast({"type": "mode", "mode": mode})
                if mode == MODE_LOCAL:
                    manager.local_next_mark = Mark.O
                    if Mark.O in manager.players:
                        manager.local_player_names[Mark.O] = manager.players[Mark.O].name
                    manager.local_player_names[Mark.X] = parse_player_name(
                        msg.get("x_name"), manager.local_player_names.get(Mark.X, "Player X")
                    )
                await broadcast_state()
                await broadcast_lobby()
                if mode == MODE_HUMAN_VS_BOT:
                    await maybe_schedule_bot_move()
                if mode == MODE_DEMO:
                    await maybe_schedule_demo_move()

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
                            "winner": winner_mark,
                        })
                    continue

                if manager.mode == MODE_HUMAN_VS_BOT:
                    if mark != Mark.O:
                        await manager.send(ws, {"type": "error", "message": "Only Player 1 can move in Play Against Computer mode."})
                        continue

                    try:
                        apply_move(manager.state, Mark.O, row, col)
                    except ValueError as e:
                        await manager.send(ws, {"type": "invalid_move", "message": str(e)})
                        continue

                    await send_state(ws, Mark.O)
                    await broadcast_pending_flags()
                    if ready_to_commit(manager.state):
                        summary = commit_turn(manager.state)
                        decision = manager.bot_pending_decision
                        manager.bot_pending_decision = None
                        await finalize_bot_commit(summary, decision)
                    else:
                        await maybe_schedule_bot_move()
                    continue

                if manager.mode == MODE_DEMO:
                    await manager.send(ws, {"type": "error", "message": "Demo mode is autoplay only."})
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
                            "winner": winner_mark,
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
                if manager.mode == MODE_HUMAN_VS_BOT:
                    await maybe_schedule_bot_move()
                if manager.mode == MODE_DEMO:
                    await maybe_schedule_demo_move()

            else:
                await manager.send(ws, {"type": "error", "message": f"Unknown message type: {mtype}"})

    except WebSocketDisconnect:
        manager.lobby_clients.pop(ws_id, None)
        mark = manager.ws_to_mark.pop(ws_id, None)
        if mark and mark in manager.players:
            del manager.players[mark]
            manager.state.pending[mark] = None
            if manager.mode in (MODE_HUMAN_VS_BOT, MODE_DEMO):
                manager.cancel_bot_move_task()
                manager.bot_pending_decision = None
                manager.bot_turn_nonce += 1
            await broadcast_state()
            await broadcast_lobby()
            manager.reset_on_next_join = True

        if not manager.players:
            manager.reset()
