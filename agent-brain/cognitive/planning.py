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
    Handles agent planning at two levels:
    1. Daily schedule (long-term)
    2. Moment-to-moment action selection (short-term)
    """

    def __init__(self):
        self.daily_plan: list[str] = []
        self.current_task: str = ""
        self.last_daily_plan_time: datetime.datetime | None = None

    async def plan_daily(self, agent: "ValisAgent") -> list[str]:
        """
        Generate a daily plan based on the agent's personality, memory, and goals.
        Called at the start of each Minecraft day.
        """
        # Build prompt context
        context = agent.perception_processor.build_context_text()
        personality = agent.personality
        goals = agent.goals
        memory_context = await self._get_memory_context(agent)

        prompt = f"""You are {agent.name}, a {personality} in a Minecraft world.

Your traits: {personality}

{context}

Recent memories:
{memory_context}

Your current goals:
{chr(10).join(f'- {g}' for g in goals)}

Generate a daily plan for today as a list of 3-6 tasks you want to accomplish.
Each task should be concrete and achievable with the blocks and resources you can see right now.
IMPORTANT: Use the "Biomes in the distance" information to decide WHERE to explore.
If you need wood and a forest is to the north, your first task should be "Travel north to find trees in the forest biome".
Include exact block coordinates from "Nearby blocks" when possible.
Format: one task per line, starting with a dash (-)."""

        response = await agent.llm.chat([
            {"role": "system", "content": "You are an AI agent in Minecraft. Output only the task list, no preamble."},
            {"role": "user", "content": prompt},
        ])

        # Parse tasks from response
        tasks = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if line.startswith("- "):
                tasks.append(line[2:])
            elif line.startswith("-"):
                tasks.append(line[1:])

        self.daily_plan = tasks if tasks else ["Explore the area", "Gather resources", "Find shelter"]
        self.last_daily_plan_time = datetime.datetime.now()
        self.current_task = self.daily_plan[0] if self.daily_plan else ""

        logger.info(f"Agent {agent.name} daily plan: {self.daily_plan}")
        return self.daily_plan

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

        prompt = f"""You are {agent.name} in Minecraft. You must choose ONE action to perform right now.

Current situation:
{context}

Your daily plan:
{chr(10).join(f'- {t}' for t in self.daily_plan)}

Current task: {self.current_task}

Inventory: {inv_text}

Relevant memories:
{memory_text}

{discrepancy_text}

Available actions:
- move_to(x, y, z): Walk to coordinates
- mine_block(x, y, z): Mine the block at given position — use exact coordinates from "Nearby blocks" above!
- place_block(block_type, x, y, z): Place a block
- craft(item): Craft using inventory. Known recipes: oak_planks (needs oak_log), spruce_planks (needs spruce_log), stick (needs oak_planks x2), crafting_table (needs oak_planks x4), wooden_pickaxe (needs oak_planks x3 + stick x2), stone_pickaxe (needs cobblestone x3 + stick x2), wooden_axe (needs oak_planks x3 + stick x2), wooden_sword (needs oak_planks x2 + stick x1)
- look_at(x, y, z): Look at a position
- chat(message): Send a chat message
- idle: Do nothing this tick

IMPORTANT: If you have wood logs in your inventory, craft planks FIRST (craft(oak_planks) or craft(spruce_planks)), then craft sticks, then tools.
If your task involves gathering resources, use mine_block with the exact coordinates shown under "Nearby blocks".
If the block you need is not nearby, use move_to to explore.

Respond with exactly ONE action in format: action_name(param1=value1, param2=value2, ...)"""

        response = await agent.llm.chat([
            {"role": "system", "content": "You control a Minecraft agent. Output ONLY the action, nothing else."},
            {"role": "user", "content": prompt},
        ])

        return response.strip()

    async def _get_memory_context(self, agent: "ValisAgent") -> str:
        """Get a summary of recent memories for planning context."""
        recent = agent.memory.get_recent(n=5)
        if not recent:
            return "No memories yet."
        return "\n".join(
            f"- [{m.created.strftime('%H:%M')}] ({m.node_type}) {m.content[:100]}"
            for m in recent
        )
