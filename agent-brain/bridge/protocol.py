"""
Message protocol definitions for the WebSocket bridge between
the Minecraft plugin and the Python agent brain service.

All messages are JSON objects with a "type" field and relevant data.
"""

from dataclasses import dataclass, field
from typing import Any
import json


# --- Morph → Brain Messages (Morph sends to Brain) ---

@dataclass
class PerceptionData:
    """Perception report sent from the Minecraft plugin to the agent brain."""
    agent_name: str
    tick: int
    position: dict[str, float]  # {"x": float, "y": float, "z": float}
    time: int  # Minecraft time (0-24000)
    is_day: bool
    weather: str  # "clear", "rain", "thunder"
    nearby_blocks: list[dict[str, Any]]  # [{x, y, z, type, relative_x, relative_y, relative_z}, ...]
    nearby_entities: list[dict[str, Any]]  # [{type, name, x, y, z, distance, is_player}, ...]
    health: float = 20.0
    inventory: dict[str, int] = field(default_factory=dict)  # material_name -> count
    biome: str = "plains"
    nearby_biomes: dict[str, str] = field(default_factory=dict)  # {"north": "forest", ...}
    craftable: list[dict[str, Any]] = field(default_factory=list)  # [{item, amount, cost}, ...]
    almost_craftable: list[dict[str, Any]] = field(default_factory=list)  # [{item, amount, missing}, ...]
    nearby_chat: list[str] = field(default_factory=list)  # ["[BuilderAlice] I need wood", ...]
    village_chest: dict[str, int] = field(default_factory=dict)  # shared chest contents
    village_chest_distance: int = -1  # distance to village chest, -1 if no chest
    village_chest_pos: dict[str, int] = field(default_factory=dict)  # real chest {x,y,z}

    @classmethod
    def from_json(cls, data: dict) -> "PerceptionData":
        craft_data = data.get("craftable", {})
        return cls(
            agent_name=data.get("agent_name", ""),
            tick=data.get("tick", 0),
            position=data.get("position", {}),
            time=data.get("time", 0),
            is_day=data.get("is_day", True),
            weather=data.get("weather", "clear"),
            nearby_blocks=data.get("nearby_blocks", []),
            nearby_entities=data.get("nearby_entities", []),
            health=data.get("health", 20.0),
            inventory=data.get("inventory", {}),
            biome=data.get("biome", "plains"),
            nearby_biomes=data.get("nearby_biomes", {}),
            craftable=craft_data.get("can_craft", []) if isinstance(craft_data, dict) else [],
            almost_craftable=craft_data.get("almost", []) if isinstance(craft_data, dict) else [],
            nearby_chat=data.get("nearby_chat", []),
            village_chest=data.get("village_chest", {}),
            village_chest_distance=data.get("village_chest_distance", -1),
            village_chest_pos=data.get("village_chest_pos", {}),
        )


@dataclass
class ActionResult:
    """Result of an action executed in Minecraft."""
    agent_name: str
    action: str
    success: bool
    details: str

    @classmethod
    def from_json(cls, data: dict) -> "ActionResult":
        return cls(
            agent_name=data.get("agent_name", ""),
            action=data.get("action", ""),
            success=data.get("success", False),
            details=data.get("details", ""),
        )


# --- Brain → Morph Messages (Brain sends to Morph) ---

@dataclass
class AgentAction:
    """Action command sent from the agent brain to Minecraft."""
    agent_name: str
    action: str  # "move_to", "mine_block", "place_block", "look_at", "chat", "idle"
    params: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "type": "agent_action",
            "name": self.agent_name,
            "action": self.action,
            "params": self.params,
        }


@dataclass
class AgentChat:
    """Chat message from the agent brain to Minecraft."""
    agent_name: str
    text: str

    def to_json(self) -> dict:
        return {
            "type": "agent_chat",
            "name": self.agent_name,
            "text": self.text,
        }


@dataclass
class AgentState:
    """Cognitive state update sent to Minecraft for spectator HUD."""
    agent_name: str
    current_task: str
    reason: str
    action: str
    plan_summary: str

    def to_json(self) -> dict:
        return {
            "type": "agent_state",
            "name": self.agent_name,
            "current_task": self.current_task,
            "reason": self.reason,
            "action": self.action,
            "plan_summary": self.plan_summary,
        }


@dataclass
class SpawnRequest:
    """Request to spawn a new agent."""
    name: str
    personality: str = "default"
    x: float = 0
    y: float = 64
    z: float = 0

    def to_json(self) -> dict:
        return {
            "type": "agent_spawn",
            "name": self.name,
            "personality": self.personality,
            "x": self.x,
            "y": self.y,
            "z": self.z,
        }


@dataclass
class DespawnRequest:
    """Request to despawn an agent."""
    name: str

    def to_json(self) -> dict:
        return {
            "type": "agent_despawn",
            "name": self.name,
        }


def parse_message(raw: str) -> tuple[str, dict]:
    """Parse a raw JSON message and return (type, data_dict)."""
    data = json.loads(raw)
    return data.get("type", ""), data
