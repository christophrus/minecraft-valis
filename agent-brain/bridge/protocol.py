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

    @classmethod
    def from_json(cls, data: dict) -> "PerceptionData":
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
