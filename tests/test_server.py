import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.agents.deploy import promote_release_artifact
from backend.app.agents.human_adapter import HumanVsAgentSession
from backend.app.agents.model import WhiskPolicyValueModel
from backend.app.game import GameState, Mark, Piece, apply_move, commit_turn
from backend.app.main import app, manager


def _recv_type(ws, expected_type: str, max_messages: int = 6):
    """Read websocket messages until expected type appears."""
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == expected_type:
            return msg
    raise AssertionError(f"Did not receive {expected_type!r} within {max_messages} messages")


def _recv_state_where(ws, predicate, max_messages: int = 12):
    """Read websocket messages until a state payload matches predicate."""
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "state" and predicate(msg):
            return msg
    raise AssertionError("Did not receive expected matching state payload")


def _recv_pending_flags(ws, max_messages: int = 8):
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "pending_flags":
            return msg
    raise AssertionError("Did not receive pending_flags")


def _recv_n(ws, n: int):
    return [ws.receive_json() for _ in range(n)]


def highlight_coords(state_msg):
    return {(h["row"], h["col"]) for h in state_msg.get("highlight", [])}


def _recv_until_type(ws, expected_type: str, max_messages: int = 12):
    """Read websocket messages until expected type appears; return all consumed."""
    msgs = []
    for _ in range(max_messages):
        msg = ws.receive_json()
        msgs.append(msg)
        if msg.get("type") == expected_type:
            return msgs
    raise AssertionError(f"Did not receive {expected_type!r} within {max_messages} messages")


def test_reject_moves_after_game_over():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "OPlayer", "mode": "remote"})
        _recv_type(ws_o, "joined")

        ws_x.send_json({"type": "join", "name": "XPlayer"})
        _recv_type(ws_x, "joined")

        # Drain one state broadcast sent to O when X joins.
        _recv_type(ws_o, "state")

        manager.game_over = True
        ws_o.send_json({"type": "move", "row": 0, "col": 0})
        err = _recv_type(ws_o, "error")
        assert err["message"] == "Game is over. Start a new game."

    manager.reset()


