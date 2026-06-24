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
        AgentAction, AgentChat, PerceptionData, ActionResult,
    )
    from .llm.providers import LLMProvider, create_llm
    from .memory import MemoryStream, MemoryRetrieval
    from .cognitive import (
        PerceptionProcessor, Planner, Reflection, Executor,
        CognitiveController, ActionAwareness, SocialAwareness, GoalGenerator,
    )
except ImportError:
    from bridge.protocol import (
        AgentAction, AgentChat, PerceptionData, ActionResult,
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
                # Block not in perception, but still navigate there
                intent_target = {"x": ix, "y": iy, "z": iz, "type": "UNKNOWN"}

        # --- Recently mined / placed / failed-place tracking ---
        if not hasattr(self, '_recently_mined'):
            self._recently_mined: dict[str, float] = {}
        if not hasattr(self, '_recently_placed'):
            self._recently_placed: dict[str, float] = {}
        if not hasattr(self, '_recently_failed_place'):
            self._recently_failed_place: dict[str, float] = {}
        now = time.time()
        self._recently_mined = {k: v for k, v in self._recently_mined.items() if now - v < 5}
        self._recently_placed = {k: v for k, v in self._recently_placed.items() if now - v < 120}
        self._recently_failed_place = {k: v for k, v in self._recently_failed_place.items() if now - v < 10}
        def pos_key(b): return f"{b.get('x',0)},{b.get('y',0)},{b.get('z',0)}"

        # --- PRE-EMPTIVE CRAFTING CHECK ---
        # If agent has materials that need processing, craft NOW regardless of controller hint.
        # This prevents the agent from running around with logs/planks without ever crafting.
        inv = perception.inventory
        all_logs = ("oak_log","birch_log","spruce_log","jungle_log","acacia_log",
                    "dark_oak_log","cherry_log","mangrove_log")
        all_planks = ("oak_planks","birch_planks","spruce_planks","jungle_planks",
                      "acacia_planks","dark_oak_planks","cherry_planks","mangrove_planks")
        total_logs = sum(inv.get(lt, 0) for lt in all_logs)
        total_planks = sum(inv.get(pt, 0) for pt in all_planks)
        total_sticks = inv.get("stick", 0)
        has_pickaxe = any("pickaxe" in k.lower() for k in inv)
        
        # Find the most abundant log/plank type for crafting
        def _find_best(key_list):
            best, best_count = None, 0
            for k in key_list:
                c = inv.get(k, 0)
                if c > best_count:
                    best, best_count = k, c
            return best
        
        craft_action = None
        if total_logs >= 1 and total_planks < 4:
            best_log = _find_best(all_logs)
            plank_type = best_log.replace("_log", "_planks") if best_log else "oak_planks"
            craft_action = AgentAction(agent_name="", action="craft", params={"item": plank_type})
            logger.debug(f"FAST-PATH: pre-emptive CRAFT planks ({best_log}={inv.get(best_log,0)})")
        # Pickaxe BEFORE sticks — reserve 3 planks for pickaxe, only craft sticks from surplus
        elif total_sticks >= 2 and total_planks >= 3 and not has_pickaxe:
            pickaxe_type = "wooden_pickaxe"
            if inv.get("cobblestone", 0) >= 3:
                pickaxe_type = "stone_pickaxe"
            craft_action = AgentAction(agent_name="", action="craft", params={"item": pickaxe_type})
            logger.debug(f"FAST-PATH: pre-emptive CRAFT {pickaxe_type} (sticks={total_sticks}, planks={total_planks})")
        # Crafting table: when we have 4+ planks and no table yet
        elif total_planks >= 4 and inv.get("crafting_table", 0) < 1:
            craft_action = AgentAction(agent_name="", action="craft", params={"item": "crafting_table"})
            logger.debug(f"FAST-PATH: pre-emptive CRAFT crafting_table (planks={total_planks})")
        elif total_planks >= 5 and total_sticks < 4:
            # Only craft sticks if we have 5+ planks (leaving 3 for pickaxe)
            craft_action = AgentAction(agent_name="", action="craft", params={"item": "stick"})
            logger.debug(f"FAST-PATH: pre-emptive CRAFT sticks (planks={total_planks}, surplus={total_planks-3})")
        
        if craft_action:
            return craft_action

        # --- CRAFTING TABLE: place if we have one but none nearby ---
        has_crafting_table_inv = inv.get("crafting_table", 0) >= 1
        has_crafting_table_nearby = any(
            b.get("type", "").upper() == "CRAFTING_TABLE" and abs(b.get("x",0)-px) <= 3 and abs(b.get("z",0)-pz) <= 3
            for b in blocks
        )
        if has_crafting_table_inv and not has_crafting_table_nearby:
            # Place crafting table at agent's feet+1
            tx, ty, tz = px, py + 1, pz
            # Check if position is air
            blocked = any(b.get("x",0)==tx and b.get("y",0)==ty and b.get("z",0)==tz
                          and b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR") for b in blocks)
            if not blocked:
                logger.debug(f"FAST-PATH: placing crafting_table at ({tx},{ty},{tz})")
                return AgentAction(agent_name="", action="place_block",
                                   params={"block_type": "crafting_table", "x": tx, "y": ty, "z": tz})

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
                            self._recently_mined[tkey] = now
                            return AgentAction(agent_name="", action="mine_block", params={"x": int(tx), "y": int(ty), "z": int(tz)})
                else:
                    inv = perception.inventory
                    place_mat = "dirt"
                    if inv:
                        placeable = {k:v for k,v in inv.items() 
                            if k.lower() not in ("air", "wheat_seeds", "cornflower",
                            "oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log",
                            "dark_oak_log", "cherry_log", "mangrove_log",
                            "oak_wood", "spruce_wood", "birch_wood")}
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
            logger.debug(f"FAST-PATH: craft hint -> returning None for LLM fallback")
            return None  # Let LLM decide

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
                            if btype not in ("AIR","CAVE_AIR","VOID_AIR","BEDROCK","WATER","LAVA"):
                                tkey = f"{px+dx},{py+dy},{pz+dz}"
                                if tkey not in self._recently_mined:
                                    logger.debug(f"FAST-PATH: STUCK-DIG mining {btype} at ({px+dx},{py+dy},{pz+dz}) to escape")
                                    self._recently_mined[tkey] = now
                                    return AgentAction(agent_name="", action="mine_block",
                                                       params={"x": px+dx, "y": py+dy, "z": pz+dz})
                # If 4 dig attempts found nothing, reset and jump
                if self._stuck_mine_attempts >= 4:
                    self._stuck_mine_attempts = 0
                self._nav_target = None
                self._explore_heading = None
                self._explore_steps = 0
                self._stuck_positions = []
                # Try a random direction away from current position
                import random as _random
                dx, dz = _random.choice([(1,0), (-1,0), (0,1), (0,-1)])
                dist = _random.randint(20, 40)
                tx, ty, tz = px + dx * dist, py + 3, pz + dz * dist  # aim a bit higher to get out of holes
                self._nav_target = (tx, ty, tz)
                self._nav_start = time.time()
                logger.debug(f"FAST-PATH: anti-stuck jump to ({tx},{ty},{tz})")
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
            
            # Priority 1: Use LLM intent coordinates for navigation
            if intent_coords:
                ix, iy, iz = int(intent_coords[0][0]), int(intent_coords[0][1]), int(intent_coords[0][2])
                dist_to_intent = math.sqrt((px-ix)**2 + (py-iy)**2 + (pz-iz)**2)
                if dist_to_intent > 3:  # Not already there
                    logger.debug(f"FAST-PATH: MOVE=intent -> ({ix},{iy},{iz}) dist={dist_to_intent:.0f}")
                    self._nav_target = (ix, iy, iz)
                    self._nav_start = time.time()
                    return AgentAction(agent_name="", action="move_to",
                                       params={"x": ix, "y": iy, "z": iz})
            
            # Priority 2: Wood/leaves nearby while exploring → stop and mine (or navigate closer)
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
        logger.info(f"Agent {self.name} action result: {result.action} -> {'OK' if result.success else 'FAIL'}: {result.details}")
        self._perception_event.set()  # Wake cognitive loop to process result

    async def cognitive_tick(self):
        """
        Run one full cognitive cycle.
        This is the agent loop: perceive → controller → retrieve → plan → reflect → execute.
        """
        if not self._running:
            return
        logger.debug(f"Tick entry {self.name} evt={self._perception_event.is_set()}")

        # Wait for perception data
        try:
            await asyncio.wait_for(self._perception_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.debug(f"Agent {self.name}: waiting for perception (timeout)")
            return
        self._perception_event.clear()

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

        try:
            # Step 1: Run Cognitive Controller (PIANO bottleneck)
            decision = await self.controller.decide(self)
            if not self._running:
                return

            # --- DEBUG: Controller output ---
            import re as _re
            logger.debug(f"CTRL: hint={decision.action_hint} priority={decision.priority:.2f}")
            logger.debug(f"CTRL: intent='{decision.intent[:200]}'")
            logger.debug(f"CTRL: reason='{decision.reason[:200]}'")
            _ic = _re.findall(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', decision.intent)
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

            # Step 2: Generate goals periodically
            if self.tick_count % 30 == 0:
                await self.goal_generator.generate_goals(
                    self,
                    self.perception_processor.build_context_text(),
                    self.social_awareness.get_social_context(),
                )
                if not self._running:
                    return

            # Step 3: Plan action (first tick or when task changes)
            if self.tick_count == 1 or self.tick_count % 10 == 0:
                await self.planner.plan_daily(self)
                if not self._running:
                    return

            # Step 4: Convert controller decision directly to action (fast path)
            action_str = ""
            parsed = self._decision_to_action(decision, perception)
            if parsed is None:
                # Fallback: LLM action decision for complex cases
                logger.debug(f"FALLBACK: fast-path returned None, calling planner.decide_action()...")
                action_str = await self.planner.decide_action(self)
                logger.debug(f"FALLBACK: LLM returned '{action_str[:200]}'")
                if not self._running:
                    return
                parsed = self.executor.parse_action(action_str)
                # If LLM returned empty/unparseable, use heuristic fallback instead of idle
                if parsed is None or parsed.action == "idle":
                    import random as _random
                    inv = perception.inventory
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
                    
                    if total_logs >= 1 and total_planks < 4:
                        best_log = _find_best(all_logs)
                        plank_type = best_log.replace("_log", "_planks") if best_log else "oak_planks"
                        logger.debug(f"FALLBACK-HEURISTIC: crafting planks ({best_log}={inv.get(best_log,0)})")
                        parsed = AgentAction(agent_name="", action="craft", params={"item": plank_type})
                    # Pickaxe BEFORE sticks — reserve 3 planks for pickaxe
                    elif total_sticks >= 2 and total_planks >= 3 and not has_pickaxe:
                        pickaxe_type = "wooden_pickaxe"
                        if inv.get("cobblestone", 0) >= 3:
                            pickaxe_type = "stone_pickaxe"
                        logger.debug(f"FALLBACK-HEURISTIC: crafting {pickaxe_type}")
                        parsed = AgentAction(agent_name="", action="craft", params={"item": pickaxe_type})
                    elif total_planks >= 5 and total_sticks < 4:
                        logger.debug(f"FALLBACK-HEURISTIC: crafting sticks (planks={total_planks})")
                        parsed = AgentAction(agent_name="", action="craft", params={"item": "stick"})
                    else:
                        logger.debug(f"FALLBACK-HEURISTIC: nothing to craft, staying idle")

            # Warn if agent position jumped to spawn (Citizens pathfinder bug)
            if perception and parsed and parsed.action == "move_to":
                ppos = perception.position
                if abs(ppos.get("x", 0)) <= 2 and abs(ppos.get("z", 0)) <= 2:
                    logger.warning(f"Agent {self.name} at spawn ({ppos.get('x')},{ppos.get('y')},{ppos.get('z')}) — possible pathfinder reset")

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

                # If the controller suggests chatting, do that too
                if decision.chat_hint and decision.action_hint == "socialize":
                    chat = AgentChat(agent_name=self.name, text=decision.chat_hint)
                    await self.bridge.send_chat(chat)
            elif not parsed:
                logger.warning(f"Agent {self.name}: could not parse action: '{action_str}'")

            # Step 6: Accumulate importance for reflection
            importance = decision.priority * 5  # Scale to roughly match threshold
            self.reflection.accumulate_importance(importance)

            # Step 7: Reflect if threshold exceeded
            if self.reflection.should_reflect():
                await self.reflection.reflect(self)

            # Step 8: Store event in memory
            context = self.perception_processor.build_context_text()
            await self.memory.add_event(
                content=f"[Tick {self.tick_count}] At {context[:200]}",
                importance=importance / 10.0,
            )

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
