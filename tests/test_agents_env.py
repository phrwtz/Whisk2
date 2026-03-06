from backend.app.agents.env import WhiskEnv
from backend.app.game import Mark


def test_env_reset_and_initial_legal_actions_remote():
    env = WhiskEnv(mode="remote")
    env.reset()

    assert env.state.turn == 0
    assert env.current_player() is None
    assert len(env.legal_actions(Mark.O)) == 64
    assert len(env.legal_actions(Mark.X)) == 64
    assert env.is_terminal() is False
    assert env.winner() is None


def test_step_joint_commits_both_moves_and_clears_pending():
    env = WhiskEnv(mode="remote")

    result = env.step_joint((0, 0), (0, 1))

    assert result.turn == 1
    assert env.state.pending[Mark.O] is None
    assert env.state.pending[Mark.X] is None
    occ = env.state.board_occupancy()
    assert occ[(0, 0)] == Mark.O
    assert occ[(0, 1)] == Mark.X


def test_step_joint_is_transactional_on_invalid_joint_action():
    env = WhiskEnv(mode="remote")

    try:
        env.step_joint((0, 0), (0, 0))
        assert False, "Expected ValueError for duplicate square"
    except ValueError:
        pass

    assert env.state.pending[Mark.O] is None
    assert env.state.pending[Mark.X] is None
    assert env.state.board_occupancy() == {}


def test_local_mode_enforces_turn_order_and_alternates_player():
    env = WhiskEnv(mode="local")

    assert env.current_player() == Mark.O
    env.step_local(Mark.O, (0, 0))
    assert env.current_player() == Mark.X

    try:
        env.step_local(Mark.O, (0, 1))
        assert False, "Expected ValueError for wrong player"
    except ValueError:
        pass

    env.step_local(Mark.X, (0, 1))
    assert env.current_player() == Mark.O
