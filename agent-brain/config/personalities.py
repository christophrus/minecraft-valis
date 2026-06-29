"""
Personality loader — reads agents.yaml and exposes named personalities.

Each personality has traits (e.g. "determined", "brave") and initial_goals
that bias the agent's behavior in the Cognitive Controller prompt.
"""

import logging
import os
from typing import TypedDict

logger = logging.getLogger("valis.personalities")

_YAML_PATH = os.path.join(os.path.dirname(__file__), "agents.yaml")
_CACHE: dict[str, dict] | None = None


class PersonalitySpec(TypedDict):
    name: str
    traits: list[str]
    initial_goals: list[str]
    focus: str  # one-line description of the role


_DEFAULT_FOCUS = {
    "explorer": "Map the world, find rare biomes and resources to share with the village.",
    "farmer": "Establish a food supply: farmland, crops, animal pens near the village.",
    "miner": "Dig for stone, coal, iron, diamond; supply the village with mineral resources.",
    "builder": "Construct durable structures, houses, walls, and community buildings.",
    "guard": "Defend the village: patrol, kill mobs, build walls and weapons.",
    "trader": "Collect valuables, negotiate item exchanges with other agents.",
    "artist": "Decorate the village with flowers, banners, ornamental builds.",
    "priest": "Build gathering places, share insights, foster community among agents.",
    "default": "Survive and contribute to whatever the village needs most right now.",
}


def _load_yaml() -> dict[str, dict]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        import yaml
        with open(_YAML_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"agents.yaml not found at {_YAML_PATH}, using empty personality config")
        data = {}
    except ImportError:
        logger.warning("pyyaml not installed, personalities unavailable")
        data = {}
    _CACHE = data
    return data


def get_personality(name: str) -> PersonalitySpec:
    """Look up a personality by name. Falls back to 'default' if unknown."""
    name_norm = (name or "default").lower().strip()
    data = _load_yaml()
    personalities = data.get("personalities", {})

    spec = personalities.get(name_norm)
    if spec is None:
        # Try default_agent block, otherwise return generic
        default_block = data.get("default_agent", {})
        traits = default_block.get("traits", ["curious", "helpful"])
        goals = default_block.get("initial_goals", [
            "Explore the surrounding area",
            "Gather basic resources",
            "Find or build shelter",
        ])
        return PersonalitySpec(
            name=name_norm,
            traits=traits,
            initial_goals=goals,
            focus=_DEFAULT_FOCUS.get(name_norm, _DEFAULT_FOCUS["default"]),
        )

    return PersonalitySpec(
        name=name_norm,
        traits=spec.get("traits", []),
        initial_goals=spec.get("initial_goals", []),
        focus=_DEFAULT_FOCUS.get(name_norm, _DEFAULT_FOCUS["default"]),
    )


def list_personalities() -> list[str]:
    """Return all known personality names from the YAML config."""
    data = _load_yaml()
    return list(data.get("personalities", {}).keys())
