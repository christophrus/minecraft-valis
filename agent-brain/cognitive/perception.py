"""
Perception module — processes raw world observation data from Minecraft
into structured context for the agent's cognitive loop.

Based on the "Perceive" module from Generative Agents (Park et al., 2023).
"""

import logging
from typing import Any

try:
    from ..bridge.protocol import PerceptionData
except ImportError:
    from bridge.protocol import PerceptionData

logger = logging.getLogger("valis.cognitive.perception")


class PerceptionProcessor:
    """
    Processes raw perception data from the Minecraft world and converts it
    into structured context that the agent can reason about.
    """

    def __init__(self):
        self.last_perception: PerceptionData | None = None
        self.perception_history: list[PerceptionData] = []

    def update(self, perception: PerceptionData):
        """Store and process a new perception snapshot."""
        self.last_perception = perception
        self.perception_history.append(perception)
        # Keep only last 100 perceptions
        if len(self.perception_history) > 100:
            self.perception_history = self.perception_history[-100:]

    def build_context_text(self) -> str:
        """Build a natural language description of the current world state."""
        if self.last_perception is None:
            return "No perception data available yet."

        p = self.last_perception
        lines = []

        # Time and weather
        time_str = "day" if p.is_day else "night"
        lines.append(f"It is {time_str} (Minecraft time {p.time}).")
        if p.weather != "clear":
            lines.append(f"The weather is {p.weather}.")

        # Position
        pos = p.position
        lines.append(
            f"I am at position ({pos.get('x', 0)}, {pos.get('y', 0)}, {pos.get('z', 0)})."
        )

        # Nearby blocks (with positions so agent knows WHERE to act)
        if p.nearby_blocks:
            block_descs = []
            for b in p.nearby_blocks[:10]:
                btype = b.get("type", "?")
                bx, by, bz = b.get("x", 0), b.get("y", 0), b.get("z", 0)
                block_descs.append(f"{btype}({bx},{by},{bz})")
            lines.append(f"Nearby blocks: {', '.join(block_descs)}.")

        # Nearby entities
        if p.nearby_entities:
            entity_descs = []
            for e in p.nearby_entities[:10]:
                dist = e.get("distance", 0)
                name = e.get("name", "unknown")
                etype = e.get("type", "unknown")
                entity_descs.append(f"{name} ({etype}) {dist:.1f}m away")
            lines.append(f"Nearby: {', '.join(entity_descs)}.")

        # Inventory (filter out AIR)
        real_inv = {k: v for k, v in p.inventory.items() if k.lower() != "air"}
        if real_inv:
            inv_items = [f"{mat}: {count}" for mat, count in real_inv.items()]
            lines.append(f"Inventory: {', '.join(inv_items)}.")
        else:
            lines.append("Inventory: empty.")

        return "\n".join(lines)

    def get_surroundings_summary(self) -> dict[str, Any]:
        """Get a structured summary of the current surroundings."""
        if self.last_perception is None:
            return {}

        p = self.last_perception
        return {
            "position": p.position,
            "time": p.time,
            "is_day": p.is_day,
            "weather": p.weather,
            "block_count": len(p.nearby_blocks),
            "entity_count": len(p.nearby_entities),
            "visible_agents": [
                e for e in p.nearby_entities
                if e.get("type") == "PLAYER" and not e.get("is_player")
            ],
        }