def test_first_mover_gets_private_preview_until_second_move():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o:
        ws_o.send_json({"type": "join", "name": "Paul", "mode": "remote"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        ws_o.send_json({"type": "move", "row": 0, "col": 0})
        o_state = _recv_type(ws_o, "state")
        assert any(p["mark"] == "O" and p["row"] == 0 and p["col"] == 0 for p in o_state["pieces"])
        assert o_state["pending"]["O"] is True
        assert o_state["pending"]["X"] is False

        with client.websocket_connect("/ws") as ws_x:
            ws_x.send_json({"type": "join", "name": "Greg"})
            _recv_type(ws_x, "joined")
            x_state = _recv_type(ws_x, "state")
            assert not any(p["mark"] == "O" and p["row"] == 0 and p["col"] == 0 for p in x_state["pieces"])
            assert x_state["pending"]["O"] is True
            assert x_state["pending"]["X"] is False

            ws_x.send_json({"type": "move", "row": 0, "col": 1})
            o_committed = _recv_state_where(
                ws_o, lambda s: (not s["pending"]["O"]) and (not s["pending"]["X"])
            )
            x_committed = _recv_state_where(
                ws_x, lambda s: (not s["pending"]["O"]) and (not s["pending"]["X"])
            )

            assert any(p["mark"] == "O" and p["row"] == 0 and p["col"] == 0 for p in o_committed["pieces"])
            assert any(p["mark"] == "X" and p["row"] == 0 and p["col"] == 1 for p in o_committed["pieces"])
            assert any(p["mark"] == "O" and p["row"] == 0 and p["col"] == 0 for p in x_committed["pieces"])
            assert any(p["mark"] == "X" and p["row"] == 0 and p["col"] == 1 for p in x_committed["pieces"])
            assert o_committed["pending"]["O"] is False
            assert o_committed["pending"]["X"] is False
            assert x_committed["pending"]["O"] is False
            assert x_committed["pending"]["X"] is False

    manager.reset()


def test_highlight_visibility_and_reset_on_new_move():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "OPlayer", "mode": "remote"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        ws_x.send_json({"type": "join", "name": "XPlayer"})
        _recv_type(ws_x, "joined")
        _recv_type(ws_x, "state")
        # Drain the broadcast to O that accompanies X joining.
        _recv_type(ws_o, "state")

        def complete_turn(o_coord, x_coord):
            ws_o.send_json({"type": "move", "row": o_coord[0], "col": o_coord[1]})
            _recv_state_where(ws_o, lambda s: s["pending"]["O"])
            ws_x.send_json({"type": "move", "row": x_coord[0], "col": x_coord[1]})
            o_state = _recv_state_where(ws_o, lambda s: not s["pending"]["O"] and not s["pending"]["X"])
            x_state = _recv_state_where(ws_x, lambda s: not s["pending"]["O"] and not s["pending"]["X"])
            return o_state, x_state

        # Lay down two turns so that O has pieces at (0,0) and (0,1).
        complete_turn((0, 0), (1, 0))
        complete_turn((0, 1), (1, 1))

        target_line = {(0, 0), (0, 1), (0, 2)}

        # O initiates a scoring move at (0,2) and should see the highlight privately.
        ws_o.send_json({"type": "move", "row": 0, "col": 2})
        o_pending = _recv_state_where(ws_o, lambda s: s["pending"]["O"])
        assert highlight_coords(o_pending) == target_line

        # Complete X's move. After the commit both players should see the same highlight.
        ws_x.send_json({"type": "move", "row": 7, "col": 7})
        post_o = _recv_state_where(ws_o, lambda s: not s["pending"]["O"] and not s["pending"]["X"])
        post_x = _recv_state_where(ws_x, lambda s: not s["pending"]["O"] and not s["pending"]["X"])
        assert highlight_coords(post_o) == target_line
        assert highlight_coords(post_x) == highlight_coords(post_o)

        # When O begins the next turn, the previous highlight should be gone for O.
        ws_o.send_json({"type": "move", "row": 2, "col": 2})
        o_next = _recv_state_where(ws_o, lambda s: s["pending"]["O"])
        assert highlight_coords(o_next) == set()

    manager.reset()


def test_join_resets_state_when_player_refreshes():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "OPlayer", "mode": "remote"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        ws_x.send_json({"type": "join", "name": "XPlayer"})
        _recv_type(ws_x, "joined")
        _recv_type(ws_x, "state")
        _recv_type(ws_o, "state")

        ws_o.send_json({"type": "move", "row": 0, "col": 0})
        _recv_state_where(ws_o, lambda s: s["pending"]["O"])
        ws_x.send_json({"type": "move", "row": 1, "col": 1})
        _recv_state_where(ws_o, lambda s: not s["pending"]["O"] and not s["pending"]["X"])

        ws_x.close()
        _recv_state_where(ws_o, lambda s: s["players"]["X"] is None)

        with client.websocket_connect("/ws") as ws_x_new:
            ws_x_new.send_json({"type": "join", "name": "XPlayer"})
            _recv_type(ws_x_new, "joined")
            state_new = _recv_state_where(ws_x_new, lambda s: s["turn"] == 0)
            assert state_new["pieces"] == []
            assert state_new["scores"]["O"] == 0
            assert state_new["scores"]["X"] == 0

            state_for_o = _recv_state_where(ws_o, lambda s: s["turn"] == 0)
            assert state_for_o["pieces"] == []
            assert state_for_o["scores"]["O"] == 0
            assert state_for_o["scores"]["X"] == 0

    manager.reset()


def test_set_mode_requires_join_and_only_o_can_set_mode():
    manager.reset()
    client = TestClient(app)

    # Can't set mode before joining.
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "set_mode", "mode": "remote"})
        err = _recv_type(ws, "error")
        assert err["message"] == "You must join first."

    # Only O can set mode.
    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "OPlayer", "mode": "remote"})
        _recv_type(ws_o, "joined")

        ws_x.send_json({"type": "join", "name": "XPlayer"})
        _recv_type(ws_x, "joined")

        ws_x.send_json({"type": "set_mode", "mode": "local"})
        err = _recv_type(ws_x, "error")
        assert err["message"] == "Only O can set mode."

    manager.reset()


