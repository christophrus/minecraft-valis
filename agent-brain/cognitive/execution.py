"""
Execution module — translates high-level action decisions into
concrete Minecraft action commands to be sent via WebSocket.

Parses action strings from planning into structured AgentAction messages.
"""

import logging
import re
from typing import TYPE_CHECKING

try:
    from ..bridge.protocol import AgentAction
except ImportError:
    from bridge.protocol import AgentAction

if TYPE_CHECKING:
    from ..agent import ValisAgent

logger = logging.getLogger("valis.cognitive.execution")


def _parse_num(s: str) -> int | float:
    """Parse a string as int or float, defaulting to 0."""
    try:
        return int(s) if "." not in s else float(s)
    except ValueError:
        return 0


class Executor:
    """
    Parses action strings from the planner and converts them into
    structured command messages to send to the Minecraft server.
    """

    def __init__(self):
        self.last_action: str = ""
        self.action_history: list[str] = []

    def parse_action(self, action_str: str) -> AgentAction | None:
        """
        Parse an action string like "move_to(x=10, y=64, z=20)"
        into a structured AgentAction.

        Supported formats:
        - move_to(x, y, z)
        - mine_block(x, y, z)
        - place_block(block_type, x, y, z)
        - look_at(x, y, z)
        - chat(message)
        - idle
        """
        action_str = action_str.strip()
        self.last_action = action_str
        self.action_history.append(action_str)
        if len(self.action_history) > 100:
            self.action_history = self.action_history[-100:]

        # Handle empty or invalid responses
        if not action_str:
            logger.debug("Empty action string, defaulting to idle")
            return AgentAction(agent_name="", action="idle")

        # Match function-style actions: action_name(key=value, key=value, ...)
        match = re.match(r"(\w+)\((.*)\)", action_str)
        if not match:
            # Try simple actions
            if action_str.lower() == "idle":
                return AgentAction(agent_name="", action="idle")
            logger.warning(f"Could not parse action: {action_str}")
            return None

        action_name = match.group(1).lower()
        params_str = match.group(2).strip()

        # Parse parameters
        params = {}
        if params_str:
            # Try name=value pairs
            for pair in re.finditer(r'(\w+)\s*=\s*([^,)]+)', params_str):
                key = pair.group(1)
                value = pair.group(2).strip().strip('"').strip("'")
                # Try to convert to int/float
                try:
                    if "." in value:
                        value = float(value)
                    else:
                        value = int(value)
                except ValueError:
                    pass  # Keep as string
                params[key] = value

            # Fallback: if no key=value pairs, try positional args
            if not params:
                parts = [p.strip().strip('"').strip("'") for p in params_str.split(",")]
                coord_actions = {"move_to", "moveto", "go_to", "goto", "walk_to",
                                 "mine_block", "mine", "break_block", "dig",
                                 "look_at", "look", "face"}
                if action_name in coord_actions and len(parts) >= 3:
                    params = {"x": _parse_num(parts[0]), "y": _parse_num(parts[1]), "z": _parse_num(parts[2])}
                elif action_name in ("place_block", "place", "build") and len(parts) >= 4:
                    params = {"block_type": parts[0], "x": _parse_num(parts[1]),
                              "y": _parse_num(parts[2]), "z": _parse_num(parts[3])}
                elif action_name in ("chat", "say", "speak", "talk"):
                    params = {"message": params_str}

        # Map action names to Minecraft actions
        action_map = {
            "move_to": "move_to",
            "moveto": "move_to",
            "go_to": "move_to",
            "goto": "move_to",
            "walk_to": "move_to",
            "mine_block": "mine_block",
            "mine": "mine_block",
            "break_block": "mine_block",
            "dig": "mine_block",
            "place_block": "place_block",
            "place": "place_block",
            "build": "place_block",
            "look_at": "look_at",
            "look": "look_at",
            "face": "look_at",
            "chat": "chat",
            "say": "chat",
            "speak": "chat",
            "talk": "chat",
            "idle": "idle",
            "wait": "idle",
            "do_nothing": "idle",
        }

        mapped_action = action_map.get(action_name)
        if not mapped_action:
            logger.warning(f"Unknown action: {action_name}")
            return None

        return AgentAction(agent_name="", action=mapped_action, params=params)

    def record_result(self, action: str, success: bool, details: str):
        """Record the result of an executed action for awareness tracking."""
        logger.debug(f"Action result: {action} -> {'OK' if success else 'FAIL'}: {details}")
