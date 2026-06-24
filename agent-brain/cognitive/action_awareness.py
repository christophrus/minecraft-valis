"""
Action Awareness — compares expected vs. actual outcomes of actions.

Based on the PIANO architecture (Project Sid, Altera.AL 2024):
This module grounds agents in reality by detecting when actions didn't
produce the expected result, preventing hallucination compounding.

For example: If the agent expected to mine iron but got stone,
this discrepancy is recorded and fed back into future planning.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import ValisAgent

logger = logging.getLogger("valis.cognitive.action_awareness")


@dataclass
class ExpectedOutcome:
    """What the agent expected from an action."""
    action: str
    params: dict
    expected_result: str
    timestamp: float = 0.0


@dataclass
class AwarenessRecord:
    """Record of action outcome vs. expectation."""
    expected: ExpectedOutcome
    actual_result: str
    success: bool
    discrepancy: str = ""


class ActionAwareness:
    """
    Tracks expected vs. actual outcomes of agent actions.
    Feeds discrepancies back into memory as learning events.
    """

    def __init__(self):
        self.pending: dict[str, ExpectedOutcome] = {}  # action_id -> expectation
        self.history: list[AwarenessRecord] = []
        self.error_count: int = 0
        self.success_count: int = 0

    def expect(self, action_id: str, action: str, params: dict, expected: str):
        """Register an expected outcome for a pending action."""
        import time
        self.pending[action_id] = ExpectedOutcome(
            action=action,
            params=params,
            expected_result=expected,
            timestamp=time.time(),
        )

    def observe(self, action_id: str, success: bool, details: str) -> AwarenessRecord | None:
        """Observe the actual outcome of an action."""
        expected = self.pending.pop(action_id, None)
        if expected is None:
            return None

        discrepancy = ""
        if not success:
            discrepancy = f"Action '{expected.action}' failed: {details}"
            self.error_count += 1
        else:
            self.success_count += 1

        record = AwarenessRecord(
            expected=expected,
            actual_result=details,
            success=success,
            discrepancy=discrepancy,
        )
        self.history.append(record)
        if len(self.history) > 100:
            self.history = self.history[-100:]

        return record

    async def learn_from_discrepancy(
        self,
        agent: "ValisAgent",
        record: AwarenessRecord,
    ):
        """Store a discrepancy as a learning memory."""
        if not record.discrepancy:
            return

        await agent.memory.add_event(
            content=f"[Action Awareness] {record.discrepancy}",
            importance=0.6,
            subject=agent.name,
            predicate="learned",
            object="action outcome mismatch",
        )

    def get_recent_discrepancies(self, n: int = 5) -> list[str]:
        """Get recent discrepancies for grounding context."""
        return [
            r.discrepancy for r in self.history[-n:]
            if r.discrepancy
        ]

    def get_stats(self) -> dict:
        """Get awareness statistics."""
        total = self.error_count + self.success_count
        return {
            "total_actions": total,
            "successes": self.success_count,
            "errors": self.error_count,
            "success_rate": self.success_count / total if total > 0 else 1.0,
        }
