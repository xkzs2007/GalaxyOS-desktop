"""cumulative_regret.py — Evaluation metrics for the ACRouter.

Implements the metrics from arXiv:2606.22902 Section 4 (Eq. 9-10):

    δ_i   = r*_i - r_i(a_i)
    CumReg_N(π) = Σ_{i=1..N} δ_i

    r_i(a) = ε_1 · s_i(a) - ε_2 · κ_i(a)
    (ε_1, ε_2) = (1.0, 0.1)        # score, -cost

    r*_i = max_j r_i(a_j)          # oracle reward per task

A lower cumulative regret = the router is making better decisions.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── Reward function (Eq. 9) ──────────────────────────────────────

def reward(score: float, cost: float,
           epsilon_score: float = 1.0,
           epsilon_cost: float = 0.1) -> float:
    """r_i(a) = ε_1 · s_i(a) - ε_2 · κ_i(a)

    score ∈ [0, 1] is the Verifier's u_i.
    cost  ∈ USD-equivalent is the per-action cost.
    """
    return epsilon_score * float(score) - epsilon_cost * float(cost)


# ── Per-task regret computation ─────────────────────────────────

@dataclass
class TaskReward:
    """All action rewards for one task (one row of the R matrix)."""
    task_id: int
    query: str
    actions: Dict[str, float] = field(default_factory=dict)
    """action name -> r_i(action) reward."""
    chosen: Optional[str] = None
    chosen_reward: Optional[float] = None
    oracle_reward: Optional[float] = None
    regret: Optional[float] = None


def compute_task_reward(
    task_id: int,
    query: str,
    chosen_action: str,
    action_scores: Dict[str, float],
    action_costs: Dict[str, float],
) -> TaskReward:
    """Compute per-task regret given the chosen action + oracle."""
    rewards = {a: reward(action_scores[a], action_costs[a])
               for a in action_scores}
    oracle = max(rewards.values()) if rewards else 0.0
    chosen_r = rewards.get(chosen_action, 0.0)
    return TaskReward(
        task_id=task_id,
        query=query,
        actions=rewards,
        chosen=chosen_action,
        chosen_reward=chosen_r,
        oracle_reward=oracle,
        regret=oracle - chosen_r,
    )


# ── Cumulative regret stream evaluator ──────────────────────────

class RegretEvaluator:
    """Compute cumulative regret over a stream of routing decisions.

    Usage:
        eval = RegretEvaluator()
        for task in stream:
            tr = compute_task_reward(...)
            eval.add(tr)
        print(eval.summary())
    """

    def __init__(self):
        self.tasks: List[TaskReward] = []

    def add(self, tr: TaskReward) -> None:
        self.tasks.append(tr)

    @property
    def cumulative_regret(self) -> float:
        return sum(t.regret for t in self.tasks if t.regret is not None)

    @property
    def mean_regret(self) -> float:
        if not self.tasks: return 0.0
        return self.cumulative_regret / len(self.tasks)

    @property
    def action_distribution(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for t in self.tasks:
            if t.chosen:
                out[t.chosen] = out.get(t.chosen, 0) + 1
        return out

    @property
    def oracle_match_rate(self) -> float:
        """% of tasks where chosen action was the oracle (regret ~ 0)."""
        if not self.tasks: return 0.0
        n_match = sum(1 for t in self.tasks
                       if t.regret is not None and t.regret < 0.01)
        return n_match / len(self.tasks)

    def summary(self) -> Dict[str, object]:
        return {
            "n_tasks": len(self.tasks),
            "cumulative_regret": round(self.cumulative_regret, 3),
            "mean_regret": round(self.mean_regret, 4),
            "oracle_match_rate": round(self.oracle_match_rate, 3),
            "action_distribution": self.action_distribution,
        }

    def save(self, path: Path) -> None:
        with path.open("w", encoding="utf-8") as f:
            json.dump({
                "n_tasks": len(self.tasks),
                "summary": self.summary(),
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "query": t.query,
                        "actions": t.actions,
                        "chosen": t.chosen,
                        "regret": t.regret,
                    }
                    for t in self.tasks
                ],
            }, f, ensure_ascii=False, indent=2)

"""Self-test skipped (see tests/test_cumulative_regret.py for unit tests)."""