def test_local_mode_single_player_alternates_marks_and_commits_immediately():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o:
        ws_o.send_json({"type": "join", "name": "OPlayer", "mode": "local", "x_name": "XPlayer"})
        _recv_type(ws_o, "joined")
        init_state = _recv_type(ws_o, "state")
        assert init_state["players"]["O"] == "OPlayer"
        assert init_state["players"]["X"] == "XPlayer"

        ws_o.send_json({"type": "move", "row": 0, "col": 0})
        first_state = _recv_state_where(
            ws_o, lambda s: s["turn"] == 1 and (not s["pending"]["O"]) and (not s["pending"]["X"])
        )
        assert any(p["mark"] == "O" and p["row"] == 0 and p["col"] == 0 for p in first_state["pieces"])
        assert first_state["local_next_mark"] == "X"

        ws_o.send_json({"type": "move", "row": 0, "col": 1})
        second_state = _recv_state_where(
            ws_o, lambda s: s["turn"] == 2 and (not s["pending"]["O"]) and (not s["pending"]["X"])
        )
        assert any(p["mark"] == "X" and p["row"] == 0 and p["col"] == 1 for p in second_state["pieces"])
        assert second_state["local_next_mark"] == "O"

    manager.reset()


def test_local_mode_move_scores_only_for_current_mark():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o:
        ws_o.send_json({"type": "join", "name": "Solo", "mode": "local"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        # Build O at (0,0), (0,1), then scoring move at (0,2).
        ws_o.send_json({"type": "move", "row": 0, "col": 0})  # O
        _recv_state_where(ws_o, lambda s: s["turn"] == 1)
        ws_o.send_json({"type": "move", "row": 7, "col": 7})  # X filler
        _recv_state_where(ws_o, lambda s: s["turn"] == 2)
        ws_o.send_json({"type": "move", "row": 0, "col": 1})  # O
        _recv_state_where(ws_o, lambda s: s["turn"] == 3)
        ws_o.send_json({"type": "move", "row": 7, "col": 6})  # X filler
        _recv_state_where(ws_o, lambda s: s["turn"] == 4)
        ws_o.send_json({"type": "move", "row": 0, "col": 2})  # O scores
        o_scoring = _recv_state_where(ws_o, lambda s: s["turn"] == 5)
        assert o_scoring["scores"]["O"] > 0
        o_before_x = o_scoring["scores"]["O"]

        # X scores on this move; O's score should not also increase.
        ws_o.send_json({"type": "move", "row": 7, "col": 5})  # X scores
        x_scoring = _recv_state_where(ws_o, lambda s: s["turn"] == 6)
        assert x_scoring["scores"]["X"] > 0
        assert x_scoring["scores"]["O"] == o_before_x

    manager.reset()


def test_remote_mode_turn_signals_for_both_players():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "Olive", "mode": "remote"})
        _recv_type(ws_o, "joined")
        state_o_initial = _recv_type(ws_o, "state")
        assert state_o_initial["pending"] == {"O": False, "X": False}

        ws_x.send_json({"type": "join", "name": "Xavier"})
        _recv_type(ws_x, "joined")
        state_x_join = _recv_type(ws_x, "state")
        assert state_x_join["pending"] == {"O": False, "X": False}
        _recv_type(ws_o, "state")

        ws_o.send_json({"type": "move", "row": 0, "col": 0})
        pending_o = _recv_pending_flags(ws_o)
        pending_x = _recv_pending_flags(ws_x)
        assert pending_o["pending"] == {"O": True, "X": False}
        assert pending_x["pending"] == {"O": True, "X": False}

        ws_x.send_json({"type": "move", "row": 0, "col": 1})
        committed_o = _recv_state_where(ws_o, lambda s: not s["pending"]["O"] and not s["pending"]["X"])
        committed_x = _recv_state_where(ws_x, lambda s: not s["pending"]["O"] and not s["pending"]["X"])
        assert committed_o["pending"] == {"O": False, "X": False}
        assert committed_x["pending"] == {"O": False, "X": False}

    manager.reset()


def test_remote_score_event_sent_only_to_scoring_player():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "Olive", "mode": "remote"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        ws_x.send_json({"type": "join", "name": "Xavier"})
        _recv_type(ws_x, "joined")
        _recv_type(ws_x, "state")
        _recv_type(ws_o, "state")

        def play_turn(o_coord, x_coord):
            ws_o.send_json({"type": "move", "row": o_coord[0], "col": o_coord[1]})
            _recv_pending_flags(ws_o)
            _recv_pending_flags(ws_x)
            ws_x.send_json({"type": "move", "row": x_coord[0], "col": x_coord[1]})
            msgs_o = _recv_until_type(ws_o, "turn_committed")
            msgs_x = _recv_until_type(ws_x, "turn_committed")
            return msgs_o, msgs_x

        # Build O line while X plays non-scoring filler moves.
        play_turn((0, 0), (7, 7))
        play_turn((0, 1), (6, 7))

        # Third O in row scores; only O should get score_event.
        o_msgs, x_msgs = play_turn((0, 2), (6, 6))

        assert any(m.get("type") == "score_event" and m.get("mark") == "O" and m.get("added", 0) > 0 for m in o_msgs)
        assert all(m.get("type") != "score_event" for m in x_msgs)

    manager.reset()


