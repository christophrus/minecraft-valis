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
        # 50.0 caused a reflection every ~55s per agent (505 in one session) and
        # a belief churn of ~20 slot changes per agent-hour. Doubled: reflect on
        # substantial accumulated experience, not on routine.
        self.importance_threshold: float = 100.0
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
        Run reflection in a SINGLE structured LLM call (was 7: 1 focal-point +
        3 synthesize + 3 novelty). The model reads recent events + existing
        thoughts and returns up to 3 insights, each with a self-assessed
        importance and an explicit novelty judgement. Novelty is then verified
        cheaply via embedding cosine similarity — no extra LLM calls.

        This cut reflection from ~41% of all LLM calls to a single call while
        keeping insights feeding back into retrieval and beliefs.
        """
        import json, re, time
        logger.info(f"Agent {agent.name} reflecting (count: {self.reflection_count + 1})")

        recent_events = agent.memory.get_recent(n=20, node_type="event")
        if len(recent_events) < 3:
            self.importance_counter = 0
            return

        events_sorted = sorted(recent_events, key=lambda e: e.importance, reverse=True)
        event_text = "\n".join(
            f"- (imp={e.importance:.1f}) {e.content[:120]}" for e in events_sorted[:10]
        )
        recent_thoughts = agent.memory.get_recent(n=8, node_type="thought")
        thought_text = "\n".join(f"- {t.content[:130]}" for t in recent_thoughts[:6]) or "(none yet)"

        prompt = (
            f"You are {agent.name}, a {agent.personality} reflecting on recent "
            f"experiences in Minecraft. Derive 1-3 genuinely NEW higher-level "
            f"insights (patterns, lessons, strategies). Do NOT restate things you "
            f"already concluded.\n\n"
            f"RECENT EXPERIENCES:\n{event_text}\n\n"
            f"INSIGHTS YOU ALREADY HOLD (do not repeat these):\n{thought_text}\n\n"
            f"Output ONLY a JSON array. Each item: "
            f'{{"insight": "one sentence starting with I", "importance": 1-10, '
            f'"is_new": true/false}}. Only include items where is_new is true. '
            f"If nothing is genuinely new, output []."
        )
        insights: list[dict] = []
        try:
            response = await agent.llm.chat([
                {"role": "system", "content": "You reflect and output ONLY a JSON array of new insights."},
                {"role": "user", "content": prompt},
            ], max_tokens=500)
            js = response.strip()
            js = re.sub(r'^```(?:json)?\s*', '', js)
            js = re.sub(r'\s*```$', '', js)
            a, b = js.find('['), js.rfind(']')
            if a >= 0 and b > a:
                insights = json.loads(js[a:b + 1])
        except Exception as e:
            logger.warning(f"REFLECTION: single-call synthesis failed: {e}")

        # Pre-embed existing thoughts once for cheap novelty checks
        thought_embeds = [t.embedding for t in recent_thoughts if t.embedding]

        for item in insights[:3]:
            if not isinstance(item, dict):
                continue
            insight = str(item.get("insight", "")).strip()
            if not insight or not item.get("is_new", True):
                continue
            raw_imp = item.get("importance", 5)
            try:
                importance = max(0.1, min(1.0, float(raw_imp) / 10.0))
            except (TypeError, ValueError):
                importance = 0.5

            # Novelty via embedding cosine similarity (no LLM call)
            novelty_ok = True
            try:
                emb = await agent.llm.embed(insight)
                for te in thought_embeds:
                    if self._cosine(emb, te) > 0.92:
                        novelty_ok = False
                        break
            except Exception:
                emb = None
            if not novelty_ok:
                logger.debug(f"REFLECTION: discarding near-duplicate insight: {insight[:80]}")
                continue
            if importance < 0.30:
                logger.debug(f"REFLECTION: discarding low-value insight (imp={importance:.2f}): {insight[:80]}")
                continue

            await agent.memory.add_thought(content=insight, importance=importance)
            logger.info(f"REFLECTION: stored insight (imp={importance:.2f}): {insight[:100]}")
            if emb is not None:
                thought_embeds.append(emb)
            # 0.7, not 0.55: the single-call format hands out 6-8/10 generously,
            # which made nearly every insight a conviction (924 formations/run).
            # Only genuinely important lessons should shape a personality.
            if importance >= 0.7 and hasattr(agent, "adopt_belief"):
                agent.adopt_belief(insight, source="own reflection", importance=importance)

        self.importance_counter = 0
        self.reflection_count += 1
        self.last_reflection_time = time.time()

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0
