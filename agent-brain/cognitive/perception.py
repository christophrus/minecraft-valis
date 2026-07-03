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

        # Biome
        if p.biome:
            lines.append(f"The biome here is {p.biome}.")
        if p.nearby_biomes:
            hints = ", ".join(f"{d}: {b}" for d, b in p.nearby_biomes.items())
            lines.append(f"Biomes in the distance: {hints}.")

        # Nearby blocks — valuable/actionable blocks keep individual coordinates,
        # commodity blocks (dirt, grass, stone...) are grouped by type with a count
        # and the nearest coordinate. Cuts prompt tokens ~50% with no information
        # the LLM actually acts on lost.
        if p.nearby_blocks:
            wood_keywords = ("_LOG", "_WOOD", "_PLANKS")
            ore_keywords = ("COAL", "IRON", "GOLD", "DIAMOND", "COPPER", "EMERALD", "REDSTONE", "LAPIS")
            special = ("CRAFTING_TABLE", "FURNACE", "CHEST", "WHEAT", "FARMLAND",
                       "CARROTS", "POTATOES", "WATER", "LAVA")
            px = p.position.get("x", 0)
            py = p.position.get("y", 0)
            pz = p.position.get("z", 0)

            def _dist(b):
                return (abs(b.get("x", 0) - px) + abs(b.get("y", 0) - py)
                        + abs(b.get("z", 0) - pz))

            priority = []
            grouped: dict[str, list] = {}
            for b in p.nearby_blocks:
                btype = str(b.get("type", "")).upper()
                if (any(k in btype for k in wood_keywords)
                        or any(k in btype for k in ore_keywords)
                        or btype in special):
                    priority.append(b)
                else:
                    grouped.setdefault(btype, []).append(b)

            block_descs = []
            for b in sorted(priority, key=_dist)[:20]:
                block_descs.append(f"{b.get('type','?')}({b.get('x',0)},{b.get('y',0)},{b.get('z',0)})")
            for btype, bs in sorted(grouped.items(), key=lambda kv: -len(kv[1]))[:8]:
                nearest = min(bs, key=_dist)
                if len(bs) == 1:
                    block_descs.append(f"{btype}({nearest.get('x',0)},{nearest.get('y',0)},{nearest.get('z',0)})")
                else:
                    block_descs.append(
                        f"{btype} x{len(bs)} (nearest {nearest.get('x',0)},{nearest.get('y',0)},{nearest.get('z',0)})")
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

        # HAUL guidance — the value chain kept breaking at the last mile: raw ore
        # accumulated far from any furnace, surplus never reached the chest. Tell
        # the agent explicitly where its cargo should go so the LLM can act on it.
        raw_ore = sum(v for k, v in real_inv.items() if k.startswith("raw_"))
        near_furnace = (real_inv.get("furnace", 0) >= 1
            or any(b.get("type", "").upper() in ("FURNACE", "BLAST_FURNACE")
                   and abs(b.get("x", 0) - p.position.get("x", 0)) <= 4
                   and abs(b.get("z", 0) - p.position.get("z", 0)) <= 4
                   for b in p.nearby_blocks))
        if raw_ore >= 3 and not near_furnace:
            if p.village_furnace_pos:
                fp = p.village_furnace_pos
                lines.append(f"HAUL: you carry {raw_ore} raw ore — take it to the "
                             f"furnace at ({fp.get('x')},{fp.get('y')},{fp.get('z')}) "
                             f"(action_hint smelt) to turn it into ingots.")
            else:
                lines.append(f"HAUL: you carry {raw_ore} raw ore — smelt it at a "
                             f"furnace (build one from 8 cobblestone) to get ingots.")
        # Heavy load of depositable surplus → chest
        total_items = sum(real_inv.values())
        if total_items >= 40 and p.village_chest_pos:
            cp = p.village_chest_pos
            lines.append(f"HAUL: your inventory is heavy ({total_items} items) — "
                         f"deposit surplus at the village chest "
                         f"({cp.get('x')},{cp.get('y')},{cp.get('z')}).")

        # Craftable items (computed server-side from Bukkit recipes)
        if p.craftable:
            craft_strs = []
            for c in p.craftable[:10]:
                item = c.get("item", "?")
                amt = c.get("amount", 1)
                cost = c.get("cost", "?")
                craft_strs.append(f"{amt}x {item} (costs: {cost})")
            lines.append(f"CAN CRAFT NOW: {' | '.join(craft_strs)}.")

        if p.almost_craftable:
            almost_strs = []
            for c in p.almost_craftable[:5]:
                item = c.get("item", "?")
                missing = c.get("missing", "?")
                almost_strs.append(f"{item} (need: {missing})")
            lines.append(f"ALMOST CRAFTABLE (missing materials): {' | '.join(almost_strs)}.")

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
