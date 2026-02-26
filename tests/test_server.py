from fastapi.testclient import TestClient

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


def highlight_coords(state_msg):
    return {(h["row"], h["col"]) for h in state_msg.get("highlight", [])}


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
        ws_o.send_json({"type": "join", "name": "OPlayer", "mode": "local"})
        _recv_type(ws_o, "joined")
        _recv_type(ws_o, "state")

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