def test_remote_scoring_only_counts_lines_created_by_latest_own_move():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "Olive", "mode": "remote"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        ws_x.send_json({"type": "join", "name": "Xavier"})
        _recv_type(ws_x, "joined")
        _recv_type(ws_x, "state")
        _recv_type(ws_o, "state")

        def play_turn(o_coord, x_coord):
            ws_o.send_json({"type": "move", "row": o_coord[0], "col": o_coord[1]})
            _recv_pending_flags(ws_o)
            _recv_pending_flags(ws_x)
            ws_x.send_json({"type": "move", "row": x_coord[0], "col": x_coord[1]})
            s_o = _recv_state_where(ws_o, lambda s: not s["pending"]["O"] and not s["pending"]["X"])
            s_x = _recv_state_where(ws_x, lambda s: not s["pending"]["O"] and not s["pending"]["X"])
            _recv_type(ws_o, "turn_committed")
            _recv_type(ws_x, "turn_committed")
            return s_o, s_x

        # No one scores yet.
        s1_o, _ = play_turn((0, 0), (7, 7))
        assert s1_o["scores"] == {"O": 0, "X": 0}

        s2_o, _ = play_turn((0, 1), (7, 6))
        assert s2_o["scores"] == {"O": 0, "X": 0}

        # Both latest moves complete a 3-in-a-row, so both should score +1.
        s3_o, _ = play_turn((0, 2), (7, 5))
        assert s3_o["scores"] == {"O": 1, "X": 1}

        # O does NOT keep scoring from the old 3-line; X scores +4 for new 4-line.
        s4_o, _ = play_turn((2, 2), (7, 4))
        assert s4_o["scores"]["O"] == 1
        assert s4_o["scores"]["X"] == 5

        # X move should not award O points, and O move should not award X points.
        s5_o, _ = play_turn((2, 3), (6, 0))
        assert s5_o["scores"] == {"O": 1, "X": 5}

    manager.reset()


def test_commit_turn_both_reach_50_plus_is_tie():
    state = GameState()

    # Turn 1: place two pieces each without scoring.
    apply_move(state, Mark.O, 0, 0)
    apply_move(state, Mark.X, 7, 7)
    commit_turn(state)
    apply_move(state, Mark.O, 0, 1)
    apply_move(state, Mark.X, 7, 6)
    commit_turn(state)

    # Force near-win, then both score +1 on the same commit.
    state.scores[Mark.O] = 49
    state.scores[Mark.X] = 49

    apply_move(state, Mark.O, 0, 2)  # completes O's 3-in-row
    apply_move(state, Mark.X, 7, 5)  # completes X's 3-in-row
    summary = commit_turn(state)

    assert summary["done"] is True
    assert summary["winner"] == "TIE"


def test_commit_turn_both_50_plus_but_unequal_not_tie():
    state = GameState()

    # Build a simple board with no immediate scoring lines.
    apply_move(state, Mark.O, 0, 0)
    apply_move(state, Mark.X, 7, 7)
    commit_turn(state)

    # Force both >= 50 but unequal.
    state.scores[Mark.O] = 50
    state.scores[Mark.X] = 51

    # Neutral commit with no scoring changes.
    apply_move(state, Mark.O, 1, 2)
    apply_move(state, Mark.X, 6, 5)
    summary = commit_turn(state)

    assert summary["done"] is True
    assert summary["winner"] == "X"


def test_second_player_cannot_join_when_first_player_selected_local_mode():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "Host", "mode": "local"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        ws_x.send_json({"type": "join", "name": "Guest", "mode": "remote"})
        err = _recv_type(ws_x, "error")
        assert err["message"] == "Host is playing Whisk in local mode so you can't join at this time."

    manager.reset()


