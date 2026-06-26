"""
Planning module — generates short-term and long-term plans for the agent.

Based on the Generative Agents paper (Park et al., 2023):
- Long-term: daily schedule generated at start of each day
- Short-term: moment-to-moment action selection based on current context
"""

import datetime
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import ValisAgent
    from ..memory.memory_stream import MemoryStream
    from ..memory.retrieval import MemoryRetrieval

logger = logging.getLogger("valis.cognitive.planning")


class Planner:
    """
    Handles agent planning at three levels (Generative Agents paper):
    1. Daily schedule — broad goals for the Minecraft day
    2. Hourly blocks — decomposed into concrete sub-tasks
    3. Moment-to-moment — action selection based on current context
    """

    def __init__(self):
        self.daily_plan: list[str] = []
        self.hourly_tasks: list[str] = []
        self.current_task: str = ""
        self.current_task_index: int = 0
        self.last_daily_plan_time: datetime.datetime | None = None
        self.tasks_completed: int = 0

    def advance_task(self):
        """Move to the next hourly task when the current one is done."""
        self.current_task_index += 1
        self.tasks_completed += 1
        tasks = self.hourly_tasks or self.daily_plan
        if self.current_task_index < len(tasks):
            self.current_task = tasks[self.current_task_index]
        else:
            self.current_task_index = 0
            self.current_task = tasks[0] if tasks else ""
        logger.info(f"Task advanced → [{self.current_task_index}] {self.current_task}")

    async def plan_daily(self, agent: "ValisAgent") -> list[str]:
        """
        Generate a hierarchical daily plan:
        1. Ask LLM for 3-6 high-level goals
        2. Decompose each goal into 2-3 concrete sub-tasks with coordinates
        """
        context = agent.perception_processor.build_context_text()
        personality = agent.personality
        goals = agent.goals
        memory_context = await self._get_memory_context(agent)

        # Reflection insights for planning context
        recent_reflections = agent.memory.get_recent(n=3, node_type="thought")
        reflection_text = ""
        if recent_reflections:
            reflection_text = "Lessons learned:\n" + "\n".join(
                f"- {r.content[:120]}" for r in recent_reflections
            )

        prompt = f"""You are {agent.name}, a {personality} in a Minecraft world.

Your traits: {personality}

{context}

Recent memories:
{memory_context}

{reflection_text}

Your current goals:
{chr(10).join(f'- {g}' for g in goals)}

Generate a daily plan with 3-5 high-level goals, each decomposed into 2-3 concrete sub-tasks.
Use exact coordinates from "Nearby blocks" when possible.
Use "Biomes in the distance" to decide WHERE to explore.
Use "CAN CRAFT NOW" and "ALMOST CRAFTABLE" to drive tech progression: include concrete steps to gather the missing materials for ALMOST CRAFTABLE upgrades and advance your tools to the next tier (wood → stone → iron) instead of repeating items you already own.

Format:
## Goal 1: <high-level goal>
- <concrete sub-task with coordinates/details>
- <concrete sub-task>
## Goal 2: <high-level goal>
- <concrete sub-task>
- <concrete sub-task>"""

        response = await agent.llm.chat([
            {"role": "system", "content": "You are an AI agent in Minecraft. Output the structured plan, no preamble."},
            {"role": "user", "content": prompt},
        ])
        if not response or not response.strip():
            logger.warning(f"Agent {agent.name} plan LLM returned empty, retrying...")
            response = await agent.llm.chat([
                {"role": "system", "content": "You MUST output a plan. Format: ## Goal: title, then - sub-tasks."},
                {"role": "user", "content": prompt},
            ])

        daily_goals, hourly_tasks = self._parse_hierarchical_plan(response)

        self.daily_plan = daily_goals if daily_goals else ["Explore the area", "Gather resources", "Find shelter"]
        self.hourly_tasks = hourly_tasks if hourly_tasks else self.daily_plan[:]
        if not daily_goals:
            logger.warning(f"Agent {agent.name} plan parse FAILED. Raw: '{response[:300]}'")
        self.last_daily_plan_time = datetime.datetime.now()
        self.current_task_index = 0
        self.current_task = self.hourly_tasks[0] if self.hourly_tasks else ""

        logger.info(f"Agent {agent.name} daily plan: {self.daily_plan}")
        logger.info(f"Agent {agent.name} hourly tasks ({len(self.hourly_tasks)}): {self.hourly_tasks}")
        return self.daily_plan

    def _parse_hierarchical_plan(self, response: str) -> tuple[list[str], list[str]]:
        """Parse a hierarchical plan into daily goals and hourly sub-tasks."""
        daily_goals = []
        hourly_tasks = []
        current_goal = None

        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            if line.startswith("## ") or line.startswith("# "):
                goal = line.lstrip("# ").strip()
                if goal.lower().startswith("goal"):
                    goal = goal.split(":", 1)[-1].strip() if ":" in goal else goal
                if goal:
                    current_goal = goal
                    daily_goals.append(goal)
            elif line.startswith("- ") or line.startswith("* "):
                task = line[2:].strip()
                if task:
                    hourly_tasks.append(task)
            elif line.startswith("-"):
                task = line[1:].strip()
                if task:
                    hourly_tasks.append(task)
            elif line and line[0].isdigit() and ". " in line[:4]:
                task = line.split(". ", 1)[1].strip()
                if task:
                    if current_goal is None:
                        daily_goals.append(task)
                    else:
                        hourly_tasks.append(task)
            elif line and not line.startswith(("#", "//", "```")) and len(line) > 5:
                if current_goal is None:
                    daily_goals.append(line)
                else:
                    hourly_tasks.append(line)

        return daily_goals, hourly_tasks

    async def decide_action(
        self,
        agent: "ValisAgent",
    ) -> str:
        """
        Decide the next immediate action based on the current context,
        daily plan, and retrieved memories.
        Returns an action string like: "move_to(x,y,z)", "mine_block(type,x,y,z)", etc.
        """
        context = agent.perception_processor.build_context_text()

        # Retrieve relevant memories
        query = f"What should I do now? Current task: {self.current_task}. {context}"
        memories = await agent.retrieval.retrieve(query, limit=5)

        # Relevant memories (trimmed for speed)
        memory_text = "\n".join(
            f"- [{m.created.strftime('%H:%M')}] {m.content[:100]}"
            for m in memories
        )

        # Inventory for crafting decisions
        inv = agent._pending_perception.inventory if agent._pending_perception else {}
        inv_text = ", ".join(f"{k}: {v}" for k, v in inv.items()) if inv else "empty"

        # Recent action failures — what NOT to repeat
        discrepancies = agent.action_awareness.get_recent_discrepancies(n=3)
        discrepancy_text = ""
        if discrepancies:
            discrepancy_text = "Recent mistakes to avoid:\n" + "\n".join(
                f"  - {d}" for d in discrepancies
            )

        # Craftable items from server
        perception = agent._pending_perception
        craft_text = ""
        if perception and perception.craftable:
            items = [f"{c.get('amount',1)}x {c.get('item','?')} (costs: {c.get('cost','?')})"
                     for c in perception.craftable[:8]]
            craft_text = "CAN CRAFT NOW: " + " | ".join(items)
        almost_text = ""
        if perception and perception.almost_craftable:
            items = [f"{c.get('item','?')} (need: {c.get('missing','?')})"
                     for c in perception.almost_craftable[:5]]
            almost_text = "ALMOST CRAFTABLE: " + " | ".join(items)

        prompt = f"""You are {agent.name} in Minecraft. You must choose ONE action to perform right now.

Current situation:
{context}

Your daily plan:
{chr(10).join(f'- {t}' for t in self.daily_plan)}

Current task: {self.current_task}

Inventory: {inv_text}

{craft_text}
{almost_text}

Relevant memories:
{memory_text}

{discrepancy_text}

Available actions:
- move_to(x, y, z): Walk to coordinates
- mine_block(x, y, z): Mine the block at given position — use exact coordinates from "Nearby blocks" above!
- place_block(block_type, x, y, z): Place a single block
- build(type=shelter, material=dirt, x, y, z): Build a 3x3 structure (shelter/hut) at the given position. Uses material from inventory. Much faster than placing blocks one by one! Use this when you need a shelter.
- craft(item): Craft an item — ONLY items listed in "CAN CRAFT NOW". Use exact item names.
- look_at(x, y, z): Look at a position
- chat(message): Send a chat message
- idle: Do nothing this tick

CRAFTING STRATEGY (tech progression — wood → stone → iron):
- Only craft items shown in CAN CRAFT NOW, using exact item names.
- Do NOT re-craft a tool you already own (check Inventory). If you have a wooden pickaxe, work toward a STONE pickaxe next, then IRON.
- Treat ALMOST CRAFTABLE as concrete upgrade targets: read the missing material and go obtain it. Example — if "stone_pickaxe (need: 3 cobblestone)" is shown, mine stone (dig down if no stone is nearby) to get cobblestone, then craft it.
- Prefer upgrading to better gear over re-gathering materials you already have enough of.

MOVEMENT: Use mine_block with exact coordinates from "Nearby blocks". If a needed block is not nearby, use move_to toward it (e.g. dig downward to reach stone).

Respond with exactly ONE action in format: action_name(param1=value1, param2=value2, ...)"""

        response = await agent.llm.chat([
            {"role": "system", "content": "You control a Minecraft agent. Output ONLY the action, nothing else."},
            {"role": "user", "content": prompt},
        ])
        # Retry once if empty response
        if not response or not response.strip():
            logger.warning(f"Agent {agent.name} decide_action LLM returned empty, retrying...")
            response = await agent.llm.chat([
                {"role": "system", "content": "Output EXACTLY one action. Example: move_to(x=10, y=64, z=20). No other text."},
                {"role": "user", "content": prompt},
            ])

        return response.strip()

    async def _get_memory_context(self, agent: "ValisAgent") -> str:
        """Get relevant memories via weighted retrieval for planning context."""
        query = f"Planning next actions. Goals: {', '.join(agent.goals[:3])}"
        try:
            memories = await agent.retrieval.retrieve(
                query, limit=5, embedding_fn=agent.llm.embed,
            )
        except Exception:
            memories = agent.memory.get_recent(n=5)
        if not memories:
            return "No memories yet."
        return "\n".join(
            f"- [{m.created.strftime('%H:%M')}] (imp={m.importance:.1f}) {m.content[:100]}"
            for m in memories
        )
