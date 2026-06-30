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
        Uses weighted retrieval (recency × relevance × importance) instead of
        raw recent memories, and includes reflection insights.
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

        # Weighted retrieval (paper-conformant: recency × relevance × importance)
        query = f"Current goals: {goal_text}. Perception: {perception_text[:200]}"
        try:
            retrieved = await agent.retrieval.retrieve(
                query, limit=5, embedding_fn=agent.llm.embed,
            )
        except Exception as e:
            logger.warning(f"Retrieval failed, falling back to recent: {e}")
            retrieved = agent.memory.get_recent(n=5)

        memory_text = "\n".join(
            f"- [{m.created.strftime('%H:%M')}] (imp={m.importance:.1f}) {m.content[:80]}"
            for m in retrieved
        )

        # Reflection insights — higher-level patterns the agent has learned
        recent_reflections = agent.memory.get_recent(n=3, node_type="thought")
        reflection_text = ""
        if recent_reflections:
            reflection_text = "Insights from reflection:\n" + "\n".join(
                f"  - {r.content[:120]}" for r in recent_reflections
            )

        # Daily plan context
        plan_text = ""
        if hasattr(agent, 'planner') and agent.planner.daily_plan:
            current = agent.planner.current_task
            plan_text = f"Current task: {current}\nDaily plan: " + " | ".join(
                agent.planner.daily_plan[:4]
            )

        # Recent action failures — what NOT to repeat
        discrepancies = agent.action_awareness.get_recent_discrepancies(n=5)
        discrepancy_text = ""
        if discrepancies:
            discrepancy_text = "Recent failures to avoid:\n" + "\n".join(
                f"  - {d}" for d in discrepancies
            )

        # Craftable items (computed server-side from Bukkit recipes)
        craft_text = ""
        perception = agent._pending_perception
        if perception and perception.craftable:
            items = [f"{c.get('amount',1)}x {c.get('item','?')} ({c.get('cost','?')})"
                     for c in perception.craftable[:8]]
            craft_text = "CAN CRAFT NOW: " + " | ".join(items)
        almost_text = ""
        if perception and perception.almost_craftable:
            items = [f"{c.get('item','?')} (need: {c.get('missing','?')})"
                     for c in perception.almost_craftable[:5]]
            almost_text = "ALMOST CRAFTABLE: " + " | ".join(items)

        # Personality block — biases decisions toward role specialization
        personality_block = ""
        traits = getattr(agent, 'traits', None) or []
        focus = getattr(agent, 'focus', "") or ""
        if traits or focus:
            trait_str = ", ".join(traits) if traits else "balanced"
            personality_block = (
                f"YOU ARE A {agent.personality.upper()} (traits: {trait_str}).\n"
                f"ROLE FOCUS: {focus}\n"
                f"Bias decisions toward your role. Only deviate when survival demands it.\n"
            )

        # Settlement context — shared village state (neutral info, LLM decides)
        settlement_block = ""
        settlement = getattr(agent, 'settlement', None)
        if settlement:
            _agent_pos = None
            _is_day = None
            if perception:
                _agent_pos = (
                    int(perception.position.get("x", 0)),
                    int(perception.position.get("y", 0)),
                    int(perception.position.get("z", 0)),
                )
                _is_day = perception.is_day
            settlement_block = settlement.get_context_for_prompt(
                agent_name=agent.name, agent_pos=_agent_pos, is_day=_is_day
            )

        # Nearby agents — who else is around
        nearby_agents_block = ""
        if perception and perception.nearby_entities:
            agent_entities = [
                e for e in perception.nearby_entities
                if e.get("type", "").upper() in ("PLAYER", "NPC", "CITIZEN")
                and e.get("name", "") != agent.name
                and not e.get("is_player", False)
            ]
            if agent_entities:
                descs = [f"{e.get('name','?')} ({e.get('distance',0):.0f}m away)" for e in agent_entities[:5]]
                nearby_agents_block = "VILLAGE MEMBERS NEARBY: " + ", ".join(descs)

        # Nearby chat — from agent's chat inbox (accumulated across perceptions)
        nearby_chat_block = ""
        _inbox = getattr(agent, '_chat_inbox', [])
        if _inbox:
            nearby_chat_block = "HEARD RECENTLY:\n" + "\n".join(
                f"  {msg}" for msg in _inbox[-5:]
            )

        # Village Council assignment — strategic task from the village planner
        council_block = ""
        _assignment = getattr(agent, '_council_assignment', "")
        if _assignment:
            council_block = f"VILLAGE COUNCIL ASSIGNMENT: {_assignment}"

        prompt = f"""You control {agent.name}, an AI in Minecraft. Pick ONE action. Be concise — output ONLY JSON, max 300 chars.

{personality_block}
{council_block}
{settlement_block}
{nearby_agents_block}
{nearby_chat_block}
INVENTORY: {inv_text}

PERCEPTION:
{perception_text}

{craft_text}
{almost_text}

PLAN: {plan_text}
GOALS: {goal_text}
SOCIAL: {social_text}

RELEVANT MEMORIES (weighted by importance+recency+relevance):
{memory_text}

{reflection_text}
{discrepancy_text}

action_hint choices: mine|craft|place|build|move|explore|hunt|socialize|give|deposit|withdraw|rest

To craft: use action_hint "craft" and specify the item name in intent. Only craft items listed in CAN CRAFT NOW.
To get missing materials: mine or gather what ALMOST CRAFTABLE shows.
To build a shelter: use action_hint "build". The agent will construct a 3x3 shelter automatically.
To give items to another agent: use action_hint "give" and specify "give [item] to [AgentName]" in intent. You must be near the target agent.
To deposit surplus items into the village chest: use action_hint "deposit" and specify "deposit [item] [amount]" in intent. You must be near the settlement center.
To withdraw items from the village chest: use action_hint "withdraw" and specify "withdraw [item] [amount]" in intent. You must be near the settlement center.
chat_hint: optional short message spoken aloud in Minecraft chat. Other agents and players can hear it. Use it to coordinate, share info, or respond to what you heard. Leave empty if nothing to say.

VILLAGE ECONOMY: The village chest at the settlement center is the shared storage. After gathering resources, RETURN to center and deposit surplus. Before building, check the chest for materials. This loop (gather → return → deposit → repeat) is how the village grows.
If you have a VILLAGE COUNCIL ASSIGNMENT, follow it — it coordinates the whole village.

Output ONLY JSON:
{{"intent": "what and where", "reason": "why (1 sentence)", "priority": 0-10, "action_hint": "mine|craft|...", "chat_hint": ""}}"""

        import json, re
        decision = None
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                response = await agent.llm.chat([
                    {"role": "system", "content": "You are a decision-making module. Output only JSON."},
                    {"role": "user", "content": prompt},
                ])

                # Extract JSON from response (may be wrapped in markdown or have preamble)
                json_str = response.strip()
                if not json_str:
                    raise ValueError(f"Empty response from LLM (attempt {attempt + 1}/{max_attempts})")
                # Remove markdown code fences
                json_str = re.sub(r'^```(?:json)?\s*', '', json_str)
                json_str = re.sub(r'\s*```$', '', json_str)
                # Find JSON object boundaries
                brace_start = json_str.find('{')
                brace_end = json_str.rfind('}')
                if brace_start >= 0 and brace_end > brace_start:
                    json_str = json_str[brace_start:brace_end + 1]
                if not json_str:
                    raise ValueError("No JSON object found in response")
                data = json.loads(json_str)

                _intent = data.get("intent", "Explore the area")
                _reason = data.get("reason", "No specific reason")
                decision = ControllerDecision(
                    intent=str(_intent) if not isinstance(_intent, str) else _intent,
                    reason=str(_reason) if not isinstance(_reason, str) else _reason,
                    priority=float(data.get("priority", 5)) / 10.0,
                    social_context=social_text,
                    action_hint=str(data.get("action_hint", "explore")),
                    chat_hint=str(data.get("chat_hint", "")),
                )
                break  # Success
            except Exception as e:
                if attempt < max_attempts - 1:
                    logger.warning(f"Controller JSON parse failed (attempt {attempt + 1}/{max_attempts}): {e}")
                    prompt += "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no explanation, just the JSON object."
                else:
                    logger.warning(f"Controller decision failed after {max_attempts} attempts, using fallback: {e}")

        if decision is None:
            decision = ControllerDecision(
                intent="Explore the area and gather resources",
                reason="Default exploration behavior (LLM unavailable)",
                priority=0.5,
                action_hint="explore",
                chat_hint="",
            )

        self.last_decision = decision
        self.decision_history.append(decision)
        if len(self.decision_history) > 50:
            self.decision_history = self.decision_history[-50:]

        return decision
