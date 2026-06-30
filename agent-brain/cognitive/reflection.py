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
        self.importance_threshold: float = 50.0  # ~10 high-priority decisions
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
        Run the full reflection cycle (Generative Agents paper):
        1. Generate focal points via LLM from recent high-importance events
        2. Retrieve relevant memories using weighted retrieval
        3. Synthesize insights via LLM
        4. Store reflections with scored importance — these feed back into
           future retrieval and controller decisions as "thought" nodes
        """
        logger.info(f"Agent {agent.name} reflecting (count: {self.reflection_count + 1})")

        recent_events = agent.memory.get_recent(n=20, node_type="event")
        if len(recent_events) < 3:
            self.importance_counter = 0
            return

        focal_points = await self._generate_focal_points_llm(agent, recent_events)

        for focal_pt in focal_points:
            memories = await agent.retrieval.retrieve(
                focal_pt, limit=5, embedding_fn=agent.llm.embed,
            )
            if not memories:
                continue

            insight = await self._synthesize_insight(agent, focal_pt, memories)
            if insight:
                recent_thoughts = agent.memory.get_recent(n=10, node_type="thought")
                importance = await self._score_with_novelty(
                    agent, insight, recent_thoughts
                )
                if importance < 0.30:
                    logger.debug(f"REFLECTION: discarding low-value insight (imp={importance:.2f}): {insight[:80]}")
                    continue
                await agent.memory.add_thought(
                    content=insight,
                    importance=importance,
                    evidence_ids=[m.node_id for m in memories],
                )
                logger.info(f"REFLECTION: stored insight (imp={importance:.2f}): {insight[:100]}")

        self.importance_counter = 0
        self.reflection_count += 1
        import time
        self.last_reflection_time = time.time()

    async def _generate_focal_points_llm(
        self,
        agent: "ValisAgent",
        recent_events: list,
    ) -> list[str]:
        """Generate reflection focal points via LLM from high-importance events."""
        events_sorted = sorted(recent_events, key=lambda e: e.importance, reverse=True)
        event_text = "\n".join(
            f"- (imp={e.importance:.1f}) {e.content[:120]}" for e in events_sorted[:10]
        )

        prompt = (
            f"Given these recent experiences of {agent.name} in Minecraft, "
            f"generate exactly 3 questions worth reflecting on. "
            f"Focus on patterns, lessons, and strategies.\n\n"
            f"Recent events:\n{event_text}\n\n"
            f"Output exactly 3 questions, one per line:"
        )
        try:
            response = await agent.llm.chat([
                {"role": "system", "content": "Generate reflection questions. Output 3 questions, one per line."},
                {"role": "user", "content": prompt},
            ])
            questions = [
                q.strip().lstrip("0123456789.-) ") for q in response.strip().split("\n")
                if q.strip() and len(q.strip()) > 10
            ]
            if questions:
                return questions[:3]
        except Exception as e:
            logger.warning(f"LLM focal point generation failed: {e}")

        focal_points = []
        for event in events_sorted[:5]:
            focal_points.append(
                f"Consider what {agent.name} learned from: {event.content[:150]}"
            )
        return focal_points[:3]

    async def _score_with_novelty(
        self,
        agent: "ValisAgent",
        insight: str,
        recent_thoughts: list,
    ) -> float:
        """Score importance with novelty penalty for repetitive insights."""
        if recent_thoughts:
            thought_text = "\n".join(
                f"- {t.content[:150]}" for t in recent_thoughts[:5]
            )
            prompt = (
                "Rate the importance AND novelty of this Minecraft agent insight "
                "on a scale of 1-10. Use the FULL range:\n"
                "  1-2: Repetitive or trivial (restates something already known)\n"
                "  3-4: Minor or partially redundant observation\n"
                "  5-6: Moderately useful new learning\n"
                "  7-8: Significant new strategy or important lesson\n"
                "  9-10: Critical breakthrough or fundamental new understanding\n\n"
                "IMPORTANT: If the new insight says essentially the same thing as "
                "a recent thought (even in different words), rate it 1-3.\n\n"
                f"Recent thoughts the agent already has:\n{thought_text}\n\n"
                f"New insight to rate: \"{insight[:300]}\"\n\n"
                "Respond with ONLY the number."
            )
            try:
                response = await agent.llm.chat([
                    {"role": "system", "content": "Rate insight novelty and importance 1-10. Duplicates of existing thoughts score 1-3. Output only a number."},
                    {"role": "user", "content": prompt},
                ])
                import re
                match = re.search(r'(\d+)', response.strip())
                if match:
                    raw = int(match.group(1))
                    return max(0.1, min(1.0, raw / 10.0))
            except Exception as e:
                logger.warning(f"Novelty scoring failed: {e}")
        return await agent.memory.score_importance(insight)

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