def test_lobby_message_exposes_host_and_mode_before_join():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o:
        lobby0 = _recv_type(ws_o, "lobby")
        assert lobby0["players"]["O"] is None
        assert lobby0["mode"] is None

        ws_o.send_json({"type": "join", "name": "Host", "mode": "remote"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        with client.websocket_connect("/ws") as ws_guest:
            lobby_guest = _recv_type(ws_guest, "lobby")
            assert lobby_guest["players"]["O"] == "Host"
            assert lobby_guest["mode"] == "remote"

    manager.reset()


def test_unjoined_client_gets_lobby_update_when_first_player_joins():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_waiting, client.websocket_connect("/ws") as ws_host:
        # Initial lobby snapshots on connect.
        _recv_type(ws_waiting, "lobby")
        _recv_type(ws_host, "lobby")

        ws_host.send_json({"type": "join", "name": "Host", "mode": "remote"})
        _recv_type(ws_host, "joined")
        _recv_type(ws_host, "state")

        # Waiting (not joined) client should be pushed an updated lobby view.
        lobby_update = _recv_type(ws_waiting, "lobby")
        assert lobby_update["players"]["O"] == "Host"
        assert lobby_update["mode"] == "remote"

    manager.reset()


def test_second_player_can_start_new_local_session_when_first_is_local():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_first, client.websocket_connect("/ws") as ws_second:
        _recv_type(ws_first, "lobby")
        _recv_type(ws_second, "lobby")

        ws_first.send_json({"type": "join", "name": "First", "mode": "local"})
        _recv_type(ws_first, "joined")
        _recv_type(ws_first, "state")
        _recv_type(ws_second, "lobby")

        # Second player chooses local and should be admitted as new local host (O).
        ws_second.send_json({"type": "join", "name": "Second", "mode": "local"})
        joined = _recv_type(ws_second, "joined")
        assert joined["mark"] == "O"
        state2 = _recv_type(ws_second, "state")
        assert state2["players"]["O"] == "Second"
        assert state2["mode"] == "local"

        # Original first player is no longer joined in this game session.
        ws_first.send_json({"type": "move", "row": 0, "col": 0})
        err = _recv_type(ws_first, "error")
        assert err["message"] == "You must join first."

    manager.reset()

def test_human_vs_bot_mode_single_human_gets_bot_reply_on_each_move():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o:
        ws_o.send_json({"type": "join", "name": "Human", "mode": "human_vs_bot"})
        joined = _recv_type(ws_o, "joined")
        assert joined["mark"] == "O"
        init_state = _recv_type(ws_o, "state")
        assert init_state["mode"] == "human_vs_bot"
        assert init_state["players"]["X"] == "WhiskBot"

        ws_o.send_json({"type": "move", "row": 0, "col": 0})
        committed = _recv_state_where(ws_o, lambda s: s["turn"] == 1)
        _recv_type(ws_o, "turn_committed")

        assert committed["pending"] == {"O": False, "X": False}
        assert any(p["mark"] == "O" and p["row"] == 0 and p["col"] == 0 for p in committed["pieces"])
        assert any(p["mark"] == "X" for p in committed["pieces"])

    manager.reset()


def test_human_vs_bot_bot_can_move_first_after_delay():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o:
        ws_o.send_json({"type": "join", "name": "Human", "mode": "human_vs_bot", "bot_seed": 123})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        pending = _recv_pending_flags(ws_o, max_messages=4)
        assert pending["pending"] == {"O": False, "X": True}

    manager.reset()


def test_second_player_cannot_join_active_human_vs_bot_game():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_o, client.websocket_connect("/ws") as ws_x:
        ws_o.send_json({"type": "join", "name": "Human", "mode": "human_vs_bot"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

        ws_x.send_json({"type": "join", "name": "Guest", "mode": "remote"})
        err = _recv_type(ws_x, "error")
        assert err["message"] == "A computer game is already in progress."

    manager.reset()


def test_bot_mode_sends_explanation_event():
    manager.reset()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "name": "Human", "mode": "human_vs_bot", "bot_seed": 42})
        _recv_type(ws, "joined")
        _recv_type(ws, "state")

        ws.send_json({"type": "move", "row": 0, "col": 0})
        msgs = _recv_until_type(ws, "turn_committed")
        explanation = next((m for m in msgs if m.get("type") == "bot_explanation"), None)
        assert explanation is not None
        assert explanation["source"] in ("must_block", "opening_tactical", "model_lookahead", "model_prior", "mcts", "greedy")
        assert "chosen" in explanation and "row" in explanation["chosen"] and "col" in explanation["chosen"]
        assert isinstance(explanation.get("candidates"), list)

    manager.reset()


def test_bot_mode_seed_is_deterministic_for_same_opening():
    manager.reset()
    client = TestClient(app)

    def first_bot_reply(seed: int):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "join", "name": "Human", "mode": "human_vs_bot", "bot_seed": seed})
            _recv_type(ws, "joined")
            _recv_type(ws, "state")
            ws.send_json({"type": "move", "row": 0, "col": 0})
            committed = _recv_state_where(ws, lambda s: s["turn"] == 1)
            _recv_type(ws, "turn_committed")
            bot_piece = next(p for p in committed["pieces"] if p["mark"] == "X")
            return (bot_piece["row"], bot_piece["col"])

    first = first_bot_reply(123)
    manager.reset()
    second = first_bot_reply(123)
    assert first == second

    manager.reset()


