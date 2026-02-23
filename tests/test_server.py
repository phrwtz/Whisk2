from fastapi.testclient import TestClient

from backend.app.main import app, manager


def _recv_type(ws, expected_type: str, max_messages: int = 6):
    """Read websocket messages until expected type appears."""
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == expected_type:
            return msg
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
