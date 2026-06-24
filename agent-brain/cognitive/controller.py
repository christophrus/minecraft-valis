"""
Cognitive Controller — the bottlenecked decision-making module.

Based on the PIANO architecture (Project Sid, Altera.AL 2024):
The Cognitive Controller synthesizes information from multiple input streams
(perception, memory, social awareness, goals) through a bottleneck, makes a
high-level decision, and broadcasts it to output modules (speech, action,
planning) for coherence.

This addresses the problem where agents "say one thing but do another."
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import ValisAgent

logger = logging.getLogger("valis.cognitive.controller")


@dataclass
class ControllerDecision:
    """A high-level decision made by the Cognitive Controller."""
    intent: str  # What the agent intends to do
    reason: str  # Why
    priority: float = 0.5  # 0-1 priority
    social_context: str = ""  # Who this involves
    action_hint: str = ""  # Suggested action type
    chat_hint: str = ""  # What to say (if anything)


class CognitiveController:
    """
    The PIANO Cognitive Controller.
    Bottlenecks multiple input streams into a single coherent decision
    that is broadcast to action, speech, and planning modules.
    """

    def __init__(self):
        self.last_decision: ControllerDecision | None = None
        self.decision_history: list[ControllerDecision] = []

    async def decide(self, agent: "ValisAgent") -> ControllerDecision:
        """
        Synthesize inputs from perception, memory, social awareness, and goals
        into a single high-level decision.

        This is the bottleneck that ensures coherence across output modules.
        """
        # Collect inputs from all modules
        perception_text = agent.perception_processor.build_context_text()

        # Social context
        social_text = ""
        if hasattr(agent, 'social_awareness') and agent.social_awareness:
            social_text = agent.social_awareness.get_social_context()

        # Goal context
        goal_text = "\n".join(f"- {g}" for g in agent.goals)

        # Recent memories (trimmed for speed)
        recent = agent.memory.get_recent(n=3)
        memory_text = "\n".join(
            f"- [{m.created.strftime('%H:%M')}] {m.content[:80]}"
            for m in recent
        )

        prompt = f"""You are the cognitive controller for {agent.name}, an AI agent in Minecraft.

Your job is to synthesize all inputs and produce ONE high-level decision.

INPUTS:
---
Perception:
{perception_text}

Social context:
{social_text}

Goals:
{goal_text}

Recent memories:
{memory_text}
---

Output a JSON object with these fields:
- "intent": What you intend to do (one sentence)
- "reason": Why you chose this intent (one sentence)
- "priority": A number 0-10 indicating urgency
- "action_hint": One of: move, mine, place, craft, explore, socialize, rest
- "chat_hint": What to say (empty string if not speaking)

Output ONLY the JSON, nothing else."""

        try:
            response = await agent.llm.chat([
                {"role": "system", "content": "You are a decision-making module. Output only JSON."},
                {"role": "user", "content": prompt},
            ])

            import json
            # Extract JSON from response (may be wrapped in markdown)
            json_str = response.strip()
            if json_str.startswith("```"):
                json_str = json_str.split("\n", 1)[1]
                if json_str.endswith("```"):
                    json_str = json_str[:-3]
            data = json.loads(json_str)

            decision = ControllerDecision(
                intent=data.get("intent", "Explore the area"),
                reason=data.get("reason", "No specific reason"),
                priority=float(data.get("priority", 5)) / 10.0,
                social_context=social_text,
                action_hint=data.get("action_hint", "explore"),
                chat_hint=data.get("chat_hint", ""),
            )
        except Exception as e:
            logger.warning(f"Controller decision failed, using fallback: {e}")
            decision = ControllerDecision(
                intent="Explore the area and gather resources",
                reason="Default exploration behavior",
                priority=0.5,
                action_hint="explore",
                chat_hint="",
            )

        self.last_decision = decision
        self.decision_history.append(decision)
        if len(self.decision_history) > 50:
            self.decision_history = self.decision_history[-50:]

        return decision