def test_bot_mode_loads_promoted_release_artifact_end_to_end(tmp_path: Path, monkeypatch):
    # Build a promoted checkpoint artifact in a temp release directory.
    source_ckpt = tmp_path / "checkpoints" / "generation_001.pkl"
    WhiskPolicyValueModel().save(source_ckpt)

    manifest = tmp_path / "checkpoints" / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"generation": 0, "path": str(source_ckpt), "promoted": False, "metrics": {}},
                {"generation": 1, "path": str(source_ckpt), "promoted": True, "metrics": {"replay_size": 64}},
            ]
        ),
        encoding="utf-8",
    )
    gate = tmp_path / "reports" / "release_gate.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps({"passed": True}), encoding="utf-8")

    promoted = promote_release_artifact(
        manifest_path=manifest,
        gate_path=gate,
        out_dir=tmp_path / "releases",
    )
    promoted_ckpt = Path(promoted["checkpoint"])
    assert promoted_ckpt.exists()

    # Force bot mode to load from the promoted release artifact.
    monkeypatch.setenv("WHISK_BOT_CHECKPOINT", str(promoted_ckpt))

    manager.reset()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "name": "Human", "mode": "human_vs_bot", "bot_seed": 7})
        _recv_type(ws, "joined")
        _recv_type(ws, "state")

        # Human move should trigger immediate bot reply in bot mode.
        ws.send_json({"type": "move", "row": 0, "col": 0})
        msgs = _recv_until_type(ws, "turn_committed")
        explanation = next((m for m in msgs if m.get("type") == "bot_explanation"), None)
        assert explanation is not None
        assert explanation["source"] in ("model_lookahead", "model_prior")

    manager.reset()


def test_human_adapter_must_blocks_single_imminent_five_threat(tmp_path: Path):
    # Human O has four in a row with one open endpoint at (0,4).
    state = GameState()
    for i, col in enumerate((0, 1, 2, 3), start=1):
        state.pieces[Mark.O].append(Piece(mark=Mark.O, row=0, col=col, turn_placed=i))

    # Force fallback behavior (no model loaded); must-block guardrail should apply.
    missing_ckpt = tmp_path / "missing_model.pkl"
    bot = HumanVsAgentSession(checkpoint_path=missing_ckpt, seed=1)
    decision = bot.choose_decision(state, Mark.X)

    assert (decision.row, decision.col) == (0, 4)
    assert decision.source == "must_block"


def test_human_adapter_opening_tactical_blocks_high_value_extension(tmp_path: Path):
    # Human O has three in a row; strongest immediate extension is at (0,3).
    state = GameState()
    for i, col in enumerate((0, 1, 2), start=1):
        state.pieces[Mark.O].append(Piece(mark=Mark.O, row=0, col=col, turn_placed=i))

    missing_ckpt = tmp_path / "missing_model.pkl"
    bot = HumanVsAgentSession(checkpoint_path=missing_ckpt, seed=1)
    decision = bot.choose_decision(state, Mark.X)

    assert (decision.row, decision.col) == (0, 3)
    assert decision.source in ("opening_tactical", "must_block")


def test_human_adapter_pending_reply_blocks_imminent_five_threat(tmp_path: Path):
    # Human O already has 0,1,2 and has pending move at 0,3 this turn.
    # Bot X should reserve 0,4 now so O cannot take an immediate 5 threat next turn.
    state = GameState()
    for i, col in enumerate((0, 1, 2), start=1):
        state.pieces[Mark.O].append(Piece(mark=Mark.O, row=0, col=col, turn_placed=i))
    apply_move(state, Mark.O, 0, 3)

    missing_ckpt = tmp_path / "missing_model.pkl"
    bot = HumanVsAgentSession(checkpoint_path=missing_ckpt, seed=3)
    decision = bot.choose_decision(state, Mark.X)

    assert (decision.row, decision.col) == (0, 4)
    assert decision.source in ("must_block", "opening_tactical")
