"""
Spawn roster loader — reads spawn_roster.yaml to determine which agents to
auto-spawn at startup.
"""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("valis.roster")

_YAML_PATH = os.path.join(os.path.dirname(__file__), "spawn_roster.yaml")


@dataclass
class RosterEntry:
    name: str
    personality: str
    offset_x: int = 0
    offset_y: int = 1
    offset_z: int = 0


def load_roster() -> list[RosterEntry]:
    """Load the spawn roster from YAML. Returns empty list if disabled or missing."""
    try:
        import yaml
        with open(_YAML_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.info(f"No spawn_roster.yaml at {_YAML_PATH} — skipping auto-spawn")
        return []
    except ImportError:
        logger.warning("pyyaml not installed — cannot load spawn_roster.yaml")
        return []
    except Exception as e:
        logger.warning(f"Failed to load spawn_roster.yaml: {e}")
        return []

    if not data.get("enabled", True):
        logger.info("Auto-spawn disabled in spawn_roster.yaml")
        return []

    entries = []
    for entry in data.get("agents", []):
        name = entry.get("name")
        personality = entry.get("personality", "default")
        if not name:
            continue
        entries.append(RosterEntry(
            name=name,
            personality=personality,
            offset_x=int(entry.get("offset_x", 0)),
            offset_y=int(entry.get("offset_y", 1)),
            offset_z=int(entry.get("offset_z", 0)),
        ))
    return entries
