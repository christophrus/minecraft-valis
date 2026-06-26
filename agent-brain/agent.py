"""
ValisAgent — the complete AI agent class.

Each ValisAgent combines:
- Memory Stream with retrieval (Generative Agents paper)
- Perception, Planning, Reflection, Execution (Generative Agents paper)
- Cognitive Controller, Action Awareness, Social Awareness, Goal Generation (Project Sid / PIANO)
- LLM provider for language reasoning

The agent runs a cognitive loop: perceive → retrieve → plan → reflect → execute
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field

try:
    from .bridge.protocol import (
        AgentAction, AgentChat, AgentState, PerceptionData, ActionResult,
    )
    from .llm.providers import LLMProvider, create_llm
    from .memory import MemoryStream, MemoryRetrieval
    from .cognitive import (
        PerceptionProcessor, Planner, Reflection, Executor,
        CognitiveController, ActionAwareness, SocialAwareness, GoalGenerator,
    )
except ImportError:
    from bridge.protocol import (
        AgentAction, AgentChat, AgentState, PerceptionData, ActionResult,
    )
    from llm.providers import LLMProvider, create_llm
    from memory import MemoryStream, MemoryRetrieval
    from cognitive import (
        PerceptionProcessor, Planner, Reflection, Executor,
        CognitiveController, ActionAwareness, SocialAwareness, GoalGenerator,
    )

logger = logging.getLogger("valis.agent")


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    name: str = "Agent"
    personality: str = "curious explorer"
    llm_provider: str = field(default_factory=lambda: os.environ.get("VALIS_DEFAULT_LLM", "ollama"))
    llm_model: str = field(default_factory=lambda: os.environ.get("VALIS_DEFAULT_MODEL", "mistral"))
    data_dir: str = field(default_factory=lambda: os.environ.get("VALIS_DATA_DIR", "data"))
    tick_rate: float = 2.0
    traits: list[str] = field(default_factory=list)
    initial_goals: list[str] = field(default_factory=list)


class ValisAgent:
    """
    A complete AI agent inhabiting the Minecraft world.

    Cognitive loop (per tick):
    1. Wait for new perception data
    2. Run Cognitive Controller (PIANO bottleneck)
    3. Retrieve relevant memories
    4. Generate/update goals
    5. Plan next action
    6. Reflect (if threshold exceeded)
    7. Execute action → send to Minecraft
    """

    def __init__(self, config: AgentConfig, bridge=None):
        self.config = config
        self.name = config.name
        self.personality = config.personality
        self.bridge = bridge
        self.tick_count = 0
        self.agent_id = uuid.uuid4().hex[:12]

        # LLM
        self.llm: LLMProvider = create_llm(
            provider=config.llm_provider,
            model=config.llm_model,
        )

        # Memory
        self.memory = MemoryStream(
            agent_name=config.name,
            data_dir=config.data_dir,
            embedding_fn=self.llm.embed,
            importance_fn=self._score_importance_llm,
        )
        self.retrieval = MemoryRetrieval(self.memory)

        # Cognitive modules
        self.perception_processor = PerceptionProcessor()
        self.planner = Planner()
        self.reflection = Reflection()
        self.executor = Executor()
        self.controller = CognitiveController()
        self.action_awareness = ActionAwareness()
        self.social_awareness = SocialAwareness(agent_name=config.name)
        self.goal_generator = GoalGenerator()

        # Active goals (delegated to goal_generator)
        self.goals: list[str] = config.initial_goals or [
            "Explore the surrounding area",
            "Gather basic resources",
            "Find or build shelter",
        ]

        # State
        self._pending_perception: PerceptionData | None = None
        self._perception_event = asyncio.Event()
        self._running = False

        # Action state
        self._crafting_table_placed = False  # Prevent re-placing after successful placement
        self._crafting_table_pos: tuple[int, int, int] | None = None  # Where we placed it
        self._recently_crafted: dict[str, float] = {}  # Track recent crafts to avoid duplicate due to inv lag
        self._failed_actions: dict[str, int] = {}  # Track repeated failures to avoid retrying (e.g. "place:stick")
        self._craft_idle_streak: int = 0  # Count consecutive craft→idle cycles to break deadlocks
        self._build_queue: list[AgentAction] = []  # Queued place_block actions for multi-block building

        # Performance: controller cache + APM tracking
        self._cached_decision: object | None = None
        self._cached_decision_tick: int = 0
        self._last_perception_hash: int | None = None
        self._action_result_ready: bool = False
        self._apm_actions: int = 0
        self._apm_start: float | None = None

        logger.info(f"Agent created: {self.name} ({self.personality}) [{self.agent_id}]")

    # --- Public API ---

    async def start(self):
        """Start the agent's cognitive loop."""
        self._running = True
        self.goal_generator.initialize_default_goals()
        logger.info(f"Agent {self.name} started cognitive loop.")

    async def stop(self):
        """Stop the agent's cognitive loop."""
        self._running = False
        self._perception_event.set()  # Unblock any waiting
        logger.info(f"Agent {self.name} stopped.")

    async def _score_importance_llm(self, content: str) -> float:
        """Score memory importance (poignancy) on a 0-1 scale via LLM.

        Based on the Generative Agents paper: the LLM rates each memory on a
        1-10 poignancy scale. We normalize to 0-1.
        """
        prompt = (
            "On a scale of 1 (mundane) to 10 (critical), rate the poignancy of "
            "this Minecraft agent memory. Use the FULL range:\n"
            "  1-2: Routine, repetitive (e.g. walked forward, looked around)\n"
            "  3-4: Minor observations (e.g. noticed a tree, picked up item)\n"
            "  5-6: Useful learning (e.g. found a resource, crafted a tool)\n"
            "  7-8: Important insight (e.g. discovered a strategy, survived danger)\n"
            "  9-10: Critical realization (e.g. fundamental strategy change, near-death lesson)\n\n"
            "Respond with ONLY the number.\n\n"
            f"Memory: \"{content[:300]}\"\n\nRating:"
        )
        try:
            response = await self.llm.chat([
                {"role": "system", "content": "Rate memory importance 1-10. Use the full range — most routine memories should be 1-4. Output only a number."},
                {"role": "user", "content": prompt},
            ])
            import re
            match = re.search(r'(\d+)', response.strip())
            if match:
                raw = int(match.group(1))
                return max(0.1, min(1.0, raw / 10.0))
        except Exception as e:
            logger.warning(f"Importance scoring LLM failed: {e}")
        return 0.5

    def _try_fast_craft(self, perception: PerceptionData) -> AgentAction | None:
        """Pre-emptive tech-tree progression — craft planks/table/tools and place the
        crafting table WITHOUT an LLM call. Runs every tick regardless of controller hint.
        Returns a craft/place_block action, or None if there is nothing to do.

        This is what keeps the agent progressing: it had logs+sticks+table in inventory
        for 200 ticks but never crafted tools because the LLM was asked instead of this.
        """
        import time
        pos = perception.position
        px, py, pz = int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("z", 0))
        blocks = perception.nearby_blocks
        inv = perception.inventory

        if not hasattr(self, '_recently_crafted'):
            self._recently_crafted: dict[str, float] = {}
        now = time.time()
        self._recently_crafted = {k: v for k, v in self._recently_crafted.items() if now - v < 5}

        all_logs = ("oak_log","birch_log","spruce_log","jungle_log","acacia_log",
                    "dark_oak_log","cherry_log","mangrove_log")
        all_planks = ("oak_planks","birch_planks","spruce_planks","jungle_planks",
                      "acacia_planks","dark_oak_planks","cherry_planks","mangrove_planks")
        total_logs = sum(inv.get(lt, 0) for lt in all_logs)
        total_planks = sum(inv.get(pt, 0) for pt in all_planks)
        total_sticks = inv.get("stick", 0)
        has_pickaxe = any("pickaxe" in k.lower() for k in inv)

        def _find_best(key_list):
            best, best_count = None, 0
            for k in key_list:
                c = inv.get(k, 0)
                if c > best_count:
                    best, best_count = k, c
            return best

        # Tools (pickaxe/axe) need a crafting table nearby — place it FIRST if we have one
        # but none is nearby, so the subsequent craft doesn't fail with "need crafting_table".
        has_crafting_table_inv = inv.get("crafting_table", 0) >= 1
        has_crafting_table_nearby = (
            self._crafting_table_placed or
            any(b.get("type", "").upper() == "CRAFTING_TABLE" and abs(b.get("x",0)-px) <= 3 and abs(b.get("z",0)-pz) <= 3
                for b in blocks)
        )
        _REPLACEABLE = frozenset({"AIR","CAVE_AIR","VOID_AIR","SHORT_GRASS","TALL_GRASS",
                                    "FERN","LARGE_FERN","DEAD_BUSH","SNOW","VINE","LEAF_LITTER"})
        # Will we want to craft a table-requiring tool this tick? (pickaxe/axe/sword)
        wants_tool = (total_sticks >= 2 and total_planks >= 3
                      and (not has_pickaxe or not any("axe" in k.lower() for k in inv)))
        if has_crafting_table_inv and not has_crafting_table_nearby and wants_tool:
            tx, ty, tz = px, py + 1, pz
            blocked = any(b.get("x",0)==tx and b.get("y",0)==ty and b.get("z",0)==tz
                          and b.get("type","").upper() not in _REPLACEABLE for b in blocks)
            if not blocked:
                logger.debug(f"FAST-CRAFT: placing crafting_table at ({tx},{ty},{tz}) before crafting tool")
                self._crafting_table_placed = True
                self._crafting_table_pos = (tx, ty, tz)
                return AgentAction(agent_name="", action="place_block",
                                   params={"block_type": "crafting_table", "x": tx, "y": ty, "z": tz})

        craft_action = None
        if total_logs >= 1 and total_planks < 4:
            best_log = _find_best(all_logs)
            plank_type = best_log.replace("_log", "_planks") if best_log else "oak_planks"
            if plank_type not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": plank_type})
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT planks ({best_log}={inv.get(best_log,0)})")
        # Crafting table: when we have 4+ planks, no table in inventory
        elif total_planks >= 4 and inv.get("crafting_table", 0) < 1:
            if self._crafting_table_placed and self._crafting_table_pos:
                ctx, cty, ctz = self._crafting_table_pos
                table_dist = abs(px - ctx) + abs(py - cty) + abs(pz - ctz)
                if table_dist > 16:
                    self._crafting_table_placed = False
                    self._crafting_table_pos = None
            if "crafting_table" not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": "crafting_table"})
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT crafting_table (planks={total_planks})")
        # Pickaxe: 3 planks + 2 sticks (needs table — placed above)
        elif total_sticks >= 2 and total_planks >= 3 and not has_pickaxe and has_crafting_table_nearby:
            pickaxe_type = "stone_pickaxe" if inv.get("cobblestone", 0) >= 3 else "wooden_pickaxe"
            if pickaxe_type not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": pickaxe_type})
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT {pickaxe_type} (sticks={total_sticks}, planks={total_planks})")
        # Axe: 3 planks + 2 sticks (needs table)
        elif total_sticks >= 2 and total_planks >= 3 and not any("axe" in k.lower() for k in inv) and has_crafting_table_nearby:
            if "wooden_axe" not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": "wooden_axe"})
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT wooden_axe (sticks={total_sticks}, planks={total_planks})")
        # Sticks: from surplus planks (>= 5 so >=3 remain for a tool)
        elif total_sticks < 4 and total_planks >= 5:
            if "stick" not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": "stick"})
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT sticks (planks={total_planks}, sticks={total_sticks})")

        if craft_action:
            item = craft_action.params.get("item", "")
            fail_key = f"craft:{item}"
            if fail_key in self._failed_actions and self._failed_actions[fail_key] >= 3:
                logger.debug(f"FAST-CRAFT: skipping {item} — blacklisted ({self._failed_actions[fail_key]} failures)")
                return None
            if item:
                self._recently_crafted[item] = time.time()
                self._craft_idle_streak = 0
                return craft_action
        return None

    def _decision_to_action(self, decision, perception: PerceptionData) -> AgentAction | None:
        """Convert controller decision to an action.
        Priority: 1) LLM intent coordinates, 2) Perception heuristics, 3) None (LLM fallback)."""
        import random, re, time
        
        hint = decision.action_hint.lower()
        intent = decision.intent  # LLM's detailed instruction with coordinates
        pos = perception.position
        px, py, pz = int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("z", 0))
        blocks = perception.nearby_blocks

        # --- Extract coordinates from LLM intent (e.g. "Mine oak_log at (125,64,-30)") ---
        intent_coords = re.findall(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', intent)
        intent_target = None
        if intent_coords:
            ix, iy, iz = int(intent_coords[0][0]), int(intent_coords[0][1]), int(intent_coords[0][2])
            # Find matching nearby block
            for b in blocks:
                if b.get("x",0)==ix and b.get("y",0)==iy and b.get("z",0)==iz:
                    intent_target = b
                    break
            if not intent_target:
                # Block not in perception — only use as nav target, not mine target
                intent_target = {"x": ix, "y": iy, "z": iz, "type": "UNKNOWN", "_nav_only": True}

        # --- Recently mined / placed / failed-place / crafted tracking ---
        if not hasattr(self, '_recently_mined'):
            self._recently_mined: dict[str, float] = {}
        if not hasattr(self, '_recently_placed'):
            self._recently_placed: dict[str, float] = {}
        if not hasattr(self, '_recently_failed_place'):
            self._recently_failed_place: dict[str, float] = {}
        if not hasattr(self, '_recently_crafted'):
            self._recently_crafted: dict[str, float] = {}
        now = time.time()
        self._recently_mined = {k: v for k, v in self._recently_mined.items() if now - v < 5}
        self._recently_placed = {k: v for k, v in self._recently_placed.items() if now - v < 120}
        self._recently_failed_place = {k: v for k, v in self._recently_failed_place.items() if now - v < 10}
        # Shorter cooldown (5s instead of 15s) — prevents deadlocks where craft fails
        # and the agent idles for 15 seconds before retrying
        self._recently_crafted = {k: v for k, v in self._recently_crafted.items() if now - v < 5}
        def pos_key(b): return f"{b.get('x',0)},{b.get('y',0)},{b.get('z',0)}"

        # --- PRE-EMPTIVE CRAFTING + TABLE PLACEMENT (extracted, runs every tick) ---
        fast_craft = self._try_fast_craft(perception)
        if fast_craft:
            return fast_craft

        # --- NON-PLACEABLE ITEMS: never try to place these ---
        NON_PLACEABLE = frozenset({
            "stick", "wheat_seeds", "string", "flint", "feather", "bone",
            "arrow", "coal", "charcoal", "iron_ingot", "gold_ingot", "diamond",
            "emerald", "lapis_lazuli", "redstone", "bowl", "paper", "book",
            "compass", "clock", "fishing_rod", "shears", "lead", "name_tag",
            "saddle", "leather", "raw_iron", "raw_gold", "raw_copper",
            "wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "wooden_axe",
            "stone_axe", "iron_axe", "wooden_sword", "stone_sword", "iron_sword",
            "wooden_shovel", "stone_shovel", "iron_shovel", "wooden_hoe",
        })

        if hint in ("mine", "place"):
            target = None
            target_priority = 0  # 1=intent, 2=wood, 3=plan, 4=solid
            
            # Priority 1: LLM intent target (if valid and not recently mined)
            if intent_target and pos_key(intent_target) not in self._recently_mined:
                target = intent_target
                target_priority = 1
                logger.debug(f"FAST-PATH: TARGET=intent -> {target.get('type','?')} at ({target.get('x')},{target.get('y')},{target.get('z')})")
            
            # Priority 2: Wood/log blocks nearby (only for mine, not place!)
            if target is None and hint == "mine":
                wood_blocks = [b for b in blocks if b.get("type", "").upper() in 
                              ("OAK_LOG", "BIRCH_LOG", "SPRUCE_LOG", "JUNGLE_LOG", "ACACIA_LOG", 
                               "DARK_OAK_LOG", "CHERRY_LOG", "MANGROVE_LOG", "OAK_WOOD", "BIRCH_WOOD")]
                wood_blocks = [b for b in wood_blocks if pos_key(b) not in self._recently_mined]
                if wood_blocks:
                    target = min(wood_blocks, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
                    target_priority = 2
                    logger.debug(f"FAST-PATH: TARGET=wood_heuristic -> {target.get('type','?')} at ({target.get('x')},{target.get('y')},{target.get('z')})")
            
            # Priority 3: Plan coordinates
            if target is None:
                plan_text = " ".join(self.planner.daily_plan)
                plan_coords = re.findall(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', plan_text)
                if plan_coords:
                    for pc in plan_coords:
                        for b in blocks:
                            if b.get("x",0)==int(pc[0]) and b.get("y",0)==int(pc[1]) and b.get("z",0)==int(pc[2]):
                                target = b
                                target_priority = 3
                                break
                        if target: break
            
            # Priority 4: Nearest solid block (fallback)
            if target is None:
                solid_blocks = [b for b in blocks if b.get("type", "AIR") not in ("AIR", "CAVE_AIR", "VOID_AIR", "WATER", "LAVA")]
                solid_blocks = [b for b in solid_blocks if pos_key(b) not in self._recently_mined]
                if solid_blocks:
                    target = min(solid_blocks, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
                    target_priority = 4
                else:
                    # Debug: what types ARE in perception?
                    sample_types = list(set(b.get("type","?") for b in blocks[:20]))
                    logger.debug(f"FAST-PATH: no solid_blocks! perception sample types: {sample_types} total={len(blocks)}")
            
            if target:
                target_type = target.get("type", "").upper()
                # For place: use agent's foot Y as base; for mine: use target's Y
                if hint == "place":
                    tx, ty, tz = target.get("x", px), py, target.get("z", pz)
                else:
                    tx, ty, tz = target.get("x", px), target.get("y", py - 1), target.get("z", pz)
                
                # If hint is "mine" but target is junk and we need wood → explore instead
                # BUT: always respect LLM intent (priority 1), plan targets (priority 3),
                #      and survival mode (nighttime = mine whatever is available)
                has_wood = any(k in ("oak_log","birch_log","spruce_log","acacia_log","dark_oak_log","cherry_log") 
                              for k in perception.inventory)
                junk_types = ("DIRT","GRASS_BLOCK","STONE","COBBLESTONE","SAND","GRAVEL","SHORT_GRASS",
                              "ANDESITE","DIORITE","GRANITE","TUFF","DEEPSLATE")
                is_night = not perception.is_day
                skip_junk_filter = (target_priority <= 2  # intent or wood heuristic
                                   or target_priority == 3  # plan targets are LLM-authored
                                   or is_night)  # nighttime = survival mode, mine anything
                if hint == "mine" and not skip_junk_filter and not has_wood and target_type in junk_types:
                    logger.debug(f"FAST-PATH: skipping junk target (priority={target_priority}, type={target_type}), fall to explore")
                    pass  # Fall through to move/explore
                elif hint == "mine":
                    # PRIORITY: Check for ANY nearby minable block first (≤4 blocks)
                    # before navigating to far-away intent targets.
                    minable_nearby = [b for b in blocks
                                    if b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR","BEDROCK","WATER","LAVA")
                                    and "_LEAVES" not in b.get("type","").upper()
                                    and pos_key(b) not in self._recently_mined
                                    and abs(b.get("x",0)-px) <= 4 and abs(b.get("y",0)-py) <= 4
                                    and abs(b.get("z",0)-pz) <= 4]
                    if minable_nearby:
                        t = min(minable_nearby, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
                        tkey = pos_key(t)
                        self._recently_mined[tkey] = now
                        logger.debug(f"FAST-PATH: MINE=nearby {t.get('type','?')} at ({t.get('x')},{t.get('y')},{t.get('z')})")
                        return AgentAction(agent_name="", action="mine_block",
                            params={"x": int(t.get("x",px)), "y": int(t.get("y",py)), "z": int(t.get("z",pz))})

                    # No nearby block — proceed with far-target logic
                    if is_night and target_type in junk_types and not has_wood:
                        logger.debug(f"FAST-PATH: night override — mining {target_type} for survival")
                    elif target_priority == 3 and target_type in junk_types and not has_wood:
                        logger.debug(f"FAST-PATH: plan override — mining {target_type} from LLM plan")
                    tkey = f"{int(tx)},{int(ty)},{int(tz)}"
                    if tkey in self._recently_placed:
                        logger.debug(f"FAST-PATH: skip mine of recently placed block at ({int(tx)},{int(ty)},{int(tz)})")
                        pass
                    else:
                        import math
                        dist_to_target = math.sqrt((px-tx)**2 + (py-ty)**2 + (pz-tz)**2)
                        if dist_to_target > 4:
                            # Track repeated attempts to reach the same far mine target
                            far_key = f"{int(tx)},{int(ty)},{int(tz)}"
                            if not hasattr(self, '_far_target_attempts'):
                                self._far_target_attempts: dict = {}
                            self._far_target_attempts[far_key] = self._far_target_attempts.get(far_key, 0) + 1
                            attempts = self._far_target_attempts[far_key]
                            if attempts >= 3:
                                logger.debug(f"FAST-PATH: far target retried {attempts}x — picking nearest wood instead")
                                self._far_target_attempts = {}
                                # Don't pass — force fallback to nearest solid/wood
                            else:
                                logger.debug(f"FAST-PATH: mine target too far ({dist_to_target:.0f}m), navigate (attempt {attempts}/3)")
                            pass  # Fall through to move/explore
                        else:
                            # Don't mine UNKNOWN/nav-only targets — they're not verified in perception
                            if target.get("_nav_only") or target_type in ("UNKNOWN", "AIR", "CAVE_AIR", "VOID_AIR"):
                                logger.debug(f"FAST-PATH: skip mine of unverified/air block {target_type} at ({int(tx)},{int(ty)},{int(tz)})")
                            else:
                                self._recently_mined[tkey] = now
                                return AgentAction(agent_name="", action="mine_block", params={"x": int(tx), "y": int(ty), "z": int(tz)})
                else:
                    inv = perception.inventory
                    place_mat = "dirt"
                    # Extract block type from intent (e.g. "place crafting_table" → "crafting_table")
                    intent_block = None
                    for word in intent.lower().replace(",", " ").split():
                        if word in inv and word.lower() not in NON_PLACEABLE:
                            intent_block = word
                            break
                    if intent_block and inv.get(intent_block, 0) >= 1:
                        place_mat = intent_block
                    elif inv:
                        placeable = {k:v for k,v in inv.items()
                            if k.lower() not in NON_PLACEABLE
                            and k.lower() not in ("air", "cornflower",
                            "oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log",
                            "dark_oak_log", "cherry_log", "mangrove_log",
                            "oak_wood", "spruce_wood", "birch_wood")
                            and f"place:{k.lower()}" not in self._failed_actions}
                        if placeable:
                            place_mat = max(placeable, key=placeable.get)
                    # Find free air above target, skip recently placed/failed Y levels
                    above_y = None
                    for dy in range(1, 6):
                        test_y = int(ty) + dy
                        test_key = f"{int(tx)},{test_y},{int(tz)}"
                        if test_key in self._recently_failed_place or test_key in self._recently_placed:
                            continue
                        # Check if position is within perception range at all
                        in_range = any(b.get("x",0)==int(tx) and b.get("z",0)==int(tz) for b in blocks)
                        blocked = any(b.get("x",0)==int(tx) and b.get("y",0)==test_y and b.get("z",0)==int(tz)
                                      and b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR") for b in blocks)
                        if not blocked and in_range:
                            above_y = test_y
                            break
                    if above_y is None:
                        # Try adjacent columns (N/E/S/W) if target column is blocked
                        for adx, adz in [(1,0),(-1,0),(0,1),(0,-1)]:
                            adj_x, adj_z = int(tx) + adx, int(tz) + adz
                            for dy in range(0, 4):
                                test_y = int(ty) + dy
                                test_key = f"{adj_x},{test_y},{adj_z}"
                                if test_key in self._recently_failed_place or test_key in self._recently_placed:
                                    continue
                                blocked_adj = any(b.get("x",0)==adj_x and b.get("y",0)==test_y and b.get("z",0)==adj_z
                                                  and b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR") for b in blocks)
                                if not blocked_adj:
                                    above_y = test_y
                                    tx, tz = float(adj_x), float(adj_z)
                                    logger.debug(f"FAST-PATH: place redirected to adjacent ({adj_x},{test_y},{adj_z})")
                                    break
                            if above_y is not None:
                                break
                    if above_y is None:
                        self._recently_failed_place[f"{int(tx)},{int(ty)},{int(tz)}"] = now
                        logger.debug(f"FAST-PATH: place all Y blocked at ({int(tx)},{int(tz)}), falling through")
                        pass  # Fall through to explore
                    elif place_mat == "dirt" and inv.get("oak_log", 0) >= 1:
                        return None  # Let LLM decide to craft
                    else:
                        pkey = f"{int(tx)},{above_y},{int(tz)}"
                        self._recently_placed[pkey] = now
                        return AgentAction(agent_name="", action="place_block",
                                           params={"block_type": place_mat, "x": int(tx), "y": above_y, "z": int(tz)})
            # No target found, fall through to move
            logger.debug(f"FAST-PATH: no target in mine/place, falling to explore")

        # --- SHELTER BUILDING: multi-block placement for basic shelter ---
        plan_text = " ".join(self.planner.daily_plan).lower()
        want_shelter = any(w in plan_text for w in ("shelter", "house", "hut", "build a"))
        if hint == "place" and want_shelter and not target:
            # Simple 4-block ring pattern at agent's feet+1 (N/E/S/W)
            shelter_offsets = [(0, 1, -1), (1, 1, 0), (0, 1, 1), (-1, 1, 0)]
            if not hasattr(self, '_shelter_step'):
                self._shelter_step = 0
            if not hasattr(self, '_shelter_origin'):
                self._shelter_origin = (px, py, pz)
            # If agent moved far, reset shelter
            sox, soy, soz = self._shelter_origin
            if abs(px - sox) > 3 or abs(pz - soz) > 3:
                self._shelter_step = 0
                self._shelter_origin = (px, py, pz)
            # Get material to build with
            build_mat = "dirt"
            if inv.get("cobblestone", 0) >= 4:
                build_mat = "cobblestone"
            elif inv.get("oak_planks", 0) >= 4:
                build_mat = "oak_planks"
            inv_build = inv.get(build_mat, 0)
            if inv_build >= 1 and self._shelter_step < 4:
                dx, dy, dz = shelter_offsets[self._shelter_step]
                tx, ty, tz = px + dx, py + dy, pz + dz
                # Check not blocked
                blocked = any(b.get("x",0)==tx and b.get("y",0)==ty and b.get("z",0)==tz
                              and b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR") for b in blocks)
                if not blocked:
                    pkey = f"{tx},{ty},{tz}"
                    self._recently_placed[pkey] = now
                    self._shelter_step += 1
                    logger.debug(f"FAST-PATH: SHELTER step {self._shelter_step}/4 placing {build_mat} at ({tx},{ty},{tz})")
                    return AgentAction(agent_name="", action="place_block",
                                       params={"block_type": build_mat, "x": tx, "y": ty, "z": tz})
                else:
                    self._shelter_step += 1  # Skip blocked position
            elif self._shelter_step >= 4:
                logger.debug(f"FAST-PATH: SHELTER complete (4 blocks placed)")
                self._shelter_step = 0  # Reset for next build

        # --- HUNT: attack nearby animals for food ---
        if hint in ("hunt", "attack") or ("hunt" in intent.lower() or any(
            w in intent.lower() for w in ("sheep", "cow", "pig", "chicken", "rabbit", "food", "meat", "hunt"))):
            entities = perception.nearby_entities
            # Find nearest huntable animal
            huntable = [e for e in entities if e.get("type","") in 
                       ("SHEEP","COW","PIG","CHICKEN","RABBIT")]
            if huntable:
                target = min(huntable, key=lambda e: e.get("distance", 999))
                logger.debug(f"FAST-PATH: HUNT -> {target.get('type')} at {target.get('distance',0):.1f}m")
                return AgentAction(agent_name="", action="attack_mob",
                                   params={"type": target.get("type","").lower()})
            else:
                logger.debug(f"FAST-PATH: HUNT -> no animals nearby")
        # Collect dropped items after hunting
        if hint == "collect" or "collect" in intent.lower():
            logger.debug(f"FAST-PATH: collecting nearby items")
            return AgentAction(agent_name="", action="collect_items")

        if hint == "craft":
            # Pre-emptive craft already ran above — if nothing was craftable, fall through to LLM-PATH.
            # Don't idle — let the LLM decide a productive alternative action.
            logger.debug(f"FAST-PATH: craft hint but nothing to craft, deferring to LLM-PATH")
            return None  # signals caller to use LLM-PATH instead of idling

        if hint in ("move", "explore", "mine", "place"):
            import math
            # --- Stuck detection ---
            # Track consecutive ticks at same position with active navigation
            current_pos_key = f"{px},{py},{pz}"
            if not hasattr(self, '_stuck_positions'):
                self._stuck_positions: list[str] = []
            self._stuck_positions.append(current_pos_key)
            if len(self._stuck_positions) > 8:
                self._stuck_positions = self._stuck_positions[-8:]
            # Check if stuck: same position for last 5+ ticks with active nav target
            if (hasattr(self, '_nav_target') and self._nav_target
                and len(self._stuck_positions) >= 5
                and len(set(self._stuck_positions[-5:])) == 1):
                logger.warning(f"FAST-PATH: STUCK at ({px},{py},{pz}) for 5 ticks, resetting nav+explore")
                # Before jumping, try to mine our way out — dig blocks around us
                if not hasattr(self, '_stuck_mine_attempts'):
                    self._stuck_mine_attempts = 0
                self._stuck_mine_attempts += 1
                # Mine blocks at foot level (py-1) in N/E/S/W cycle + blocks at head level (py, py+1)
                dig_dirs = [(1,0), (0,1), (-1,0), (0,-1)]
                dig_idx = (self._stuck_mine_attempts - 1) % 4
                dx, dz = dig_dirs[dig_idx]
                # Try foot level first, then head level
                for dy in (-1, 0, 1):
                    for b in blocks:
                        if b.get("x",0)==px+dx and b.get("y",0)==py+dy and b.get("z",0)==pz+dz:
                            btype = b.get("type","").upper()
                            if btype not in ("AIR","CAVE_AIR","VOID_AIR","BEDROCK","WATER","LAVA") \
                               and "_LEAVES" not in btype and "_LOG" not in btype:
                                tkey = f"{px+dx},{py+dy},{pz+dz}"
                                if tkey not in self._recently_mined:
                                    logger.debug(f"FAST-PATH: STUCK-DIG mining {btype} at ({px+dx},{py+dy},{pz+dz}) to escape")
                                    self._recently_mined[tkey] = now
                                    return AgentAction(agent_name="", action="mine_block",
                                                       params={"x": px+dx, "y": py+dy, "z": pz+dz})
                # If 4 dig attempts found nothing useful, teleport to safe ground immediately
                if self._stuck_mine_attempts >= 4:
                    self._stuck_mine_attempts = 0
                    import random as _rnd2
                    dx2, dz2 = _rnd2.choice([(1,0),(-1,0),(0,1),(0,-1)])
                    offset2 = _rnd2.randint(10, 20)
                    tp_x, tp_z = px + dx2 * offset2, pz + dz2 * offset2
                    safe_y = 70
                    logger.warning(f"FAST-PATH: STUCK-ESCAPE teleporting from ({px},{py},{pz}) to ({tp_x},{safe_y},{tp_z})")
                    self._nav_target = None
                    self._stuck_positions = []
                    return AgentAction(agent_name="", action="teleport",
                                       params={"x": tp_x, "y": safe_y, "z": tp_z})
                self._nav_target = None
                self._explore_heading = None
                self._explore_steps = 0
                self._stuck_positions = []
                # Track stuck positions to avoid bouncing back to the same area
                if not hasattr(self, '_stuck_position_history'):
                    self._stuck_position_history: set[str] = set()
                if len(self._stuck_position_history) > 30:
                    self._stuck_position_history.clear()
                self._stuck_position_history.add(current_pos_key)
                # Try anti-stuck jump: pick direction furthest from known stuck spots
                import random as _random
                best_target, best_min_dist = None, 0
                for _ in range(5):
                    dx, dz = _random.choice([(1,0), (-1,0), (0,1), (0,-1)])
                    dist = _random.randint(25, 45)
                    tx, ty, tz = px + dx * dist, py + 3, pz + dz * dist
                    # Check distance to nearest known stuck position
                    min_dist = float('inf')
                    for sp in self._stuck_position_history:
                        sx, sy, sz = map(int, sp.split(","))
                        d = ((tx-sx)**2 + (ty-sy)**2 + (tz-sz)**2) ** 0.5
                        if d < min_dist: min_dist = d
                    if min_dist > best_min_dist:
                        best_min_dist = min_dist
                        best_target = (tx, ty, tz)
                if best_target is None:
                    dx, dz = _random.choice([(1,0), (-1,0), (0,1), (0,-1)])
                    best_target = (px + dx * 35, py + 3, pz + dz * 35)
                tx, ty, tz = best_target
                self._nav_target = (tx, ty, tz)
                self._nav_start = time.time()
                logger.debug(f"FAST-PATH: anti-stuck jump to ({tx},{ty},{tz}) (avoiding {len(self._stuck_position_history)} known spots)")
                return AgentAction(agent_name="", action="move_to",
                                   params={"x": tx, "y": ty, "z": tz})
            # Don't interrupt ongoing navigation — idle directly (skip LLM fallback)
            if hasattr(self, '_nav_target') and self._nav_target:
                tx, ty, tz = self._nav_target
                dist = math.sqrt((px - tx)**2 + (py - ty)**2 + (pz - tz)**2)
                elapsed = time.time() - getattr(self, '_nav_start', 0)
                if dist > 3 and elapsed < 8:
                    logger.debug(f"FAST-PATH: waiting for nav, dist={dist:.1f} elapsed={elapsed:.1f}s target=({tx},{ty},{tz}) pos=({px},{py},{pz})")
                    return AgentAction(agent_name="", action="idle")
                self._nav_target = None
            
            # Priority 1 (MINE): Mine nearby blocks first — don't navigate to far-away intent coords
            if hint == "mine":
                # Find the closest minable block within 4 blocks
                minable = [b for b in blocks
                          if b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR","BEDROCK","WATER","LAVA","CRAFTING_TABLE")
                          and "_LEAVES" not in b.get("type","").upper()
                          and pos_key(b) not in self._recently_mined]
                close_minable = [b for b in minable
                                if abs(b.get("x",0)-px) <= 4 and abs(b.get("y",0)-py) <= 4
                                and abs(b.get("z",0)-pz) <= 4]
                if close_minable:
                    t = min(close_minable, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
                    logger.debug(f"FAST-PATH: MINE=nearby {t.get('type','?')} at ({t.get('x')},{t.get('y')},{t.get('z')})")
                    self._nav_target = None
                    return AgentAction(agent_name="", action="mine_block",
                        params={"x": int(t.get("x",px)), "y": int(t.get("y",py)), "z": int(t.get("z",pz))})
                # If intent target is minable and within 5 blocks, mine it
                # Skip nav-only/UNKNOWN/AIR targets — they're not verified in perception
                if intent_target and pos_key(intent_target) not in self._recently_mined:
                    it_type = intent_target.get("type", "UNKNOWN").upper()
                    if not intent_target.get("_nav_only") and it_type not in ("UNKNOWN", "AIR", "CAVE_AIR", "VOID_AIR"):
                        itx, ity, itz = int(intent_target.get("x",0)), int(intent_target.get("y",0)), int(intent_target.get("z",0))
                        if abs(itx-px) <= 5 and abs(ity-py) <= 5 and abs(itz-pz) <= 5:
                            logger.debug(f"FAST-PATH: MINE=intent-target {it_type} at ({itx},{ity},{itz})")
                            self._nav_target = None
                            return AgentAction(agent_name="", action="mine_block",
                                params={"x": itx, "y": ity, "z": itz})

            # Priority 2 (MOVE): Navigate toward intent coordinates (for mine/explore/move)
            if intent_coords:
                ix, iy, iz = int(intent_coords[0][0]), int(intent_coords[0][1]), int(intent_coords[0][2])
                dist_to_intent = math.sqrt((px-ix)**2 + (py-iy)**2 + (pz-iz)**2)
                if dist_to_intent > 3:  # Not already there
                    logger.debug(f"FAST-PATH: MOVE=intent -> ({ix},{iy},{iz}) dist={dist_to_intent:.0f}")
                    self._nav_target = (ix, iy, iz)
                    self._nav_start = time.time()
                    return AgentAction(agent_name="", action="move_to",
                                       params={"x": ix, "y": iy, "z": iz})
            
            # Priority 3: Wood/leaves nearby while exploring → stop and mine (or navigate closer)
            wood_nearby = [b for b in blocks if b.get("type","").upper() in 
                          ("OAK_LOG","BIRCH_LOG","SPRUCE_LOG","JUNGLE_LOG","ACACIA_LOG",
                           "DARK_OAK_LOG","CHERRY_LOG","MANGROVE_LOG")]
            # If only leaves, navigate toward them (trees are there)
            leaves_nearby = [b for b in blocks if b.get("type","").upper() in
                            ("OAK_LEAVES","BIRCH_LEAVES","SPRUCE_LEAVES","JUNGLE_LEAVES",
                             "ACACIA_LEAVES","DARK_OAK_LEAVES")]
            if wood_nearby and pos_key(wood_nearby[0]) not in self._recently_mined:
                t = min(wood_nearby, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
                self._nav_target = None
                return AgentAction(agent_name="", action="mine_block",
                    params={"x": int(t.get("x",px)), "y": int(t.get("y",py-1)), "z": int(t.get("z",pz))})
            if leaves_nearby and not wood_nearby:
                # Don't interrupt long-distance navigation for nearby leaves
                nav_dist = 0
                if hasattr(self, '_nav_target') and self._nav_target:
                    ntx, nty, ntz = self._nav_target
                    nav_dist = math.sqrt((px - ntx)**2 + (py - nty)**2 + (pz - ntz)**2)
                if nav_dist < 5:
                    t = min(leaves_nearby, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
                    logger.debug(f"FAST-PATH: leaves spotted at ({t.get('x')},{t.get('y')},{t.get('z')}), navigating closer")
                    self._nav_target = (t.get("x",px), t.get("y",py), t.get("z",pz))
                    self._nav_start = time.time()
                    return AgentAction(agent_name="", action="move_to",
                        params={"x": int(t.get("x",px)), "y": int(t.get("y",py)), "z": int(t.get("z",pz))})
            
            # Priority 3: Plan coordinates (skip if forest nearby and we need wood)
            nb = perception.nearby_biomes
            has_wood = any(k in ("oak_log","birch_log","spruce_log","acacia_log","dark_oak_log") 
                          for k in perception.inventory)
            forest_nearby = False
            if nb and not has_wood:
                for d, b in nb.items():
                    if "forest" in b or "taiga" in b or "jungle" in b or "grove" in b or "wood" in b:
                        forest_nearby = True
                        break
            plan_text = " ".join(self.planner.daily_plan)
            coords = re.findall(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', plan_text)
            if coords and random.random() < 0.7 and not forest_nearby:
                tx, ty, tz = map(int, random.choice(coords))
                self._nav_target = (tx, ty, tz)
                self._nav_start = time.time()
                return AgentAction(agent_name="", action="move_to",
                                   params={"x": tx, "y": ty, "z": tz})
            
            # Priority 4: Systematic exploration
            if not hasattr(self, '_explore_heading'):
                self._explore_heading = None
                self._explore_steps = 0
            # Forest lock: if forest/taiga nearby and no wood, head there and stay on course
            if not has_wood and forest_nearby and self._explore_steps == 0:
                forest_dirs = []
                dir_map = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
                for d, b in nb.items():
                    if "forest" in b or "taiga" in b or "jungle" in b or "grove" in b or "wood" in b:
                        if d in dir_map:
                            forest_dirs.append(dir_map[d])
                if forest_dirs:
                    self._explore_heading = random.choice(forest_dirs)
                    logger.debug(f"FAST-PATH: forest lock -> heading=({self._explore_heading[0]},{self._explore_heading[1]})")
            self._explore_steps += 1
            # Don't reset explore heading if locked onto forest and still need wood
            if self._explore_steps > random.randint(20 if forest_nearby else 8, 30 if forest_nearby else 15):
                self._explore_heading = None
                self._explore_steps = 0
            if self._explore_heading is None:
                self._explore_heading = random.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
            dx, dz = self._explore_heading
            dist = random.randint(30, 50) if forest_nearby else random.randint(15, 30)  # Go deeper into forest
            tx, ty, tz = px + dx * dist, py, pz + dz * dist
            logger.debug(f"FAST-PATH: MOVE=explore -> ({tx},{ty},{tz}) heading=({dx},{dz}) step={self._explore_steps} has_wood={has_wood}")
            self._nav_target = (tx, ty, tz)
            self._nav_start = time.time()
            return AgentAction(agent_name="", action="move_to",
                               params={"x": tx, "y": ty, "z": tz})

        if hint in ("rest", "idle"):
            return AgentAction(agent_name="", action="idle")

        return None

    def receive_perception(self, perception: PerceptionData):
        """Called when new perception data arrives from Minecraft."""
        self.perception_processor.update(perception)
        self._pending_perception = perception
        self._perception_event.set()

    async def receive_action_result(self, result: ActionResult):
        """Called when an action result comes back from Minecraft."""
        record = self.action_awareness.observe(
            action_id=result.action,
            success=result.success,
            details=result.details,
        )
        if record and record.discrepancy:
            await self.action_awareness.learn_from_discrepancy(self, record)
            logger.info(f"Agent {self.name} learned: {record.discrepancy}")

        # On successful mine, immediately mark coordinates as mined to prevent re-mining
        if result.success and result.action == "mine_block" and result.details:
            import re as _re_mine
            coord_match = _re_mine.search(r'at (\d+),(\d+),(\d+)', result.details)
            if coord_match:
                mkey = f"{coord_match.group(1)},{coord_match.group(2)},{coord_match.group(3)}"
                self._recently_mined[mkey] = __import__('time').time()

        # Clear build queue on place_block failure — don't keep hammering invalid positions
        if not result.success and result.action == "place_block" and self._build_queue:
            dropped = len(self._build_queue)
            self._build_queue.clear()
            logger.warning(f"BUILD-QUEUE: cleared {dropped} queued blocks after place_block failure: {result.details}")

        # Track repeated failures to avoid retrying the same broken action
        if not result.success:
            fail_key = f"{result.action}:{result.details.split(' ')[-1] if result.details else 'unknown'}"
            # For place_block failures, track the material (e.g. "place:stick")
            if "not a placeable block" in (result.details or ""):
                import re
                mat_match = re.search(r'(\w+) is not a placeable', result.details)
                if mat_match:
                    fail_key = f"place:{mat_match.group(1)}"
            # For mine_block failures on AIR, track the action
            elif "cannot mine AIR" in (result.details or ""):
                fail_key = "mine:AIR"
            self._failed_actions[fail_key] = self._failed_actions.get(fail_key, 0) + 1
            if self._failed_actions[fail_key] >= 3:
                logger.warning(f"Agent {self.name}: action '{fail_key}' failed {self._failed_actions[fail_key]}× — blacklisted for session")

        logger.info(f"Agent {self.name} action result: {result.action} -> {'OK' if result.success else 'FAIL'}: {result.details}")
        self._action_result_ready = True
        self._perception_event.set()  # Wake cognitive loop for immediate next action

    async def _emergency_help(self, problem_type: str, context: dict) -> AgentAction | None:
        """
        Ask the LLM for immediate help when the agent is stuck in a problem loop.
        Sends a concise emergency report and returns the LLM's suggested action.

        Args:
            problem_type: e.g. 'NAV_STALL', 'REPEAT_FAIL', 'STUCK_CANOPY'
            context: dict with problem details (position, failures, surroundings, etc.)
        """
        import json as _json, re as _re

        # Cooldown: don't spam the LLM
        if not hasattr(self, '_last_emergency_help'):
            self._last_emergency_help: float = 0
        now_ts = __import__('time').time()
        if now_ts - self._last_emergency_help < 15:
            logger.debug(f"EMERGENCY-HELP: cooldown active ({now_ts - self._last_emergency_help:.0f}s since last call)")
            return None
        self._last_emergency_help = now_ts

        # Build emergency report
        inv = context.get("inventory", {})
        inv_text = ", ".join(f"{k}:{v}" for k, v in sorted(inv.items()) if v > 0) if inv else "empty"
        pos = context.get("position", {})
        px, py, pz = pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)

        lines = [f"EMERGENCY: Agent {self.name} needs immediate help!",
                 f"Problem: {problem_type}",
                 f"Position: ({px}, {py}, {pz})",
                 f"Description: {context.get('description', 'No details')}",
                 f"Inventory: {inv_text}"]
        if context.get("failures"):
            lines.append(f"Recent failures: {context['failures']}")
        if context.get("surroundings"):
            lines.append(f"Surroundings: {context['surroundings']}")
        if context.get("nav_target"):
            lines.append(f"Nav target: {context['nav_target']}")

        prompt = "\n".join(lines) + """
Give ONE specific action to escape this situation immediately.
RULES: Never mine LEAVES or LOGS above you (they don't help escape). Prefer mining blocks at your feet level or teleporting to safe ground (y=63-70 in forest). If stuck in a tree canopy, teleport down.
Respond ONLY with valid JSON:
{"action": "mine_block|move_to|place_block|craft|teleport|idle", "params": {"x":int,"y":int,"z":int,...}, "reason": "one sentence why"}"""

        try:
            response = await self.llm.chat([
                {"role": "system", "content": "You are an emergency escape advisor for a Minecraft AI agent. The agent is stuck. Give ONE direct action to escape. NEVER suggest mining leaves or logs — they don't help. Prefer teleporting to solid ground at y=64-70, or mining blocks at the agent's feet. Output ONLY JSON."},
                {"role": "user", "content": prompt},
            ])
            json_str = response.strip()
            json_str = _re.sub(r'^```(?:json)?\s*', '', json_str)
            json_str = _re.sub(r'\s*```$', '', json_str)
            brace_start = json_str.find('{')
            brace_end = json_str.rfind('}')
            if brace_start >= 0 and brace_end > brace_start:
                json_str = json_str[brace_start:brace_end + 1]
            if not json_str:
                raise ValueError("Empty LLM response")

            data = _json.loads(json_str)
            action = data.get("action", "idle")
            params = data.get("params", {})
            reason = data.get("reason", "")

            logger.info(f"EMERGENCY-HELP: LLM suggests {action} {params} — {reason}")
            return AgentAction(agent_name="", action=action, params=params)
        except Exception as e:
            logger.warning(f"EMERGENCY-HELP: LLM call failed: {e}")
            return None

    def _expand_build(self, parsed: AgentAction, perception: PerceptionData | None) -> list[AgentAction]:
        """
        Expand a build() action into a queue of place_block actions.

        The LLM outputs: build(type=shelter, material=dirt, x=85, y=67, z=-80)
        This generates a 3x3x3 hollow box of place_block calls around (x, y, z).
        """
        params = parsed.params
        material = str(params.get("material", "dirt")).lower()
        cx = int(params.get("x", 0))
        cy = int(params.get("y", 0))
        cz = int(params.get("z", 0))
        build_type = str(params.get("type", "shelter")).lower()

        queue: list[AgentAction] = []

        if build_type in ("shelter", "hut", "house", "wall"):
            # 3x3 shelter: walls at floor level + 1 block up, roof on top, hollow inside
            # Floor plan (relative to center cx, cz):
            #  W W W
            #  W . W    (. = interior, W = wall)
            #  W D W    (D = doorway, left open)
            for dy in range(3):  # 3 layers: floor-walls, upper-walls, roof
                for dx in [-1, 0, 1]:
                    for dz in [-1, 0, 1]:
                        bx, by, bz = cx + dx, cy + dy, cz + dz
                        if dy < 2:
                            # Wall layers: place only perimeter blocks, skip interior
                            if dx == 0 and dz == 0:
                                continue  # hollow interior
                            # Leave a doorway at (cx, cy, cz+1) and (cx, cy+1, cz+1)
                            if dx == 0 and dz == 1 and dy < 2:
                                continue
                        # Roof layer (dy==2): place all 9 blocks
                        queue.append(AgentAction(
                            agent_name="",
                            action="place_block",
                            params={"block_type": material, "x": bx, "y": by, "z": bz},
                        ))

        logger.info(f"BUILD: expanded '{build_type}' at ({cx},{cy},{cz}) into {len(queue)} place_block actions")
        return queue

    def _try_start_build(self, decision, perception: PerceptionData) -> AgentAction | None:
        """When the controller wants to build but the LLM keeps emitting move_to/look_at
        instead of build(), start the build directly from a fast-path heuristic.

        Requires enough placeable building material in inventory. Returns the first
        place_block of the build queue, or None if we can't build yet.
        """
        import time
        inv = perception.inventory
        # Building materials we can place for a shelter, in preference order
        build_mats = ("dirt", "cobblestone", "oak_planks", "birch_planks", "spruce_planks",
                      "jungle_planks", "acacia_planks", "dark_oak_planks", "oak_log",
                      "birch_log", "spruce_log", "jungle_log")
        material, mat_count = None, 0
        for m in build_mats:
            c = inv.get(m, 0)
            if c > mat_count:
                material, mat_count = m, c
        # A 3x3 shelter needs ~20 blocks; start if we have a reasonable stock (>=8),
        # the build queue itself stops gracefully when material runs out.
        if not material or mat_count < 8:
            logger.debug(f"FAST-BUILD: not enough material (best={material}:{mat_count}), deferring")
            return None
        if getattr(self, '_recently_built', 0) and time.time() - self._recently_built < 120:
            return None

        pos = perception.position
        cx, cy, cz = int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("z", 0))
        build_params = AgentAction(agent_name="", action="build",
                                   params={"type": "shelter", "material": material,
                                           "x": cx, "y": cy, "z": cz})
        self._build_queue = self._expand_build(build_params, perception)
        if not self._build_queue:
            return None
        self._recently_built = time.time()
        logger.info(f"FAST-BUILD: starting shelter with {material} ({mat_count} avail) at ({cx},{cy},{cz})")
        return self._build_queue.pop(0)

    def _perception_changed_significantly(self, perception: PerceptionData) -> bool:
        """Check if perception changed enough to warrant a fresh controller LLM call."""
        inv_key = tuple(sorted(perception.inventory.items()))
        entity_count = len(perception.nearby_entities)
        pos_bucket = (
            round(perception.position.get('x', 0) / 5) * 5,
            round(perception.position.get('y', 0) / 5) * 5,
            round(perception.position.get('z', 0) / 5) * 5,
        )
        current_hash = hash((inv_key, entity_count, pos_bucket))
        changed = current_hash != self._last_perception_hash
        self._last_perception_hash = current_hash
        return changed

    async def cognitive_tick(self):
        """
        Run one full cognitive cycle.
        This is the agent loop: perceive → controller → retrieve → plan → reflect → execute.
        """
        if not self._running:
            return
        logger.debug(f"Tick entry {self.name} evt={self._perception_event.is_set()}")

        # APM tracking
        import time as _t
        if self._apm_start is None:
            self._apm_start = _t.time()

        # Wait for perception data or action_result signal
        try:
            await asyncio.wait_for(self._perception_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.debug(f"Agent {self.name}: waiting for perception (timeout)")
            return
        self._perception_event.clear()
        is_fast_tick = self._action_result_ready
        self._action_result_ready = False

        # Cap consecutive fast-ticks so the LLM planner keeps setting direction.
        # After 6 heuristic actions in a row, force a full LLM tick.
        if is_fast_tick:
            self._consecutive_fast_ticks = getattr(self, '_consecutive_fast_ticks', 0) + 1
            if self._consecutive_fast_ticks >= 6:
                is_fast_tick = False
                self._consecutive_fast_ticks = 0
                logger.debug("FAST-TICK-CAP: forcing full LLM tick after 6 fast-ticks")
        else:
            self._consecutive_fast_ticks = 0

        self.tick_count += 1
        perception = self._pending_perception
        if perception is None:
            return

        real_inv = {k: v for k, v in perception.inventory.items() if k.lower() != "air"}
        nb = perception.nearby_biomes
        nb_str = ""
        if nb:
            nb_str = " nbiomes=" + "|".join(f"{d[0]}:{b}" for d, b in nb.items() if b != perception.biome)
        logger.info(f"Agent {self.name} tick {self.tick_count}: pos=({perception.position.get('x',0)},{perception.position.get('y',0)},{perception.position.get('z',0)}) biome={perception.biome}{nb_str} inv={real_inv}")

        # --- NAV debug: track position changes since last move_to ---
        if hasattr(self, '_last_move_info') and self._last_move_info:
            import time as _time
            lmi = self._last_move_info
            lpx, lpy, lpz = lmi["from_pos"]
            cpx = perception.position.get("x", 0)
            cpy = perception.position.get("y", 0)
            cpz = perception.position.get("z", 0)
            pos_moved = abs(cpx - lpx) > 0.5 or abs(cpy - lpy) > 0.5 or abs(cpz - lpz) > 0.5
            ticks_since = self.tick_count - lmi["tick"]
            elapsed = _time.time() - lmi["time"]
            if pos_moved:
                logger.debug(f"NAV-PROGRESS: moved from ({lpx},{lpy},{lpz}) to ({cpx:.0f},{cpy:.0f},{cpz:.0f}) after {ticks_since}t ({elapsed:.1f}s)")
                self._last_move_info = {}  # Reset tracker on position change
            elif ticks_since >= 3 and elapsed > 4:
                logger.warning(f"NAV-STALL: no movement for {ticks_since} ticks ({elapsed:.1f}s) since move_to target=({lmi['target'][0]},{lmi['target'][1]},{lmi['target'][2]}), still at ({cpx:.0f},{cpy:.0f},{cpz:.0f})")
                # After 5+ ticks of stall, teleport with random offset to escape
                # (same X,Z teleport often fails because the spot itself is stuck)
                if ticks_since >= 5 and self._last_move_info:
                    import random as _rnd
                    dx, dz = _rnd.choice([(1,0),(-1,0),(0,1),(0,-1)])
                    offset = _rnd.randint(10, 20)
                    tp_x, tp_z = int(cpx) + dx * offset, int(cpz) + dz * offset
                    safe_y = 70
                    logger.warning(f"NAV-STALL-ESCAPE: teleporting from ({cpx:.0f},{cpy:.0f},{cpz:.0f}) to ({tp_x},{safe_y},{tp_z})")
                    self._last_move_info = {}
                    self._nav_target = None
                    if hasattr(self, '_stuck_positions'):
                        self._stuck_positions = []
                    return AgentAction(agent_name=self.name, action="teleport",
                                       params={"x": tp_x, "y": safe_y, "z": tp_z})

        try:
            # Step 0: Handle pending emergency from stuck detection (sync→async bridge)
            if hasattr(self, '_pending_emergency') and self._pending_emergency:
                emerg = self._pending_emergency
                self._pending_emergency = None
                emergency_action = await self._emergency_help(emerg["type"], emerg["context"])
                if emergency_action:
                    logger.info(f"EMERGENCY-HELP (stuck): overriding with LLM suggestion: {emergency_action.action} {emergency_action.params}")
                    return emergency_action

            # Step 1: Run Cognitive Controller (PIANO bottleneck) — with caching
            ticks_since_ctrl = self.tick_count - self._cached_decision_tick
            perception_changed = self._perception_changed_significantly(perception)
            use_cache = (
                self._cached_decision is not None
                and ticks_since_ctrl < 5
                and not perception_changed
            )
            if is_fast_tick and self._cached_decision and ticks_since_ctrl < 15:
                decision = self._cached_decision
                logger.debug(f"CTRL-CACHE: fast-tick reuse (age={ticks_since_ctrl}t)")
            elif use_cache:
                decision = self._cached_decision
                logger.debug(f"CTRL-CACHE: reusing (age={ticks_since_ctrl}t, no significant change)")
            else:
                decision = await self.controller.decide(self)
                self._cached_decision = decision
                self._cached_decision_tick = self.tick_count
                if not self._running:
                    return

            # --- DEBUG: Controller output ---
            import re as _re
            logger.debug(f"CTRL: hint={decision.action_hint} priority={decision.priority:.2f} cached={use_cache or is_fast_tick}")
            _intent_str = str(decision.intent) if not isinstance(decision.intent, str) else decision.intent
            _reason_str = str(decision.reason) if not isinstance(decision.reason, str) else decision.reason
            logger.debug(f"CTRL: intent='{_intent_str[:200]}'")
            logger.debug(f"CTRL: reason='{_reason_str[:200]}'")
            _ic = _re.findall(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', _intent_str)
            logger.debug(f"CTRL: intent_coords={_ic if _ic else 'NONE'}")
            nb = perception.nearby_biomes
            logger.debug(f"CTRL: nearby_biomes={dict(nb) if nb else 'NONE'}")
            wood_count = sum(1 for b in perception.nearby_blocks
                if b.get("type","").upper() in
                ("OAK_LOG","BIRCH_LOG","SPRUCE_LOG","JUNGLE_LOG","ACACIA_LOG",
                 "DARK_OAK_LOG","CHERRY_LOG","MANGROVE_LOG",
                 "OAK_LEAVES","BIRCH_LEAVES","SPRUCE_LEAVES","JUNGLE_LEAVES",
                 "ACACIA_LEAVES","DARK_OAK_LEAVES"))
            logger.debug(f"CTRL: wood_in_perception={wood_count} total_blocks={len(perception.nearby_blocks)}")

            # Step 2: Generate goals periodically (skip on fast-ticks to avoid LLM latency)
            if not is_fast_tick and self.tick_count % 100 == 0:
                await self.goal_generator.generate_goals(
                    self,
                    self.perception_processor.build_context_text(),
                    self.social_awareness.get_social_context(),
                )
                if not self._running:
                    return

            # Step 3: Plan action (first tick or periodically, skip on fast-ticks)
            if not is_fast_tick and (self.tick_count == 1 or self.tick_count % 50 == 0):
                await self.planner.plan_daily(self)
                if not self._running:
                    return

            # Step 4: Decide action — build queue first, then LLM/fast-path
            action_str = ""
            parsed = None

            # --- BUILD QUEUE: execute queued place_block actions one per tick ---
            if self._build_queue:
                next_block = self._build_queue[0]
                logger.debug(f"BUILD-QUEUE: placing {next_block.params} ({len(self._build_queue)} remaining)")
                parsed = next_block
                self._build_queue.pop(0)

            # --- FIX B: pre-emptive crafting/table — ALWAYS, regardless of hint ---
            # Without this the agent ran 200 ticks with logs+sticks+table but never
            # crafted tools because the slow LLM was asked instead.
            if parsed is None:
                parsed = self._try_fast_craft(perception)
                if parsed:
                    logger.debug(f"FAST-CRAFT-PATH: {parsed.action} {parsed.params}")

            # --- FIX C: build hint → start the build directly (LLM never emits build()) ---
            if parsed is None and decision.action_hint == "build" and not self._build_queue:
                parsed = self._try_start_build(decision, perception)

            # --- Fast-path: craft hint, danger, stuck, AND fast-ticks (FIX A) ---
            if parsed is None:
                _stuck_list = getattr(self, '_stuck_positions', [])
                _actually_stuck = (len(_stuck_list) >= 5 and len(set(_stuck_list[-5:])) == 1)
                use_fast_path = (
                    decision.action_hint in ("craft",)
                    or (decision.priority >= 0.9 and decision.action_hint in ("attack", "flee"))
                    or _actually_stuck
                    # FIX A: on action-result-driven ticks, continue the sequence with the
                    # heuristic instead of waiting 5-20s for the planner LLM. The LLM still
                    # runs on normal perception ticks, preserving LLM-driven direction.
                    or is_fast_tick
                )
                if use_fast_path:
                    parsed = self._decision_to_action(decision, perception)
                    if parsed:
                        logger.debug(f"FAST-PATH: priority={decision.priority:.2f} hint={decision.action_hint} fast_tick={is_fast_tick} → {parsed.action}")

            if parsed is None:
                # Primary path: LLM action decision informed by plan + retrieval
                logger.debug(f"LLM-PATH: calling planner.decide_action() (priority={decision.priority:.2f}, hint={decision.action_hint})")
                action_str = await self.planner.decide_action(self)
                logger.debug(f"LLM-PATH: returned '{action_str[:200]}'")
                if not self._running:
                    return
                parsed = self.executor.parse_action(action_str)

                # Handle build() action — LLM returns a multi-block placement plan
                if parsed and parsed.action == "build":
                    self._build_queue = self._expand_build(parsed, perception)
                    if self._build_queue:
                        parsed = self._build_queue.pop(0)
                        logger.debug(f"BUILD-QUEUE: expanded build into {len(self._build_queue)+1} blocks, starting first")
                    else:
                        parsed = None

                # If LLM returned unparseable, fall back to fast-path heuristics
                if parsed is None or parsed.action == "idle":
                    logger.debug(f"LLM-PATH: unparseable/idle, trying fast-path fallback")
                    parsed = self._decision_to_action(decision, perception)

            # Warn if agent position jumped to spawn (Citizens pathfinder bug)
            if perception and parsed and parsed.action == "move_to":
                ppos = perception.position
                if abs(ppos.get("x", 0)) <= 2 and abs(ppos.get("z", 0)) <= 2:
                    logger.warning(f"Agent {self.name} at spawn ({ppos.get('x')},{ppos.get('y')},{ppos.get('z')}) — possible pathfinder reset")
                    self._crafting_table_placed = False
                    self._recently_crafted.clear()

            # --- TRACK move_to execution ---
            if parsed and parsed.action == "move_to":
                import time as _time
                ppos = perception.position if perception else {}
                _px = ppos.get("x", 0)
                _py = ppos.get("y", 0)
                _pz = ppos.get("z", 0)
                if not hasattr(self, '_last_move_info'):
                    self._last_move_info: dict = {}
                tx = parsed.params.get("x", _px)
                ty = parsed.params.get("y", _py)
                tz = parsed.params.get("z", _pz)
                import math as _math
                dist = _math.sqrt((_px-tx)**2 + (_py-ty)**2 + (_pz-tz)**2)
                self._last_move_info = {
                    "tick": self.tick_count, "time": _time.time(),
                    "target": (tx, ty, tz),
                    "from_pos": (_px, _py, _pz),
                }
                logger.debug(f"NAV-SEND: tick={self.tick_count} from=({_px:.0f},{_py:.0f},{_pz:.0f}) to=({tx:.0f},{ty:.0f},{tz:.0f}) dist={dist:.0f}m")

            logger.info(f"Agent {self.name} tick {self.tick_count}: {parsed.action} {parsed.params}")

            # Step 5: Execute action via bridge
            if parsed and self.bridge:
                parsed.agent_name = self.name
                # Register expectation for action awareness
                self.action_awareness.expect(
                    action_id=parsed.action,
                    action=parsed.action,
                    params=parsed.params,
                    expected=f"Successfully performed {parsed.action}",
                )

                await self.bridge.send_action(parsed)

                # Send cognitive state for spectator HUD
                plan_summary = self.planner.daily_plan[0] if self.planner.daily_plan else ""
                state = AgentState(
                    agent_name=self.name,
                    current_task=self.planner.current_task[:80],
                    reason=decision.reason[:80],
                    action=f"{parsed.action} {parsed.params}",
                    plan_summary=plan_summary[:60],
                )
                await self.bridge.send_state(state)

                # If the controller suggests chatting, do that too
                if decision.chat_hint and decision.action_hint == "socialize":
                    chat = AgentChat(agent_name=self.name, text=decision.chat_hint)
                    await self.bridge.send_chat(chat)
            elif not parsed:
                logger.warning(f"Agent {self.name}: could not parse action: '{action_str}'")

            # Step 6: Accumulate importance for reflection
            importance = decision.priority * 5  # Scale to roughly match threshold
            self.reflection.accumulate_importance(importance)

            # Step 7: Reflect if threshold exceeded (skip on fast-ticks)
            if not is_fast_tick and self.reflection.should_reflect():
                await self.reflection.reflect(self)

            # Step 8: Store event in memory (skip on fast-ticks for build queue blocks)
            if not is_fast_tick or (parsed and parsed.action != "place_block"):
                action_desc = f"{parsed.action} {parsed.params}" if parsed else "idle"
                context = self.perception_processor.build_context_text()
                await self.memory.add_event(
                    content=f"[Tick {self.tick_count}] {action_desc}. Context: {context[:150]}",
                )

            # APM tracking
            self._apm_actions += 1
            elapsed_apm = _t.time() - self._apm_start
            if elapsed_apm >= 60:
                apm = self._apm_actions / (elapsed_apm / 60)
                logger.info(f"APM: {apm:.1f} actions/min over {elapsed_apm:.0f}s ({self._apm_actions} actions)")
                self._apm_start = _t.time()
                self._apm_actions = 0
            elif self.tick_count % 10 == 0 and elapsed_apm > 5:
                apm = self._apm_actions / (elapsed_apm / 60)
                logger.debug(f"APM-INTERIM: {apm:.1f} actions/min ({self._apm_actions} actions in {elapsed_apm:.0f}s)")

        except Exception as e:
            logger.error(f"Agent {self.name} cognitive tick error: {e}", exc_info=True)


class AgentManager:
    """
    Manages all AI agents in the simulation.
    Handles spawning, despawning, and running the cognitive loop for all agents.
    """

    def __init__(self):
        self.agents: dict[str, ValisAgent] = {}
        self._bridge = None
        self._despawned_recently: set[str] = set()  # Prevent auto-recreate race

    def set_bridge(self, bridge):
        """Set the WebSocket bridge for agent communication."""
        self._bridge = bridge

    async def spawn_agent(self, name: str, personality: str = "default") -> ValisAgent:
        """Create and start a new agent."""
        if name in self.agents:
            logger.warning(f"Agent {name} already exists, despawning first.")
            await self.despawn_agent(name)

        config = AgentConfig(
            name=name,
            personality=personality,
            data_dir="data",
            tick_rate=2.0,
        )
        agent = ValisAgent(config, bridge=self._bridge)
        self.agents[name] = agent
        await agent.start()

        # Send agent_spawn back to Minecraft to create the NPC
        if self._bridge:
            await self._bridge.send({"type": "agent_spawn", "name": name, "personality": personality, "x": 0, "y": 64, "z": 0})

        logger.info(f"Agent spawned: {name} ({personality}). Total agents: {len(self.agents)}")
        return agent

    async def handle_player_instruction(self, player: str, text: str):
        """Handle a chat instruction from a player — inject into all agents."""
        logger.info(f"Player instruction from {player}: {text}")
        for agent in self.agents.values():
            if text not in agent.goals:
                agent.goals.insert(0, f"[Player {player} says] {text}")
                if len(agent.goals) > 8:
                    agent.goals = agent.goals[:8]
            await agent.memory.add_event(
                content=f"[Instruction from {player}] {text}",
                importance=0.95,
                subject=player,
                predicate="instructed",
                object=text[:100],
            )
            agent._perception_event.set()

    async def despawn_agent(self, name: str):
        """Stop and remove an agent."""
        agent = self.agents.pop(name, None)
        if agent:
            await agent.stop()
            self._despawned_recently.add(name)
            logger.info(f"Agent despawned: {name}")

    async def handle_perception(self, perception: PerceptionData):
        """Route perception data to the correct agent. Auto-creates agent if unknown."""
        agent = self.agents.get(perception.agent_name)
        if agent:
            agent.receive_perception(perception)
            if perception.tick % 50 == 0:
                logger.debug(f"Perception delivered to {perception.agent_name} (tick {perception.tick})")
        else:
            # Don't auto-create if recently despawned (race condition)
            if perception.agent_name in self._despawned_recently:
                self._despawned_recently.discard(perception.agent_name)
                return
            # Auto-create agent from perception data (server already has the NPC)
            logger.info(f"Auto-creating agent from perception: {perception.agent_name}")
            await self.spawn_agent(perception.agent_name, "default")
            agent = self.agents.get(perception.agent_name)
            if agent:
                agent.receive_perception(perception)

    async def handle_action_result(self, result: ActionResult):
        """Route action result to the correct agent."""
        agent = self.agents.get(result.agent_name)
        if agent:
            await agent.receive_action_result(result)

    async def run_tick_loop(self):
        """
        Main tick loop that runs cognitive cycles for all agents.
        Uses asyncio.gather to run agents concurrently (PIANO concurrency principle).
        """
        logger.info("Agent tick loop started.")
        while True:
            if not self.agents:
                await asyncio.sleep(1)
                continue

            # Run all agent ticks concurrently
            tasks = [
                agent.cognitive_tick()
                for agent in self.agents.values()
                if agent._running
            ]
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, r in enumerate(results):
                    if isinstance(r, Exception):
                        logger.error(f"Agent tick error: {r}", exc_info=r)

            await asyncio.sleep(0.1)  # Small delay to prevent busy-loop

    def get_agent_count(self) -> int:
        return len(self.agents)

    def get_all_relationship_data(self) -> dict:
        """Get relationship graph data for all agents (for dashboard)."""
        return {
            name: agent.social_awareness.get_relationship_graph_data()
            for name, agent in self.agents.items()
        }
