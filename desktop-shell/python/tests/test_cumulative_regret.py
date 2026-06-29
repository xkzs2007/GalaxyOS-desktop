"""tests/test_cumulative_regret.py - unit tests for cumulative_regret.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from cumulative_regret import (
    reward, compute_task_reward, RegretEvaluator,
)


def test_reward_basic():
    """r(s, c) = s - 0.1*c"""
    assert abs(reward(1.0, 0.0) - 1.0) < 0.001
    assert abs(reward(0.5, 0.0) - 0.5) < 0.001
    assert abs(reward(1.0, 1.0) - 0.9) < 0.001
    assert abs(reward(0.0, 1.0) - -0.1) < 0.001
    print("test_reward_basic PASSED")


def test_compute_task_reward():
    """compute_task_reward returns TaskReward with regret = oracle - chosen."""
    scores = {"a": 0.5, "b": 0.8, "c": 0.3}
    costs = {"a": 0.001, "b": 0.010, "c": 0.005}
    tr = compute_task_reward(
        task_id=0, query="q", chosen_action="a",
        action_scores=scores, action_costs=costs,
    )
    # Oracle = b (0.8 - 0.001 = 0.799); chosen = a (0.5 - 0.0001 = 0.4999)
    assert tr.chosen == "a"
    assert abs(tr.chosen_reward - 0.4999) < 0.001
    assert abs(tr.oracle_reward - 0.799) < 0.001
    assert abs(tr.regret - (0.799 - 0.4999)) < 0.01
    print("test_compute_task_reward PASSED")


def test_evaluator_cumulative():
    """A 'good' router (picks oracle) has lower cum_regret than a 'bad' one."""
    ACTION_COSTS = {
        "fast_path": 0.001, "liquid_only": 0.002,
        "memo_3stage": 0.003, "process_5_stage": 0.010,
    }
    TASKS = [
        ("What is GalaxyOS",  {"fast_path": 0.5, "liquid_only": 0.6, "memo_3stage": 0.95, "process_5_stage": 0.5}),
        ("!ls -la",          {"fast_path": 0.2, "liquid_only": 0.3, "memo_3stage": 0.2, "process_5_stage": 0.9}),
        ("Hi",               {"fast_path": 0.9, "liquid_only": 0.5, "memo_3stage": 0.3, "process_5_stage": 0.2}),
        ("Tell me about R-CCAM", {"fast_path": 0.5, "liquid_only": 0.6, "memo_3stage": 0.9, "process_5_stage": 0.5}),
    ]
    eval_good = RegretEvaluator()
    eval_bad = RegretEvaluator()
    for i, (q, scores) in enumerate(TASKS):
        good_action = max(scores, key=lambda a: reward(scores[a], ACTION_COSTS[a]))
        eval_good.add(compute_task_reward(i, q, good_action, scores, ACTION_COSTS))
        eval_bad.add(compute_task_reward(i, q, "fast_path", scores, ACTION_COSTS))

    assert eval_good.cumulative_regret <= eval_bad.cumulative_regret
    print(f"test_evaluator_cumulative PASSED (good={eval_good.cumulative_regret:.3f}, bad={eval_bad.cumulative_regret:.3f})")


def test_evaluator_summary():
    """Summary dict has all expected keys."""
    eval = RegretEvaluator()
    eval.add(compute_task_reward(0, "q", "a",
        {"a": 0.9, "b": 0.3}, {"a": 0.001, "b": 0.005}))
    s = eval.summary()
    assert "n_tasks" in s
    assert "cumulative_regret" in s
    assert "mean_regret" in s
    assert "oracle_match_rate" in s
    assert "action_distribution" in s
    assert s["n_tasks"] == 1
    assert s["cumulative_regret"] >= 0
    print("test_evaluator_summary PASSED")


if __name__ == "__main__":
    test_reward_basic()
    test_compute_task_reward()
    test_evaluator_cumulative()
    test_evaluator_summary()
    print()
    print("All cumulative_regret tests passed.")
