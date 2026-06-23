"""
Goal Generation — creates and manages agent objectives.

Based on the PIANO architecture (Project Sid, Altera.AL 2024):
Agents generate goals from their experiences, personality, environmental
interactions, and social context. Goals drive planning and action selection.

Goal types:
- survival: food, shelter, safety
- social: make friends, spread ideas, help others
- economic: gather resources, craft items, trade
- creative: build structures, create art
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import ValisAgent

logger = logging.getLogger("valis.cognitive.goal_generation")


@dataclass
class Goal:
    """An agent goal/objective."""
    description: str
    goal_type: str  # "survival", "social", "economic", "creative"
    priority: float = 0.5  # 0-1
    completed: bool = False
    created_tick: int = 0


class GoalGenerator:
    """
    Generates and manages goals for the agent.
    Goals evolve based on experiences and interactions.
    """

    def __init__(self):
        self.goals: list[Goal] = []
        self.goal_history: list[Goal] = []

    def add_goal(self, description: str, goal_type: str = "survival",
                 priority: float = 0.5, tick: int = 0):
        """Add a new goal."""
        goal = Goal(
            description=description,
            goal_type=goal_type,
            priority=priority,
            created_tick=tick,
        )
        self.goals.append(goal)
        logger.debug(f"New goal: [{goal_type}] {description}")

    def complete_goal(self, description: str):
        """Mark a goal as completed."""
        for goal in self.goals:
            if goal.description == description and not goal.completed:
                goal.completed = True
                self.goal_history.append(goal)
                self.goals.remove(goal)
                return

    def get_active_goals(self) -> list[Goal]:
        """Get all uncompleted goals sorted by priority."""
        return sorted(
            [g for g in self.goals if not g.completed],
            key=lambda g: g.priority,
            reverse=True,
        )

    def get_top_goal(self) -> Goal | None:
        """Get the highest-priority active goal."""
        active = self.get_active_goals()
        return active[0] if active else None

    async def generate_goals(
        self,
        agent: "ValisAgent",
        perception_text: str,
        social_context: str,
    ):
        """Generate new goals based on the agent's current context."""
        # Avoid generating if we have enough active goals
        if len(self.get_active_goals()) >= 5:
            return

        prompt = f"""You are {agent.name} in a Minecraft world. Generate 1-2 new goals.

Current situation:
{perception_text}

Social context:
{social_context}

Existing goals:
{chr(10).join(f'- [{g.goal_type}] {g.description}' for g in self.goals)}

Based on the situation, generate 1-2 concrete goals. For each goal, specify:
- description: What to accomplish (one sentence)
- goal_type: survival, social, economic, or creative
- priority: 1-10 (10 = urgent)

Output a JSON array with 1-2 goal objects. Output ONLY the JSON."""

        try:
            response = await agent.llm.chat([
                {"role": "system", "content": "You generate goals for a Minecraft agent. Output only JSON."},
                {"role": "user", "content": prompt},
            ])

            import json
            json_str = response.strip()
            if json_str.startswith("```"):
                json_str = json_str.split("\n", 1)[1]
                if json_str.endswith("```"):
                    json_str = json_str[:-3]

            goals_data = json.loads(json_str)
            if isinstance(goals_data, dict):
                goals_data = [goals_data]

            for g in goals_data:
                self.add_goal(
                    description=g.get("description", "Explore the area"),
                    goal_type=g.get("goal_type", "survival"),
                    priority=float(g.get("priority", 5)) / 10.0,
                    tick=agent.tick_count if hasattr(agent, 'tick_count') else 0,
                )
        except Exception as e:
            logger.warning(f"Goal generation failed: {e}")

    def initialize_default_goals(self):
        """Set up initial goals for a new agent."""
        defaults = [
            Goal("Explore the surrounding area", "survival", 0.8),
            Goal("Gather basic resources (wood, stone)", "economic", 0.7),
            Goal("Find or build shelter", "survival", 0.6),
            Goal("Meet other agents in the area", "social", 0.4),
        ]
        self.goals = defaults
