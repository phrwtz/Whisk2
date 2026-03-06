import random

from backend.app.agents.baselines import CenterBiasAgent, GreedyScoreAgent, RandomAgent
from backend.app.agents.eval import Arena
from backend.app.game import Mark


def test_random_agent_selects_legal_action():
    agent = RandomAgent()
    rng = random.Random(3)
    from backend.app.agents.env import WhiskEnv

    env = WhiskEnv(mode="remote")
    env.reset()

    action = agent.select_action(env, Mark.O, rng)
    assert action in env.legal_actions(Mark.O)


def test_greedy_agent_prefers_immediate_scoring_move():
    from backend.app.agents.env import WhiskEnv

    env = WhiskEnv(mode="remote")
    env.reset()

    # Create O pieces on row 0 except the middle, so (0,2) yields a 5-in-row.
    env.step_joint((0, 0), (7, 7))
    env.step_joint((0, 1), (7, 6))
    env.step_joint((0, 3), (7, 5))
    env.step_joint((0, 4), (7, 4))

    agent = GreedyScoreAgent()
    action = agent.select_action(env, Mark.O, random.Random(1))
    assert action == (0, 2)


def test_arena_run_accounting_is_consistent():
    arena = Arena(max_turns=40)
    summary = arena.run(RandomAgent(), CenterBiasAgent(), games=6, seed=11)

    assert summary.games == 6
    assert summary.wins_o + summary.wins_x + summary.ties == 6
    assert summary.avg_turns <= 40
