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

        # Inventory context
        inv = agent._pending_perception.inventory if agent._pending_perception else {}
        inv_text = ", ".join(f"{k}: {v}" for k, v in inv.items()) if inv else "empty"

        # Recent memories (trimmed for speed)
        recent = agent.memory.get_recent(n=3)
        memory_text = "\n".join(
            f"- [{m.created.strftime('%H:%M')}] {m.content[:80]}"
            for m in recent
        )

        # Recent action failures — what NOT to repeat
        discrepancies = agent.action_awareness.get_recent_discrepancies(n=5)
        discrepancy_text = ""
        if discrepancies:
            discrepancy_text = "Recent failures to avoid:\n" + "\n".join(
                f"  - {d}" for d in discrepancies
            )

        prompt = f"""You control {agent.name}, an AI in Minecraft. Pick ONE action.

RAW MATERIALS I CARRY: {inv_text}

PERCEPTION:
{perception_text}

GOALS:
{goal_text}

SOCIAL: {social_text}

{memory_text}
{discrepancy_text}

DECISION RULES — choose action_hint:

mine  = Break a block to get resources. Use when: you see wood, stone, coal, iron, or need dirt for shelter. Include coordinates in intent like "Mine oak_log at (12,64,-8)".

craft = Turn raw materials into items. Use when you carry materials for a needed tool/block AND don't have it yet. CRITICAL: if inventory has logs but no planks → CRAFT. If planks ≥4 and no crafting_table → CRAFT. If planks≥3 + sticks≥2 and no pickaxe → CRAFT. The basic chain (log→plank→stick→pickaxe) runs automatically — YOU decide complex crafts like crafting_table, furnace, stone tools, doors.

CRAFTING RECIPES:
- 1 log → 4 planks (any wood)
- 4 planks → crafting_table (needed for 3×3 recipes!)
- 2 planks → 4 sticks
- 3 planks + 2 sticks → wooden_pickaxe
- 3 planks + 2 sticks → wooden_axe
- 2 planks + 1 stick → wooden_sword
- 8 cobblestone → furnace (need crafting_table)
- 3 cobblestone + 2 sticks → stone_pickaxe (need crafting_table)
- 6 planks → 2 doors

place = Put a block in the world. Use when: building shelter, placing crafting_table, or blocking yourself in at night.

move  = Walk to coordinates or toward a biome/structure. Include coords if known.

explore = Systematic exploration when you don't know what's around. Move further than 'move'.

hunt = Attack nearby animals (sheep, cow, pig, chicken, rabbit) for food. Use when hungry or need wool/leather.

socialize = Talk to nearby players/villagers.

rest/idle = Do nothing (only when waiting or nothing useful to do).

Output ONLY valid JSON:
{{"intent": "what and where", "reason": "why", "priority": 0-10, "action_hint": "mine|craft|place|move|explore|hunt|socialize|rest", "chat_hint": "or empty"}}"""

        import json, re
        for attempt in range(2):
            try:
                response = await agent.llm.chat([
                    {"role": "system", "content": "You are a decision-making module. Output only JSON."},
                    {"role": "user", "content": prompt},
                ])

                # Extract JSON from response (may be wrapped in markdown or have preamble)
                json_str = response.strip()
                # Remove markdown code fences
                json_str = re.sub(r'^```(?:json)?\s*', '', json_str)
                json_str = re.sub(r'\s*```$', '', json_str)
                # Find JSON object boundaries
                brace_start = json_str.find('{')
                brace_end = json_str.rfind('}')
                if brace_start >= 0 and brace_end > brace_start:
                    json_str = json_str[brace_start:brace_end + 1]
                if not json_str:
                    raise ValueError("Empty response from LLM")
                data = json.loads(json_str)

                decision = ControllerDecision(
                    intent=data.get("intent", "Explore the area"),
                    reason=data.get("reason", "No specific reason"),
                    priority=float(data.get("priority", 5)) / 10.0,
                    social_context=social_text,
                    action_hint=data.get("action_hint", "explore"),
                    chat_hint=data.get("chat_hint", ""),
                )
                break  # Success
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"Controller JSON parse failed (retrying): {e}")
                    prompt += "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no explanation, just the JSON object."
                else:
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
