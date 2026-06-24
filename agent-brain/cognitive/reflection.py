"""
Reflection module — synthesizes memories into higher-level insights.

Based on the Generative Agents paper (Park et al., 2023):
When the importance of accumulated events exceeds a threshold, the agent
reflects: it generates focal points, retrieves relevant memories, and
synthesizes them into new insights (thoughts) stored in memory.

Also from Project Sid (2024): the "Action Awareness" concept of comparing
expected vs. actual outcomes feeds into reflection.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import ValisAgent

logger = logging.getLogger("valis.cognitive.reflection")


class Reflection:
    """
    Manages the reflection process: triggering conditions,
    focal point generation, insight synthesis, and memory consolidation.
    """

    def __init__(self):
        self.importance_counter: float = 0.0
        self.importance_threshold: float = 30.0  # Trigger after ~10 priority-3 decisions
        self.reflection_count: int = 0
        self.last_reflection_time: float = 0.0

    def accumulate_importance(self, amount: float):
        """Add an event's importance to the counter."""
        self.importance_counter += amount

    def should_reflect(self) -> bool:
        """Check if the importance threshold has been exceeded."""
        return self.importance_counter >= self.importance_threshold

    async def reflect(self, agent: "ValisAgent"):
        """
        Run the full reflection cycle:
        1. Generate focal points from recent events
        2. Retrieve relevant memories
        3. Synthesize insights
        4. Store reflections in memory
        """
        logger.info(f"Agent {agent.name} reflecting (count: {self.reflection_count + 1})")

        # Get recent events as focal points
        recent_events = agent.memory.get_recent(n=20, node_type="event")
        if len(recent_events) < 3:
            self.importance_counter = 0
            return

        # Build 3 focal points from recent events
        focal_points = self._generate_focal_points(agent, recent_events)

        for focal_pt in focal_points:
            # Retrieve memories related to this focal point
            memories = await agent.retrieval.retrieve(focal_pt, limit=5)

            if not memories:
                continue

            # Synthesize insight
            insight = await self._synthesize_insight(agent, focal_pt, memories)

            if insight:
                await agent.memory.add_thought(
                    content=insight,
                    importance=0.7,
                    evidence_ids=[m.node_id for m in memories],
                )

        # Reset counter
        self.importance_counter = 0
        self.reflection_count += 1
        import time
        self.last_reflection_time = time.time()

    def _generate_focal_points(
        self,
        agent: "ValisAgent",
        recent_events: list,
    ) -> list[str]:
        """Generate reflection focal points from recent events."""
        # Simple approach: group events by type and pick top-3 most important
        events_sorted = sorted(recent_events, key=lambda e: e.importance, reverse=True)

        focal_points = []
        for event in events_sorted[:5]:
            focal_points.append(
                f"Consider what {agent.name} learned from: {event.content[:150]}"
            )
        return focal_points[:3]

    async def _synthesize_insight(
        self,
        agent: "ValisAgent",
        focal_point: str,
        memories: list,
    ) -> str | None:
        """Use the LLM to synthesize a higher-level insight from memories."""
        memory_text = "\n".join(
            f"- {m.content[:200]}" for m in memories
        )

        prompt = f"""You are reflecting on recent experiences.

{focal_point}

Relevant memories:
{memory_text}

Based on these memories, what is one important insight or lesson learned?
Output a single sentence starting with "I". 
For example: "I learned that iron ore is found deep underground."

Your insight:"""

        try:
            response = await agent.llm.chat([
                {"role": "system", "content": "You are reflecting on experiences. Output one concise insight."},
                {"role": "user", "content": prompt},
            ])
            return response.strip()
        except Exception as e:
            logger.warning(f"Failed to synthesize insight: {e}")
            return None
