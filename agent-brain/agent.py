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

# Population cap — protects LLM budget while allowing council-driven growth
MAX_VILLAGERS = 6


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    name: str = "Agent"
    personality: str = "default"
    llm_provider: str = field(default_factory=lambda: os.environ.get("VALIS_DEFAULT_LLM", "ollama"))
    llm_model: str = field(default_factory=lambda: os.environ.get("VALIS_DEFAULT_MODEL", "mistral"))
    data_dir: str = field(default_factory=lambda: os.environ.get("VALIS_DATA_DIR", "data"))
    tick_rate: float = 2.0
    traits: list[str] = field(default_factory=list)
    initial_goals: list[str] = field(default_factory=list)
    focus: str = ""  # one-line description of role/specialization


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
        self.traits = config.traits
        self.focus = config.focus
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
            importance_fn=self._score_importance_heuristic,
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
        self._pending_shelter_pos: tuple[int, int, int] | None = None
        self._build_blocks_placed: int = 0

        # Performance: controller cache + APM tracking
        self._cached_decision: object | None = None
        self._cached_decision_tick: int = 0
        self._last_perception_hash: int | None = None
        self._action_result_ready: bool = False
        self._apm_actions: int = 0
        self._apm_start: float | None = None
        self._tables_crafted_total: int = 0
        self._nav_target_attempts: dict[str, int] = {}  # track attempts per nav target
        self._blacklisted_nav_targets: set[str] = set()  # unreachable targets
        self.settlement: "Settlement | None" = None  # set by AgentManager after creation

        # Chat inbox — accumulates across perception overwrites, drained by tick loop
        self._chat_inbox: list[str] = []
        # Village Council assignment for this agent (set by AgentManager)
        self._council_assignment: str = ""
        self._council_tick: int = 0

        # Personal convictions — the atoms of culture. Formed by own reflections,
        # adoptable from other agents' chat (with attribution). Persisted per
        # agent so a villager's worldview survives restarts, like the chronicle.
        self.beliefs: list[dict] = []  # {"text", "source", "importance"}
        self._beliefs_path = os.path.join(config.data_dir, config.name, "beliefs.json")
        self._load_beliefs()

        logger.info(f"Agent created: {self.name} ({self.personality}) [{self.agent_id}]")

    def _load_beliefs(self):
        import json
        try:
            with open(self._beliefs_path, "r", encoding="utf-8") as f:
                self.beliefs = json.load(f)[:3]
            if self.beliefs:
                logger.info(f"CULTURE: {self.name} restored {len(self.beliefs)} conviction(s)")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"CULTURE: belief load failed for {self.name}: {e}")

    def _save_beliefs(self):
        import json
        try:
            os.makedirs(os.path.dirname(self._beliefs_path), exist_ok=True)
            with open(self._beliefs_path, "w", encoding="utf-8") as f:
                json.dump(self.beliefs, f, indent=1)
        except Exception as e:
            logger.warning(f"CULTURE: belief save failed for {self.name}: {e}")

    def adopt_belief(self, text: str, source: str, importance: float = 0.6):
        """Add a conviction (max 3 — the weakest gets replaced)."""
        text = " ".join(str(text).split())[:200]
        if not text or any(b["text"] == text for b in self.beliefs):
            return
        self.beliefs.append({"text": text, "source": source, "importance": importance})
        self.beliefs.sort(key=lambda b: -b["importance"])
        dropped = self.beliefs[3:]
        self.beliefs = self.beliefs[:3]
        origin = "formed own" if source == "own reflection" else f"adopted ({source})"
        logger.info(f"CULTURE: {self.name} {origin} belief: {text[:90]}")
        if dropped:
            logger.debug(f"CULTURE: {self.name} outgrew belief: {dropped[0]['text'][:60]}")
        self._save_beliefs()

    async def _consider_belief_adoption(self, sender: str, statement: str):
        """LLM decides whether a heard conviction resonates enough to adopt.
        PIANO-pure: hearing is mechanical, adoption is a cognitive decision."""
        try:
            own = "; ".join(b["text"][:80] for b in self.beliefs) or "none yet"
            response = await self.llm.chat([
                {"role": "system",
                 "content": "You decide if a heard idea becomes one of your personal "
                            "convictions. Answer ONLY YES or NO with one short reason."},
                {"role": "user",
                 "content": f"You are {self.name}, a {self.personality} "
                            f"(traits: {', '.join(self.traits) or 'none'}).\n"
                            f"Your current convictions: {own}\n"
                            f"{sender} said: \"{statement}\"\n"
                            f"Does this idea resonate with who you are — enough to "
                            f"adopt it as your own conviction?"},
            ])
            if response.strip().upper().startswith("YES"):
                self.adopt_belief(statement, source=f"adopted from {sender}",
                                  importance=0.55)
                await self.memory.add_event(
                    content=f"[Culture] I adopted a conviction from {sender}: {statement}",
                    importance=0.7,
                    subject=self.name, predicate="adopted belief from", object=sender,
                )
        except Exception as e:
            logger.debug(f"CULTURE: belief adoption check failed: {e}")

    # --- Public API ---

    async def start(self):
        """Start the agent's cognitive loop."""
        self._running = True
        # Seed goal_generator from personality goals if any, else generic defaults
        if self.config.initial_goals:
            from cognitive.goal_generation import Goal
            self.goal_generator.goals = [
                Goal(description=g, goal_type="survival", priority=0.7)
                for g in self.config.initial_goals
            ]
        else:
            self.goal_generator.initialize_default_goals()
        logger.info(f"Agent {self.name} started cognitive loop ({self.personality}).")

    async def stop(self):
        """Stop the agent's cognitive loop."""
        self._running = False
        self._perception_event.set()  # Unblock any waiting
        logger.info(f"Agent {self.name} stopped.")

    async def _score_importance_heuristic(self, content: str) -> float:
        """Score memory importance via keyword heuristics instead of LLM.

        The LLM-based scorer consumed 45% of all API calls (~200 calls, 9 minutes)
        per session for marginal benefit. Keyword matching is instant and good enough
        for the recency × relevance × importance retrieval formula.
        """
        lower = content.lower()
        score = 0.2

        critical = ["died", "death", "killed", "diamond", "strategy change",
                     "fundamental", "critical", "emergency", "insight"]
        important = ["craft", "pickaxe", "axe", "sword", "shelter", "build",
                      "danger", "attack", "found", "iron", "learned", "reflection",
                      "goal", "stuck", "escape", "teleport"]
        moderate = ["mine", "place", "wood", "log", "planks", "stone", "cobblestone",
                    "resource", "gather", "collect", "inventory"]
        routine = ["move_to", "idle", "explore", "walk", "look", "navigate"]

        for kw in critical:
            if kw in lower:
                score += 0.25
        for kw in important:
            if kw in lower:
                score += 0.1
        for kw in moderate:
            if kw in lower:
                score += 0.05
        for kw in routine:
            if kw in lower:
                score -= 0.05

        return max(0.1, min(1.0, score))

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
        _settlement_table_nearby = False
        if self.settlement:
            for stx, sty, stz in self.settlement.crafting_tables:
                if abs(px - stx) <= 4 and abs(py - sty) <= 3 and abs(pz - stz) <= 4:
                    _settlement_table_nearby = True
                    break
        has_crafting_table_nearby = (
            self._crafting_table_placed or _settlement_table_nearby or
            any(b.get("type", "").upper() == "CRAFTING_TABLE"
                and abs(b.get("x",0)-px) <= 3 and abs(b.get("y",0)-py) <= 3 and abs(b.get("z",0)-pz) <= 3
                for b in blocks)
        )
        _REPLACEABLE = frozenset({"AIR","CAVE_AIR","VOID_AIR","SHORT_GRASS","TALL_GRASS",
                                    "FERN","LARGE_FERN","DEAD_BUSH","SNOW","VINE","LEAF_LITTER"})
        # Will we want to craft a table-requiring tool soon? (pickaxe/axe/sword all need a
        # table). Place the table whenever we hold one, none is nearby, and we have the
        # materials for any such tool — otherwise an LLM-chosen sword craft fails with
        # "need nearby crafting_table" because we never put the table down.
        has_sword = any("sword" in k.lower() for k in inv)
        wants_tool = (total_sticks >= 1 and total_planks >= 2
                      and (not has_pickaxe or not any("axe" in k.lower() for k in inv) or not has_sword))
        if has_crafting_table_inv and not has_crafting_table_nearby and wants_tool:
            tx, ty, tz = px, py + 1, pz
            blocked = any(b.get("x",0)==tx and b.get("y",0)==ty and b.get("z",0)==tz
                          and b.get("type","").upper() not in _REPLACEABLE for b in blocks)
            if not blocked:
                logger.debug(f"FAST-CRAFT: placing crafting_table at ({tx},{ty},{tz}) before crafting tool")
                self._crafting_table_placed = True
                self._crafting_table_pos = (tx, ty, tz)
                if self.settlement:
                    self.settlement.register_crafting_table(tx, ty, tz)
                return AgentAction(agent_name="", action="place_block",
                                   params={"block_type": "crafting_table", "x": tx, "y": ty, "z": tz})

        # A crafting table is "available" if we hold one, placed one, or see one nearby.
        # Reset the placed flag if we wandered far from where we put it.
        if self._crafting_table_placed and self._crafting_table_pos:
            ctx, cty, ctz = self._crafting_table_pos
            if abs(px - ctx) > 4 or abs(py - cty) > 3 or abs(pz - ctz) > 4:
                self._crafting_table_placed = False
                self._crafting_table_pos = None
        has_any_table = (inv.get("crafting_table", 0) >= 1
                         or self._crafting_table_placed or has_crafting_table_nearby)

        # Tech-tree progression. Order matters: keep a plank buffer (>=6) so we never
        # deadlock at exactly 4 planks (where the only affordable craft was a 2nd table).
        craft_action = None
        if total_logs >= 1 and total_planks < 6:
            best_log = _find_best(all_logs)
            plank_type = best_log.replace("_log", "_planks") if best_log else "oak_planks"
            if plank_type not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": plank_type})
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT planks ({best_log}={inv.get(best_log,0)})")
        # Crafting table: ONLY if none exists anywhere AND we haven't crafted too many
        elif not has_any_table and total_planks >= 4 and getattr(self, '_tables_crafted_total', 0) < 2:
            if "crafting_table" not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": "crafting_table"})
                self._tables_crafted_total = getattr(self, '_tables_crafted_total', 0) + 1
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT crafting_table (planks={total_planks}, total_tables={self._tables_crafted_total})")
        # Sticks: need them for every tool; only planks required (no table). Keep >=3 for a pickaxe.
        elif total_sticks < 2 and total_planks >= 5:
            if "stick" not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": "stick"})
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT sticks (planks={total_planks}, sticks={total_sticks})")
        # Pickaxe: 3 planks + 2 sticks (needs a table nearby — placed above if we had one)
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
        # Top up sticks for the second tool once the first is done.
        elif total_sticks < 2 and total_planks >= 4 and has_pickaxe:
            if "stick" not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": "stick"})
                logger.debug(f"FAST-CRAFT: top-up sticks for axe (planks={total_planks})")
        # Furnace: gateway to the iron age. 8 cobblestone, needs table.
        elif (inv.get("furnace", 0) == 0 and inv.get("cobblestone", 0) >= 8
              and has_crafting_table_nearby
              and not any(b.get("type","").upper() in ("FURNACE","BLAST_FURNACE")
                          and abs(b.get("x",0)-px) <= 4 and abs(b.get("z",0)-pz) <= 4
                          for b in blocks)):
            if "furnace" not in self._recently_crafted:
                craft_action = AgentAction(agent_name="", action="craft", params={"item": "furnace"})
                logger.debug(f"FAST-CRAFT: pre-emptive CRAFT furnace (cobblestone={inv.get('cobblestone',0)})")

        # Pre-emptive smelting: raw ore + furnace + coal → ingots (same exception
        # category as pre-emptive crafting — pure tech-tree progression).
        if craft_action is None:
            has_furnace_access = (inv.get("furnace", 0) >= 1
                or any(b.get("type","").upper() in ("FURNACE","BLAST_FURNACE")
                       and abs(b.get("x",0)-px) <= 4 and abs(b.get("y",0)-py) <= 2
                       and abs(b.get("z",0)-pz) <= 4 for b in blocks))
            fuel = inv.get("coal", 0) + inv.get("charcoal", 0)
            if has_furnace_access and fuel >= 1:
                for raw in ("raw_iron", "raw_copper", "raw_gold"):
                    n = inv.get(raw, 0)
                    if n >= 1 and f"smelt:{raw}" not in self._recently_crafted:
                        self._recently_crafted[f"smelt:{raw}"] = now
                        logger.debug(f"FAST-SMELT: pre-emptive SMELT {n}x {raw} (fuel={fuel})")
                        return AgentAction(agent_name="", action="smelt",
                                           params={"item": raw, "amount": min(n, 8)})

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
        self._recently_mined = {k: v for k, v in self._recently_mined.items() if now - v < 30}
        self._recently_placed = {k: v for k, v in self._recently_placed.items() if now - v < 120}
        self._recently_failed_place = {k: v for k, v in self._recently_failed_place.items() if now - v < 10}
        # Shorter cooldown (5s instead of 15s) — prevents deadlocks where craft fails
        # and the agent idles for 15 seconds before retrying
        self._recently_crafted = {k: v for k, v in self._recently_crafted.items() if now - v < 5}
        def pos_key(b): return f"{b.get('x',0)},{b.get('y',0)},{b.get('z',0)}"

        # Village infrastructure protection: heuristic mining must never target
        # blocks the village registered (crafting tables, chest, shelters). The
        # LLM can still explicitly order it via intent coords — this only guards
        # the reflex paths that pick "nearest minable block".
        _INFRA_TYPES = frozenset({"CRAFTING_TABLE", "CHEST", "FURNACE", "TORCH",
                                  "WHEAT", "FARMLAND", "CARROTS", "POTATOES", "BEETROOTS"})
        def is_protected(b) -> bool:
            btype = b.get("type", "").upper()
            if btype in _INFRA_TYPES:
                return True
            if not self.settlement:
                return False
            bx, by, bz = b.get("x", 0), b.get("y", 0), b.get("z", 0)
            for sx, sy, sz in self.settlement.shelter_positions:
                if abs(bx - sx) <= 2 and abs(by - sy) <= 3 and abs(bz - sz) <= 2:
                    return True
            return False

        # --- PRE-EMPTIVE CRAFTING + TABLE PLACEMENT (extracted, runs every tick) ---
        fast_craft = self._try_fast_craft(perception)
        if fast_craft:
            return fast_craft

        # --- PROACTIVE CANOPY DESCENT ---
        # If the agent is standing on a LEAF block, it climbed into a tree and will get
        # stuck (Citizens can't path down through the canopy). Leaves are never legitimate
        # standing ground, so mine straight down immediately instead of waiting for the
        # 5-tick stuck detector. This was the root of the repeated NAV-STALL teleports.
        below = next((b for b in blocks
                      if b.get("x",0)==px and b.get("y",0)==py-1 and b.get("z",0)==pz), None)
        if below and "_LEAVES" in below.get("type","").upper() and py > 65:
            tkey = f"{px},{py-1},{pz}"
            if tkey not in self._recently_mined:
                logger.warning(f"FAST-PATH: CANOPY-DESCENT (proactive) mining leaves below at ({px},{py-1},{pz})")
                self._recently_mined[tkey] = now
                return AgentAction(agent_name="", action="mine_block",
                                   params={"x": px, "y": py-1, "z": pz})

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
                # Don't target logs high above us — chasing canopy logs makes the Citizens
                # pathfinder climb the tree and get stuck in the leaves. Prefer logs at/below
                # foot level, and break ties by the LOWEST log (harvest the trunk bottom-up).
                reachable = [b for b in wood_blocks if b.get("y", 0) <= py + 2]
                candidates = reachable or wood_blocks
                if candidates:
                    target = min(candidates, key=lambda b: (
                        b.get("y", 0),  # lowest first → mine trunk from the bottom
                        abs(b.get("x",0)-px) + abs(b.get("z",0)-pz),  # then nearest horizontally
                    ))
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
                    _SURFACE_JUNK_NAV = {"GRASS_BLOCK","SHORT_GRASS","TALL_GRASS","DIRT","SAND","GRAVEL",
                                         "COARSE_DIRT","PODZOL","MUD","ROOTED_DIRT","FARMLAND","DIRT_PATH",
                                         "FERN","LARGE_FERN","DANDELION","POPPY","DEAD_BUSH",
                                         "SUNFLOWER","LILAC","ROSE_BUSH","PEONY"}
                    minable_nearby = [b for b in blocks
                                    if b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR","BEDROCK","WATER","LAVA")
                                    and "_LEAVES" not in b.get("type","").upper()
                                    and not is_protected(b)
                                    and pos_key(b) not in self._recently_mined
                                    and abs(b.get("x",0)-px) <= 4 and abs(b.get("y",0)-py) <= 4
                                    and abs(b.get("z",0)-pz) <= 4]
                    if minable_nearby:
                        valuable_nearby = [b for b in minable_nearby if b.get("type","").upper() not in _SURFACE_JUNK_NAV]
                        pick_from = valuable_nearby if valuable_nearby else minable_nearby
                        t = min(pick_from, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
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
                    # Don't re-place a crafting table we already placed/see nearby — this caused
                    # a perception-lag loop that hammered place_block into the tree canopy.
                    if intent_block == "crafting_table":
                        table_nearby = (
                            self._crafting_table_placed or
                            any(b.get("type","").upper() == "CRAFTING_TABLE"
                                and abs(b.get("x",0)-px) <= 4 and abs(b.get("z",0)-pz) <= 4
                                for b in blocks)
                        )
                        if table_nearby:
                            logger.debug("FAST-PATH: crafting_table already placed/nearby, skipping re-place")
                            return None
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
        if hint == "collect" or any(phrase in intent.lower() for phrase in
                                    ("collect items", "collect drops", "pick up items", "pick up drops")):
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
                # CANOPY DESCENT: if we're stuck standing ON leaves/logs (trapped in a tree),
                # mine the block directly below to drop straight down. The N/E/S/W dig below
                # deliberately skips leaves/logs, so without this the agent can never get out
                # of a canopy and has to wait many ticks for the teleport fallback.
                below_block = next((b for b in blocks
                                    if b.get("x",0)==px and b.get("y",0)==py-1 and b.get("z",0)==pz), None)
                if below_block:
                    below_type = below_block.get("type","").upper()
                    if ("_LEAVES" in below_type or "_LOG" in below_type) and py > 65:
                        tkey = f"{px},{py-1},{pz}"
                        if tkey not in self._recently_mined:
                            logger.warning(f"FAST-PATH: CANOPY-DESCENT mining {below_type} below at ({px},{py-1},{pz}) to drop down")
                            self._recently_mined[tkey] = now
                            self._stuck_positions = []
                            return AgentAction(agent_name="", action="mine_block",
                                               params={"x": px, "y": py-1, "z": pz})
                # Before jumping, try to mine our way out — dig blocks around us
                # BUT skip our own shelter blocks to avoid destroying our builds
                if not hasattr(self, '_stuck_mine_attempts'):
                    self._stuck_mine_attempts = 0
                self._stuck_mine_attempts += 1
                dig_dirs = [(1,0), (0,1), (-1,0), (0,-1)]
                dig_idx = (self._stuck_mine_attempts - 1) % 4
                dx, dz = dig_dirs[dig_idx]
                for dy in (-1, 0, 1):
                    for b in blocks:
                        if b.get("x",0)==px+dx and b.get("y",0)==py+dy and b.get("z",0)==pz+dz:
                            btype = b.get("type","").upper()
                            if btype not in ("AIR","CAVE_AIR","VOID_AIR","BEDROCK","WATER","LAVA",
                                             "CRAFTING_TABLE") \
                               and "_LEAVES" not in btype and "_LOG" not in btype \
                               and f"{px+dx},{py+dy},{pz+dz}" not in self._recently_placed:
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
            # Don't interrupt ongoing navigation — re-send move_to instead of idling
            # (idle wastes a fast-tick action slot and drops APM)
            if hasattr(self, '_nav_target') and self._nav_target:
                tx, ty, tz = self._nav_target
                dist = math.sqrt((px - tx)**2 + (py - ty)**2 + (pz - tz)**2)
                elapsed = time.time() - getattr(self, '_nav_start', 0)
                if dist > 3 and elapsed < 8:
                    # Mine any reachable wood/ore blocks while walking (all hints, not just mine)
                    has_wood = any(k in ("oak_log","birch_log","spruce_log","acacia_log","dark_oak_log","cherry_log")
                                  for k in perception.inventory)
                    walk_minable = [b for b in blocks
                                   if b.get("type","").upper() in
                                   ("OAK_LOG","BIRCH_LOG","SPRUCE_LOG","JUNGLE_LOG","ACACIA_LOG",
                                    "DARK_OAK_LOG","CHERRY_LOG","MANGROVE_LOG","COAL_ORE",
                                    "IRON_ORE","COPPER_ORE")
                                   and not is_protected(b)
                                   and abs(b.get("x",0)-px) <= 4 and abs(b.get("z",0)-pz) <= 4
                                   and b.get("y",0) <= py + 2
                                   and pos_key(b) not in self._recently_mined]
                    if walk_minable:
                        t = min(walk_minable, key=lambda b: (
                            b.get("y", 0),
                            abs(b.get("x",0)-px)+abs(b.get("z",0)-pz),
                        ))
                        self._recently_mined[pos_key(t)] = now
                        logger.debug(f"FAST-PATH: mine-while-walking {t.get('type','?')} at ({t.get('x')},{t.get('y')},{t.get('z')})")
                        return AgentAction(agent_name="", action="mine_block",
                            params={"x": int(t.get("x",px)), "y": int(t.get("y",py)), "z": int(t.get("z",pz))})
                    logger.debug(f"FAST-PATH: nav in progress, dist={dist:.1f} elapsed={elapsed:.1f}s")
                    return None  # return None → caller falls to LLM-PATH only on non-fast-ticks
                # Arrived — clear nav target and reset attempts counter for this target
                nav_k = f"{int(tx)},{int(ty)},{int(tz)}"
                if nav_k in self._nav_target_attempts:
                    del self._nav_target_attempts[nav_k]
                self._nav_target = None

            # Priority 0 (WOOD): Opportunistic wood mining — any hint, if logs are reachable
            # This is the critical fix: agents would walk THROUGH forests without mining
            # because wood-mining only triggered on hint=="mine". Now every hint checks.
            has_wood = any(k in ("oak_log","birch_log","spruce_log","acacia_log","dark_oak_log","cherry_log")
                          for k in perception.inventory)
            has_planks = any("planks" in k for k in perception.inventory)
            if not has_wood and not has_planks:
                wood_nearby = [b for b in blocks
                              if b.get("type","").upper() in
                              ("OAK_LOG","BIRCH_LOG","SPRUCE_LOG","JUNGLE_LOG","ACACIA_LOG",
                               "DARK_OAK_LOG","CHERRY_LOG","MANGROVE_LOG")
                              and not is_protected(b)
                              and b.get("y", 0) <= py + 2
                              and pos_key(b) not in self._recently_mined]
                if wood_nearby:
                    t = min(wood_nearby, key=lambda b: (
                        b.get("y", 0),
                        abs(b.get("x",0)-px) + abs(b.get("z",0)-pz),
                    ))
                    tdist = abs(t.get("x",0)-px) + abs(t.get("z",0)-pz)
                    if tdist <= 4:
                        self._recently_mined[pos_key(t)] = now
                        logger.debug(f"FAST-PATH: WOOD-GRAB {t.get('type','?')} at ({t.get('x')},{t.get('y')},{t.get('z')}) hint={hint}")
                        return AgentAction(agent_name="", action="mine_block",
                            params={"x": int(t.get("x",px)), "y": int(t.get("y",py)), "z": int(t.get("z",pz))})
                    else:
                        logger.debug(f"FAST-PATH: WOOD-NAV to {t.get('type','?')} at ({t.get('x')},{t.get('y')},{t.get('z')}) dist={tdist}")
                        self._nav_target = (int(t.get("x",px)), int(t.get("y",py)), int(t.get("z",pz)))
                        self._nav_start = time.time()
                        return AgentAction(agent_name="", action="move_to",
                            params={"x": int(t.get("x",px)), "y": int(t.get("y",py)), "z": int(t.get("z",pz))})

            # Priority 1 (MINE): Mine nearby blocks first — don't navigate to far-away intent coords
            if hint == "mine":
                _SURFACE_JUNK = {"GRASS_BLOCK","SHORT_GRASS","TALL_GRASS","DIRT","SAND","GRAVEL",
                                 "COARSE_DIRT","PODZOL","MUD","ROOTED_DIRT","FARMLAND","DIRT_PATH",
                                 "FERN","LARGE_FERN","DANDELION","POPPY","BLUE_ORCHID","ALLIUM",
                                 "AZURE_BLUET","OXEYE_DAISY","CORNFLOWER","LILY_OF_THE_VALLEY",
                                 "SUNFLOWER","LILAC","ROSE_BUSH","PEONY","DEAD_BUSH"}
                minable = [b for b in blocks
                          if b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR","BEDROCK","WATER","LAVA","CRAFTING_TABLE")
                          and "_LEAVES" not in b.get("type","").upper()
                          and not is_protected(b)
                          and pos_key(b) not in self._recently_mined]
                close_minable = [b for b in minable
                                if abs(b.get("x",0)-px) <= 4 and abs(b.get("y",0)-py) <= 4
                                and abs(b.get("z",0)-pz) <= 4]
                if close_minable:
                    # Prefer valuable blocks (logs, ores, stone) over surface junk
                    valuable = [b for b in close_minable if b.get("type","").upper() not in _SURFACE_JUNK]
                    pick_from = valuable if valuable else close_minable
                    t = min(pick_from, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
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
                nav_key = f"{ix},{iy},{iz}"
                if nav_key in self._blacklisted_nav_targets:
                    logger.debug(f"FAST-PATH: skipping blacklisted nav target ({ix},{iy},{iz})")
                else:
                    dist_to_intent = math.sqrt((px-ix)**2 + (py-iy)**2 + (pz-iz)**2)
                    if dist_to_intent > 3:
                        self._nav_target_attempts[nav_key] = self._nav_target_attempts.get(nav_key, 0) + 1
                        if self._nav_target_attempts[nav_key] >= 5:
                            logger.warning(f"FAST-PATH: blacklisting nav target ({ix},{iy},{iz}) after {self._nav_target_attempts[nav_key]} failed attempts")
                            self._blacklisted_nav_targets.add(nav_key)
                            if len(self._blacklisted_nav_targets) > 20:
                                self._blacklisted_nav_targets = set(list(self._blacklisted_nav_targets)[-10:])
                        else:
                            logger.debug(f"FAST-PATH: MOVE=intent -> ({ix},{iy},{iz}) dist={dist_to_intent:.0f} attempt={self._nav_target_attempts[nav_key]}")
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
            wood_available = [b for b in wood_nearby if pos_key(b) not in self._recently_mined]
            if wood_available:
                # Prefer reachable logs (at/below foot level) to avoid canopy climbing
                reachable_wood = [b for b in wood_available if b.get("y", 0) <= py + 2]
                candidates = reachable_wood or wood_available
                t = min(candidates, key=lambda b: (
                    b.get("y", 0),  # lowest first
                    abs(b.get("x",0)-px) + abs(b.get("z",0)-pz),
                ))
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
            
            # Adaptive leash: an empty-handed gatherer may range far enough to REACH
            # the nearest resource (trees can be 70+ blocks away on treeless plains),
            # but once it carries cargo it returns home to deposit. This resolves the
            # catch-22 where a tight 60-block leash pulled agents back before they ever
            # got within perception range (12 blocks) of the only forest.
            _CARGO_KEYS = ("log", "planks", "ore", "ingot", "cobblestone", "coal",
                           "raw_", "diamond", "stone", "stick", "torch")
            carrying_cargo = sum(
                cnt for k, cnt in perception.inventory.items()
                if any(c in k for c in _CARGO_KEYS)
            ) >= 4
            leash = 60 if carrying_cargo else 110

            # Priority 3.5: Round-trip — return to center if beyond the (adaptive) leash
            if self.settlement and self.settlement.center:
                cx, cy, cz = self.settlement.center
                dist_to_center = math.sqrt((px - cx)**2 + (pz - cz)**2)
                if dist_to_center > leash:
                    logger.debug(f"FAST-PATH: RETURN-TO-CENTER dist={dist_to_center:.0f}m (>{leash}, cargo={carrying_cargo})")
                    self._nav_target = (cx, cy, cz)
                    self._nav_start = time.time()
                    return AgentAction(agent_name="", action="move_to",
                                       params={"x": cx, "y": cy, "z": cz})

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
            # Clamp explore target to the adaptive leash (110 while gathering, 60 with cargo)
            if self.settlement and self.settlement.center:
                cx, _, cz = self.settlement.center
                _clamp = leash
                _inner = max(_clamp - 5, 5)
                _target_dist = math.sqrt((tx - cx)**2 + (tz - cz)**2)
                if _target_dist > _clamp:
                    _scale = _inner / max(_target_dist, 1)
                    tx = int(cx + (tx - cx) * _scale)
                    tz = int(cz + (tz - cz) * _scale)
                    logger.debug(f"FAST-PATH: clamped explore target to ({tx},{ty},{tz}), dist_from_center={_inner}m (leash={_clamp})")
                    # If clamped target is essentially where we already are, pick a new heading
                    if abs(tx - px) <= 3 and abs(tz - pz) <= 3:
                        # Rotate heading 90° instead of standing still
                        self._explore_heading = (dz, -dx) if random.random() < 0.5 else (-dz, dx)
                        ndx, ndz = self._explore_heading
                        tx = int(px + ndx * 20)
                        tz = int(pz + ndz * 20)
                        # Re-clamp after rotation
                        _td2 = math.sqrt((tx - cx)**2 + (tz - cz)**2)
                        if _td2 > _clamp:
                            _s2 = _inner / max(_td2, 1)
                            tx = int(cx + (tx - cx) * _s2)
                            tz = int(cz + (tz - cz) * _s2)
                        logger.debug(f"FAST-PATH: rotated heading to ({ndx},{ndz}) to avoid deadlock, new target ({tx},{ty},{tz})")
            logger.debug(f"FAST-PATH: MOVE=explore -> ({tx},{ty},{tz}) heading=({self._explore_heading[0]},{self._explore_heading[1]}) step={self._explore_steps} has_wood={has_wood}")
            self._nav_target = (tx, ty, tz)
            self._nav_start = time.time()
            return AgentAction(agent_name="", action="move_to",
                               params={"x": tx, "y": ty, "z": tz})

        if hint == "give":
            # Parse "give cobblestone to BuilderAlice" from intent
            give_match = re.search(r'give\s+(\w+)\s+to\s+(\w+)', intent, re.IGNORECASE)
            if give_match:
                item = give_match.group(1).lower()
                target = give_match.group(2)
                inv = perception.inventory
                amount = min(inv.get(item, 0), 10)
                if amount > 0:
                    # Check if target agent is nearby
                    nearby_agent = None
                    for e in perception.nearby_entities:
                        if e.get("name", "") == target and e.get("distance", 999) < 5:
                            nearby_agent = e
                            break
                    if nearby_agent:
                        return AgentAction(agent_name="", action="give_item",
                            params={"target": target, "item": item, "amount": amount})
                    elif any(e.get("name","") == target for e in perception.nearby_entities):
                        # Target exists but too far — move closer
                        for e in perception.nearby_entities:
                            if e.get("name","") == target:
                                return AgentAction(agent_name="", action="move_to",
                                    params={"x": int(e.get("x",px)), "y": int(e.get("y",py)),
                                            "z": int(e.get("z",pz))})

        if hint == "deposit":
            deposit_match = re.search(r'deposit\s+(\w+)\s*(\d+)?', intent, re.IGNORECASE)
            if deposit_match:
                item = deposit_match.group(1).lower()
                amount = int(deposit_match.group(2)) if deposit_match.group(2) else 10
                inv = perception.inventory
                actual = min(inv.get(item, 0), amount)
                if actual > 0:
                    if self.settlement and self.settlement.center:
                        cx, cy, cz = self.settlement.center
                        if abs(px - cx) < 6 and abs(pz - cz) < 6 and abs(py - cy) < 10:
                            return AgentAction(agent_name="", action="deposit_chest",
                                params={"item": item, "amount": actual})
                        else:
                            return AgentAction(agent_name="", action="move_to",
                                params={"x": cx, "y": cy, "z": cz})

        if hint == "smelt":
            smelt_match = re.search(r'smelt\s+(\w+)\s*(\d+)?', intent, re.IGNORECASE)
            if smelt_match:
                item = smelt_match.group(1).lower()
                amount = int(smelt_match.group(2)) if smelt_match.group(2) else 8
                if perception.inventory.get(item, 0) > 0:
                    # Furnace access? (own furnace, or a furnace block nearby)
                    furnace_near = (perception.inventory.get("furnace", 0) >= 1
                        or any(b.get("type","").upper() in ("FURNACE","BLAST_FURNACE")
                               and abs(b.get("x",0)-px) <= 4 and abs(b.get("z",0)-pz) <= 4
                               for b in blocks))
                    if furnace_near:
                        return AgentAction(agent_name="", action="smelt",
                                           params={"item": item, "amount": amount})
                    # Otherwise walk to the shared village furnace (the workshop)
                    if self.settlement and self.settlement.furnace_pos:
                        fx, fy, fz = self.settlement.furnace_pos
                        if abs(fx - px) <= 3 and abs(fz - pz) <= 3:
                            return AgentAction(agent_name="", action="smelt",
                                               params={"item": item, "amount": amount})
                        logger.debug(f"FAST-PATH: SMELT-NAV to village furnace ({fx},{fy},{fz})")
                        return AgentAction(agent_name="", action="move_to",
                                           params={"x": fx, "y": fy, "z": fz})

        if hint == "till":
            # Till the coordinates from intent, or the nearest dirt/grass at feet level
            if intent_coords:
                ix, iy, iz = int(intent_coords[0][0]), int(intent_coords[0][1]), int(intent_coords[0][2])
                if abs(ix - px) <= 4 and abs(iz - pz) <= 4:
                    return AgentAction(agent_name="", action="till",
                                       params={"x": ix, "y": iy, "z": iz})
                else:
                    return AgentAction(agent_name="", action="move_to",
                                       params={"x": ix, "y": iy, "z": iz})
            tillable = [b for b in blocks
                        if b.get("type","").upper() in ("DIRT","GRASS_BLOCK")
                        and abs(b.get("x",0)-px) <= 4 and abs(b.get("z",0)-pz) <= 4
                        and abs(b.get("y",0)-py) <= 1]
            if tillable:
                t = min(tillable, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("z",0)-pz))
                return AgentAction(agent_name="", action="till",
                                   params={"x": int(t.get("x",px)), "y": int(t.get("y",py)),
                                           "z": int(t.get("z",pz))})

        if hint == "dig_shaft":
            # Parse target Y from intent ("dig_shaft to y=30", "dig to y=-40")
            has_pickaxe = any("pickaxe" in k.lower() for k in perception.inventory)
            ty_match = re.search(r'y\s*=?\s*(-?\d+)', intent, re.IGNORECASE)
            target_y = int(ty_match.group(1)) if ty_match else 30
            if has_pickaxe and target_y < py:
                return AgentAction(agent_name="", action="dig_shaft",
                                   params={"target_y": target_y})

        if hint == "withdraw":
            withdraw_match = re.search(r'withdraw\s+(\w+)\s*(\d+)?', intent, re.IGNORECASE)
            if withdraw_match:
                item = withdraw_match.group(1).lower()
                amount = int(withdraw_match.group(2)) if withdraw_match.group(2) else 10
                if self.settlement and self.settlement.center:
                    cx, cy, cz = self.settlement.center
                    if abs(px - cx) < 6 and abs(pz - cz) < 6 and abs(py - cy) < 10:
                        return AgentAction(agent_name="", action="withdraw_chest",
                            params={"item": item, "amount": amount})
                    else:
                        return AgentAction(agent_name="", action="move_to",
                            params={"x": cx, "y": cy, "z": cz})

        if hint in ("rest", "idle"):
            return AgentAction(agent_name="", action="idle")

        return None

    def receive_perception(self, perception: PerceptionData):
        """Called when new perception data arrives from Minecraft."""
        # Accumulate chat into a separate inbox that survives perception overwrites
        if perception.nearby_chat:
            self._chat_inbox.extend(perception.nearby_chat)
            perception.nearby_chat = []
        # Sync village chest contents from perception into settlement
        if self.settlement and perception.village_chest:
            self.settlement.update_chest(perception.village_chest)
        # Sync settlement center to the chest's REAL surface position. The chest is
        # snapped to ground level by the plugin; a stale hardcoded y=64 center would
        # send agents to a buried point they can never reach to deposit.
        if self.settlement and perception.village_chest_pos:
            cp = perception.village_chest_pos
            real = (int(cp.get("x", 0)), int(cp.get("y", 64)), int(cp.get("z", 0)))
            if self.settlement.center != real:
                self.settlement.center = real
        # Sync shared workshop positions (furnace + crafting table)
        if self.settlement and perception.village_furnace_pos:
            fp = perception.village_furnace_pos
            self.settlement.furnace_pos = (int(fp.get("x", 0)), int(fp.get("y", 0)), int(fp.get("z", 0)))
        if self.settlement and perception.village_table_pos:
            tp = perception.village_table_pos
            table = (int(tp.get("x", 0)), int(tp.get("y", 0)), int(tp.get("z", 0)))
            self.settlement.furnace_table_pos = table
            if table not in self.settlement.crafting_tables:
                self.settlement.crafting_tables.append(table)
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

        # Build-queue failure handling: abort on material shortage or repeated failures.
        if not result.success and result.action == "place_block" and self._build_queue:
            details = (result.details or "").lower()
            out_of_material = "missing" in details or "don't have" in details or "no " in details
            if out_of_material:
                dropped = len(self._build_queue)
                self._build_queue.clear()
                self._pending_shelter_pos = None
                logger.warning(f"BUILD-QUEUE: out of material, cleared {dropped} blocks: {result.details}")
            else:
                self._build_fail_streak = getattr(self, '_build_fail_streak', 0) + 1
                if self._build_fail_streak >= 3:
                    dropped = len(self._build_queue)
                    self._build_queue.clear()
                    self._pending_shelter_pos = None
                    logger.warning(f"BUILD-QUEUE: {self._build_fail_streak} consecutive failures, aborting build ({dropped} blocks left)")
                    self._build_fail_streak = 0
                else:
                    logger.debug(f"BUILD-QUEUE: skipping blocked cell ({len(self._build_queue)} left): {result.details}")
        elif result.success and result.action == "place_block":
            self._build_fail_streak = 0
            self._build_blocks_placed = getattr(self, '_build_blocks_placed', 0) + 1

        # Register shelter only after build queue completes with blocks actually placed
        if result.action in ("place_block", "move_to") and not self._build_queue:
            pending = getattr(self, '_pending_shelter_pos', None)
            placed = getattr(self, '_build_blocks_placed', 0)
            if pending and placed >= 4:
                if self.settlement:
                    self.settlement.register_shelter(*pending)
                logger.info(f"BUILD-COMPLETE: shelter registered at {pending} ({placed} blocks placed)")
            elif pending and placed < 4:
                logger.info(f"BUILD-INCOMPLETE: skipping shelter registration at {pending} (only {placed} blocks placed)")
            self._pending_shelter_pos = None
            self._build_blocks_placed = 0

        # Track repeated failures to avoid retrying the same broken action
        if not result.success:
            # Chest and smelt failures are TRANSIENT (chest contents and distance
            # change constantly) — a session blacklist would permanently disable
            # the village economy over a temporary condition. Feed the fact into
            # memory instead and let the LLM adapt.
            if result.action in ("deposit_chest", "withdraw_chest", "smelt"):
                await self.memory.add_event(
                    content=f"[{result.action} failed] {result.details}",
                    importance=0.4,
                    subject=self.name, predicate="failed", object=result.action,
                )
                self._cached_decision = None  # re-decide with fresh context
                logger.debug(f"CHEST-SOFT-FAIL: {result.action}: {result.details} (no blacklist)")
                self._action_result_ready = True
                self._perception_event.set()
                logger.info(f"Agent {self.name} action result: {result.action} -> FAIL: {result.details}")
                return
            fail_key = f"{result.action}:{result.details.split(' ')[-1] if result.details else 'unknown'}"
            # For place_block failures, track the material (e.g. "place:stick")
            if "not a placeable block" in (result.details or ""):
                import re
                mat_match = re.search(r'(\w+) is not a placeable', result.details)
                if mat_match:
                    fail_key = f"place:{mat_match.group(1)}"
            # For mine_block failures on AIR, blacklist the specific coordinates
            elif "cannot mine" in (result.details or "") and result.action == "mine_block":
                last = getattr(self, '_last_mine_coords', None)
                if last:
                    fail_key = f"mine:{last}"
                    if not hasattr(self, '_blacklisted_mine_positions'):
                        self._blacklisted_mine_positions: set[str] = set()
                    self._blacklisted_mine_positions.add(last)
                    logger.debug(f"MINE-BLACKLIST: position {last} blocked (now {len(self._blacklisted_mine_positions)} blacklisted)")
                else:
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

        Uses a fallback pattern (3x3x3 hollow shelter) when no LLM blueprint
        is available. The LLM blueprint system (_generate_blueprint) is called
        asynchronously when enough materials exist — see _try_start_build.
        """
        params = parsed.params
        material = str(params.get("material", "dirt")).lower()
        cx = int(params.get("x", 0))
        cy = int(params.get("y", 0))
        cz = int(params.get("z", 0))
        build_type = str(params.get("type", "shelter")).lower()

        # Check if we have an LLM-generated blueprint ready
        blueprint = getattr(self, '_pending_blueprint', None)
        if blueprint:
            self._pending_blueprint = None
            queue = self._blueprint_to_queue(blueprint, cx, cy, cz, perception)
            if queue:
                logger.info(f"BUILD: LLM blueprint '{build_type}' at ({cx},{cy},{cz}) → {len(queue)} blocks")
                return queue

        # --- Fallback: simple 3x3x3 shelter ---
        BUILD_MATERIALS = ("cobblestone", "oak_planks", "birch_planks", "spruce_planks",
                           "jungle_planks", "acacia_planks", "dark_oak_planks", "oak_log",
                           "birch_log", "spruce_log", "jungle_log", "cobbled_deepslate",
                           "stone", "dirt")
        pool: list[str] = []
        if perception:
            inv = perception.inventory
            for m in BUILD_MATERIALS:
                pool.extend([m] * inv.get(m, 0))
        pool.sort(key=lambda m: 0 if m == material else 1)
        if not pool:
            pool = [material]

        _REPLACEABLE_BUILD = frozenset({"AIR","CAVE_AIR","VOID_AIR","SHORT_GRASS","TALL_GRASS",
                                        "FERN","LARGE_FERN","DEAD_BUSH","SNOW","VINE","LEAF_LITTER",
                                        "GRASS_BLOCK","DIRT","GRAVEL","WATER",
                                        "OAK_LEAVES","BIRCH_LEAVES","SPRUCE_LEAVES","JUNGLE_LEAVES",
                                        "ACACIA_LEAVES","DARK_OAK_LEAVES","AZALEA_LEAVES",
                                        "FLOWERING_AZALEA_LEAVES","CHERRY_LEAVES","MANGROVE_LEAVES",
                                        "PALE_OAK_LEAVES"})
        blocked_positions: set[tuple[int, int, int]] = set()
        if perception:
            for b in perception.nearby_blocks:
                btype = b.get("type", "").upper()
                if btype not in _REPLACEABLE_BUILD:
                    blocked_positions.add((b.get("x", 0), b.get("y", 0), b.get("z", 0)))

        cells: list[tuple[int, int, int]] = []
        if build_type in ("shelter", "hut", "house", "wall"):
            for dy in range(3):
                for dx in [-1, 0, 1]:
                    for dz in [-1, 0, 1]:
                        if dy < 2:
                            if dx == 0 and dz == 0:
                                continue
                            if dx == 0 and dz == 1:
                                continue
                        pos = (cx + dx, cy + dy, cz + dz)
                        if pos in blocked_positions:
                            continue
                        cells.append(pos)

        queue: list[AgentAction] = []
        for i, (bx, by, bz) in enumerate(cells):
            if i >= len(pool):
                break
            queue.append(AgentAction(
                agent_name="",
                action="place_block",
                params={"block_type": pool[i], "x": bx, "y": by, "z": bz},
            ))

        logger.info(f"BUILD: fallback '{build_type}' at ({cx},{cy},{cz}) into {len(queue)} "
                    f"place_block actions ({len(cells)} cells, {len(pool)} materials available)")
        return queue

    async def _generate_blueprint(self, perception: PerceptionData) -> list[dict] | None:
        """Ask the LLM to design a building. Returns a list of block placements.

        PIANO-compliant: the LLM decides what to build, not hardcoded patterns.
        The agent provides its inventory and location as neutral facts.
        """
        import json, re
        inv = perception.inventory
        pos = perception.position
        px, py, pz = int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("z", 0))

        BUILD_MATERIALS = ("cobblestone", "oak_planks", "birch_planks", "spruce_planks",
                           "jungle_planks", "acacia_planks", "dark_oak_planks", "oak_log",
                           "birch_log", "spruce_log", "jungle_log", "cobbled_deepslate",
                           "stone", "dirt", "oak_fence", "oak_stairs", "oak_slab",
                           "cobblestone_stairs", "cobblestone_slab", "glass", "torch",
                           "oak_door", "birch_door", "spruce_door")
        available = {m: inv.get(m, 0) for m in BUILD_MATERIALS if inv.get(m, 0) > 0}
        total = sum(available.values())
        if total < 8:
            return None

        mat_str = ", ".join(f"{v}x {k}" for k, v in
                           sorted(available.items(), key=lambda x: -x[1]))

        biome = perception.biome
        council_task = getattr(self, '_council_assignment', "")
        personality = getattr(self, 'personality', 'builder')

        prompt = f"""You are a Minecraft architect. Design a small structure that can be built with the available materials.

LOCATION: ({px}, {py}, {pz}), biome: {biome}
AVAILABLE MATERIALS: {mat_str} (total: {total} blocks)
BUILDER PERSONALITY: {personality}
{f'TASK: {council_task}' if council_task else ''}

RULES:
- Use ONLY materials listed above — do not exceed available quantities.
- All coordinates are RELATIVE to the build origin (0,0,0 = ground level center).
- Y=0 is ground level, Y=1 is one block up, etc.
- Leave a doorway (don't fully enclose — agents need to exit).
- Build bottom-up (lowest Y first) so blocks have support.
- Max size: 7x7 footprint, 5 blocks tall. Stay practical for the material count.
- Prefer cobblestone for foundations, planks for walls, slabs/stairs for decoration.
- Be creative but realistic — match the biome and personality.

Output ONLY a JSON array of block placements, sorted bottom-up:
[{{"block": "material_name", "x": 0, "y": 0, "z": 0}}, ...]"""

        try:
            response = await self.llm.chat([
                {"role": "system", "content": "You are a Minecraft architect. Output only a JSON array."},
                {"role": "user", "content": prompt},
            ])
            json_str = response.strip()
            json_str = re.sub(r'^```(?:json)?\s*', '', json_str)
            json_str = re.sub(r'\s*```$', '', json_str)
            bracket_start = json_str.find('[')
            bracket_end = json_str.rfind(']')
            if bracket_start >= 0 and bracket_end > bracket_start:
                json_str = json_str[bracket_start:bracket_end + 1]
            blueprint = json.loads(json_str)
            if not isinstance(blueprint, list) or len(blueprint) < 3:
                logger.warning(f"BLUEPRINT: LLM returned too few blocks ({len(blueprint) if isinstance(blueprint, list) else 'not a list'})")
                return None
            # Validate: only use materials we actually have
            mat_budget = dict(available)
            validated = []
            for entry in blueprint:
                block = entry.get("block", "").lower()
                if block in mat_budget and mat_budget[block] > 0:
                    mat_budget[block] -= 1
                    validated.append(entry)
                else:
                    # Substitute with most-available material
                    sub = max(mat_budget, key=mat_budget.get) if mat_budget else None
                    if sub and mat_budget[sub] > 0:
                        mat_budget[sub] -= 1
                        entry["block"] = sub
                        validated.append(entry)
            logger.info(f"BLUEPRINT: LLM designed {len(validated)} blocks "
                       f"({len(blueprint)} requested, {total} available)")
            return validated
        except Exception as e:
            logger.warning(f"BLUEPRINT: LLM call failed: {e}")
            return None

    def _blueprint_to_queue(self, blueprint: list[dict], cx: int, cy: int, cz: int,
                            perception: PerceptionData | None) -> list[AgentAction]:
        """Convert an LLM blueprint (relative coords) to absolute place_block actions."""
        _REPLACEABLE_BUILD = frozenset({"AIR","CAVE_AIR","VOID_AIR","SHORT_GRASS","TALL_GRASS",
                                        "FERN","LARGE_FERN","DEAD_BUSH","SNOW","VINE","LEAF_LITTER",
                                        "GRASS_BLOCK","DIRT","GRAVEL","WATER",
                                        "OAK_LEAVES","BIRCH_LEAVES","SPRUCE_LEAVES","JUNGLE_LEAVES",
                                        "ACACIA_LEAVES","DARK_OAK_LEAVES","AZALEA_LEAVES",
                                        "FLOWERING_AZALEA_LEAVES","CHERRY_LEAVES","MANGROVE_LEAVES",
                                        "PALE_OAK_LEAVES"})
        blocked: set[tuple[int, int, int]] = set()
        if perception:
            for b in perception.nearby_blocks:
                btype = b.get("type", "").upper()
                if btype not in _REPLACEABLE_BUILD:
                    blocked.add((b.get("x", 0), b.get("y", 0), b.get("z", 0)))

        queue: list[AgentAction] = []
        for entry in blueprint:
            bx = cx + int(entry.get("x", 0))
            by = cy + int(entry.get("y", 0))
            bz = cz + int(entry.get("z", 0))
            if (bx, by, bz) in blocked:
                continue
            block = entry.get("block", "dirt").lower()
            queue.append(AgentAction(
                agent_name="",
                action="place_block",
                params={"block_type": block, "x": bx, "y": by, "z": bz},
            ))
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
        # Count the TOTAL building stock across all types — the shelter draws on all of them,
        # so a few planks + a few logs together is enough even if no single type is.
        total_mat = sum(inv.get(m, 0) for m in build_mats)
        material = max(build_mats, key=lambda m: inv.get(m, 0))
        if total_mat < 12:
            if getattr(self, '_last_build_mat_log', 0) != total_mat:
                self._last_build_mat_log = total_mat
                logger.debug(f"FAST-BUILD: not enough material (total={total_mat}, best={material}:{inv.get(material,0)}), deferring")
            return None
        pos = perception.position
        cx, cy, cz = int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("z", 0))

        # Location-specific cooldown: don't rebuild within 10 blocks of a recent build site
        import math as _m
        build_sites = getattr(self, '_recent_build_sites', {})
        for (bx, by, bz), ts in build_sites.items():
            if time.time() - ts < 300 and _m.sqrt((cx-bx)**2 + (cy-by)**2 + (cz-bz)**2) < 10:
                return None

        # Don't build near water — any water/beach block within 4 blocks horizontally
        water_types = frozenset({"WATER", "SAND", "SEAGRASS", "TALL_SEAGRASS", "KELP", "KELP_PLANT",
                                 "BUBBLE_COLUMN"})
        water_nearby = sum(1 for b in perception.nearby_blocks
                          if b.get("type", "").upper() in water_types
                          and abs(b.get("x", 0) - cx) <= 4 and abs(b.get("z", 0) - cz) <= 4
                          and abs(b.get("y", 0) - cy) <= 2)
        if water_nearby >= 1:
            logger.debug(f"FAST-BUILD: water/beach nearby ({water_nearby} blocks), skipping build")
            return None

        # If we have a pending LLM blueprint, use it
        blueprint = getattr(self, '_pending_blueprint', None)
        if blueprint:
            self._pending_blueprint = None
            queue = self._blueprint_to_queue(blueprint, cx, cy, cz, perception)
            if queue:
                self._build_queue = queue
                self._build_queue.append(AgentAction(
                    agent_name="", action="move_to",
                    params={"x": cx, "y": cy, "z": cz + 4}))
                if not hasattr(self, '_recent_build_sites'):
                    self._recent_build_sites = {}
                self._recent_build_sites[(cx, cy, cz)] = time.time()
                self._pending_shelter_pos = (cx, cy, cz)
                logger.info(f"FAST-BUILD: LLM blueprint ({len(queue)} blocks) at ({cx},{cy},{cz})")
                return self._build_queue.pop(0)

        # Request a blueprint from the LLM (will be used on the NEXT build attempt)
        if not getattr(self, '_blueprint_requested', False) and total_mat >= 15:
            self._blueprint_requested = True
            import asyncio
            async def _fetch():
                try:
                    bp = await self._generate_blueprint(perception)
                    if bp:
                        self._pending_blueprint = bp
                        self._cached_decision = None  # trigger fresh build attempt
                        logger.info(f"BLUEPRINT: ready — {len(bp)} blocks designed")
                except Exception as e:
                    logger.warning(f"BLUEPRINT: generation failed: {e}")
                finally:
                    self._blueprint_requested = False
            asyncio.create_task(_fetch())
            logger.info(f"BLUEPRINT: requesting LLM design ({total_mat} materials available)")
            return None  # wait for blueprint to arrive

        # Fallback: use hardcoded 3x3 shelter
        build_params = AgentAction(agent_name="", action="build",
                                   params={"type": "shelter", "material": material,
                                           "x": cx, "y": cy, "z": cz})
        self._build_queue = self._expand_build(build_params, perception)
        if not self._build_queue:
            return None
        self._build_queue.append(AgentAction(
            agent_name="", action="move_to",
            params={"x": cx, "y": cy, "z": cz + 3}))
        if not hasattr(self, '_recent_build_sites'):
            self._recent_build_sites = {}
        self._recent_build_sites[(cx, cy, cz)] = time.time()
        self._pending_shelter_pos = (cx, cy, cz)
        logger.info(f"FAST-BUILD: fallback shelter ({total_mat} total material) at ({cx},{cy},{cz})")
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
        # Tick entry logged only at DEBUG-FINE level (omitted to reduce log noise)

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
        # After 10 heuristic actions in a row, force a full LLM tick. The heuristics
        # (crafting, building, mining, canopy descent) are robust enough that longer
        # fast-tick runs are safe and meaningfully raise actions-per-minute.
        if is_fast_tick:
            self._consecutive_fast_ticks = getattr(self, '_consecutive_fast_ticks', 0) + 1
            if self._consecutive_fast_ticks >= 10:
                is_fast_tick = False
                self._consecutive_fast_ticks = 0
                logger.debug("FAST-TICK-CAP: forcing full LLM tick after 10 fast-ticks")
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
                    # Blacklist the unreachable target and force a fresh LLM decision
                    stall_target = lmi['target']
                    stall_key = f"{int(stall_target[0])},{int(stall_target[1])},{int(stall_target[2])}"
                    self._blacklisted_nav_targets.add(stall_key)
                    self._cached_decision = None  # force fresh LLM call
                    logger.warning(f"NAV-STALL-ESCAPE: teleporting from ({cpx:.0f},{cpy:.0f},{cpz:.0f}) to ({tp_x},{safe_y},{tp_z}), blacklisted {stall_key}")
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
            _ctrl_cached = False
            if is_fast_tick and self._cached_decision and ticks_since_ctrl < 15:
                decision = self._cached_decision
                _ctrl_cached = True
            elif use_cache:
                decision = self._cached_decision
                _ctrl_cached = True
            else:
                decision = await self.controller.decide(self)
                self._cached_decision = decision
                self._cached_decision_tick = self.tick_count
                if not self._running:
                    return

            # --- DEBUG: Controller output (compact; details only on fresh decisions) ---
            import re as _re
            _intent_str = str(decision.intent) if not isinstance(decision.intent, str) else decision.intent
            _reason_str = str(decision.reason) if not isinstance(decision.reason, str) else decision.reason
            _ic = _re.findall(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', _intent_str)
            if _ctrl_cached:
                logger.debug(f"CTRL: hint={decision.action_hint} p={decision.priority:.2f} cached age={ticks_since_ctrl}t")
            else:
                nb = perception.nearby_biomes
                _nb_str = dict(nb) if nb else 'NONE'
                _nb_changed = _nb_str != getattr(self, '_last_logged_biomes', None)
                if _nb_changed:
                    self._last_logged_biomes = _nb_str
                wood_count = sum(1 for b in perception.nearby_blocks
                    if b.get("type","").upper() in
                    ("OAK_LOG","BIRCH_LOG","SPRUCE_LOG","JUNGLE_LOG","ACACIA_LOG",
                     "DARK_OAK_LOG","CHERRY_LOG","MANGROVE_LOG",
                     "OAK_LEAVES","BIRCH_LEAVES","SPRUCE_LEAVES","JUNGLE_LEAVES",
                     "ACACIA_LEAVES","DARK_OAK_LEAVES"))
                logger.debug(
                    f"CTRL: hint={decision.action_hint} p={decision.priority:.2f} "
                    f"intent='{_intent_str[:120]}' coords={_ic if _ic else 'NONE'} "
                    f"wood={wood_count}/{len(perception.nearby_blocks)}"
                    + (f" biomes={_nb_str}" if _nb_changed else "")
                )
                if _reason_str:
                    logger.debug(f"CTRL-WHY: {_reason_str[:150]}")

            # Night/distance info is passed to the controller prompt via
            # Settlement.get_context_for_prompt() — the LLM decides whether
            # to return home, not hard-coded logic.

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

            # --- Single decision path: the controller already produced intent +
            # coordinates + hint, so map that straight to an action here. This runs
            # on EVERY tick now (was gated behind fast-path conditions), which lets
            # us skip the redundant second planner LLM call whenever the heuristic
            # can resolve the controller's intent — the planner becomes a genuine
            # fallback for ambiguity only. Roughly halves per-decision LLM cost.
            if parsed is None:
                parsed = self._decision_to_action(decision, perception)
                if parsed:
                    logger.debug(f"DECISION-PATH: hint={decision.action_hint} p={decision.priority:.2f} fast_tick={is_fast_tick} → {parsed.action}")

            if parsed is None and not is_fast_tick:
                # Fallback: the heuristic couldn't resolve the intent (genuine
                # ambiguity) — ask the planner LLM. Skipped on fast-ticks.
                logger.debug(f"LLM-PATH: calling planner.decide_action() (priority={decision.priority:.2f}, hint={decision.action_hint})")
                action_str = await self.planner.decide_action(self)
                logger.debug(f"LLM-PATH: returned '{action_str[:200]}'")
                if not self._running:
                    return
                parsed = self.executor.parse_action(action_str)

                # Block mine_block on recently-mined positions (LLM uses stale coords)
                if parsed and parsed.action == "mine_block":
                    mk = f"{int(parsed.params.get('x',0))},{int(parsed.params.get('y',0))},{int(parsed.params.get('z',0))}"
                    if hasattr(self, '_recently_mined') and mk in self._recently_mined:
                        logger.debug(f"LLM-PATH: blocking mine_block at recently-mined {mk}")
                        parsed = None

                # Handle build() action — LLM returns a multi-block placement plan
                if parsed and parsed.action == "build":
                    self._build_queue = self._expand_build(parsed, perception)
                    if self._build_queue:
                        bx = int(parsed.params.get("x", 0))
                        by = int(parsed.params.get("y", 0))
                        bz = int(parsed.params.get("z", 0))
                        self._build_queue.append(AgentAction(
                            agent_name="", action="move_to",
                            params={"x": bx, "y": by, "z": bz + 3}))
                        self._pending_shelter_pos = (bx, by, bz)
                        parsed = self._build_queue.pop(0)
                        logger.debug(f"BUILD-QUEUE: expanded build into {len(self._build_queue)} blocks + exit-move, starting first")
                    else:
                        parsed = None

                # Block excessive crafting_table crafting from LLM
                if parsed and parsed.action == "craft" and parsed.params.get("item") == "crafting_table":
                    if self._tables_crafted_total >= 2:
                        logger.debug(f"LLM-PATH: blocking crafting_table (already crafted {self._tables_crafted_total})")
                        parsed = None
                    else:
                        self._tables_crafted_total += 1

                # Block LLM move_to to blacklisted nav targets
                if parsed and parsed.action == "move_to":
                    mk = f"{int(parsed.params.get('x',0))},{int(parsed.params.get('y',0))},{int(parsed.params.get('z',0))}"
                    if mk in self._blacklisted_nav_targets:
                        logger.debug(f"LLM-PATH: blocking move_to blacklisted target {mk}")
                        parsed = None

                # Block backwards-pendling: if the LLM sends us to coords we already
                # visited recently (within last 3 positions), skip and let fast-path explore
                if parsed and parsed.action == "move_to" and perception:
                    tx = int(parsed.params.get('x', 0))
                    tz = int(parsed.params.get('z', 0))
                    _ppx = int(perception.position.get('x', 0))
                    _ppz = int(perception.position.get('z', 0))
                    if not hasattr(self, '_recent_move_targets'):
                        self._recent_move_targets: list[tuple[int, int]] = []
                    # Check if target is within 10 blocks of a recently visited position
                    for rx, rz in self._recent_move_targets[-5:]:
                        if abs(tx - rx) <= 10 and abs(tz - rz) <= 10 and abs(tx - _ppx) + abs(tz - _ppz) > 15:
                            logger.debug(f"LLM-PATH: blocking backwards move to ({tx},_,{tz}) — recently visited ({rx},_,{rz})")
                            parsed = None
                            break
                    if parsed and parsed.action == "move_to":
                        self._recent_move_targets.append((_ppx, _ppz))
                        if len(self._recent_move_targets) > 10:
                            self._recent_move_targets = self._recent_move_targets[-10:]

                # If LLM returned unparseable, fall back to fast-path heuristics
                if parsed is None or parsed.action == "idle":
                    logger.debug(f"LLM-PATH: unparseable/idle, trying fast-path fallback")
                    parsed = self._decision_to_action(decision, perception)

            # Fast-tick with nothing to do — explore only if no nav is already running
            if parsed is None and is_fast_tick:
                if hasattr(self, '_nav_target') and self._nav_target:
                    pass  # nav in progress, just wait
                else:
                    # Rate-limit explores: max 1 every 10 seconds to avoid spamming moves
                    import time as _ft_time
                    _last_explore = getattr(self, '_last_explore_time', 0)
                    if _ft_time.time() - _last_explore < 10:
                        pass  # too soon, wait for next LLM tick
                    else:
                        self._last_explore_time = _ft_time.time()
                        import random as _ft_rnd
                        _dx, _dz = _ft_rnd.choice([(1,0),(-1,0),(0,1),(0,-1)])
                        _dist = _ft_rnd.randint(20, 40)
                        _fpx = int(perception.position.get("x", 0))
                        _fpy = int(perception.position.get("y", 0))
                        _fpz = int(perception.position.get("z", 0))
                        parsed = AgentAction(agent_name="", action="move_to",
                                             params={"x": _fpx + _dx * _dist, "y": _fpy, "z": _fpz + _dz * _dist})
                        logger.debug(f"FAST-TICK-EXPLORE: exploring ({_fpx + _dx * _dist},{_fpy},{_fpz + _dz * _dist})")

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

            # Block mine_block to already-mined (AIR) positions
            if parsed and parsed.action == "mine_block":
                mk = f"{int(parsed.params.get('x',0))},{int(parsed.params.get('y',0))},{int(parsed.params.get('z',0))}"
                self._last_mine_coords = mk
                if hasattr(self, '_blacklisted_mine_positions') and mk in self._blacklisted_mine_positions:
                    logger.debug(f"MINE-BLACKLIST: blocking mine_block at {mk} (already AIR)")
                    parsed = None

            if parsed is None:
                logger.debug(f"Agent {self.name} tick {self.tick_count}: no action (waiting)")
                return

            logger.info(f"Agent {self.name} tick {self.tick_count}: {parsed.action} {parsed.params}")

            # Step 5: Execute action via bridge
            if self.bridge:
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

                # Send chat only on fresh (non-cached) decisions to avoid spam
                if decision.chat_hint and self.bridge and not _ctrl_cached:
                    chat = AgentChat(agent_name=self.name, text=decision.chat_hint)
                    await self.bridge.send_chat(chat)
            elif not parsed:
                logger.warning(f"Agent {self.name}: could not parse action: '{action_str}'")

            # Step 5b: Social awareness — drain chat inbox
            if not is_fast_tick and self._chat_inbox:
                import re as _re_chat
                _chat_msgs = list(self._chat_inbox)
                self._chat_inbox.clear()
                for msg in _chat_msgs:
                    _chat_match = _re_chat.match(r'\[(\w+)\]\s*(.*)', msg)
                    if _chat_match:
                        _sender = _chat_match.group(1)
                        if _sender != self.name:
                            self.social_awareness.update_relationship(
                                target=_sender, sentiment_delta=0.1, trust_delta=0.05)
                            if not hasattr(self, '_unanalyzed_chat'):
                                self._unanalyzed_chat: dict[str, list[str]] = {}
                            self._unanalyzed_chat.setdefault(_sender, []).append(
                                _chat_match.group(2))
                            # Extract requests for Settlement routing
                            if self.settlement:
                                self.settlement.parse_chat_request(
                                    _sender, _chat_match.group(2))
                            # Cultural transmission: value-laden statements become
                            # adoption candidates (LLM decides later). Broadened from
                            # literal "I believe" to any conviction/lesson/should-phrasing,
                            # since agents rarely quote themselves verbatim.
                            _text = _chat_match.group(2)
                            if _re_chat.search(
                                    r"i believe|i've learned|i learned|i've found|"
                                    r"i think|we should|our village should|the village should|"
                                    r"always |never |it'?s best to|the key is|"
                                    r"what matters|i'm convinced|lesson", _text,
                                    _re_chat.IGNORECASE) and len(_text) > 15:
                                if not hasattr(self, '_belief_candidates'):
                                    self._belief_candidates: list[tuple[str, str]] = []
                                self._belief_candidates.append((_sender, _text[:200]))
                logger.debug(f"SOCIAL: {self.name} processed {len(_chat_msgs)} chat message(s)")

                # Cultural transmission: consider adopting a heard conviction.
                # One LLM call, rate-limited to every ~2 minutes per agent.
                import time as _t_cult
                if (getattr(self, '_belief_candidates', None)
                        and _t_cult.time() - getattr(self, '_last_belief_check', 0) > 120):
                    self._last_belief_check = _t_cult.time()
                    _cand_sender, _cand_text = self._belief_candidates.pop(0)
                    self._belief_candidates = self._belief_candidates[-3:]
                    asyncio.create_task(
                        self._consider_belief_adoption(_cand_sender, _cand_text))

                # Deep LLM analysis every 30 ticks if unanalyzed messages exist
                if (self.tick_count % 30 == 0
                        and hasattr(self, '_unanalyzed_chat')
                        and self._unanalyzed_chat):
                    for _sa_sender, _sa_msgs in list(self._unanalyzed_chat.items()):
                        if _sa_msgs:
                            _sa_convo = "\n".join(
                                f"{_sa_sender}: {m}" for m in _sa_msgs[-5:])
                            await self.social_awareness.analyze_interaction(
                                self, _sa_sender, _sa_convo)
                    self._unanalyzed_chat.clear()
                    logger.debug(f"SOCIAL: analyzed chat interactions for {self.name}")

            # Step 5c: Report status to settlement so other agents see it
            if self.settlement and not is_fast_tick and self.tick_count % 5 == 0:
                _pos = perception.position
                self.settlement.update_agent_status(
                    name=self.name,
                    personality=self.personality,
                    intent=decision.intent,
                    inventory=perception.inventory,
                    position=(int(_pos.get("x",0)), int(_pos.get("y",0)), int(_pos.get("z",0))),
                )

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


class Settlement:
    """Shared settlement state across all agents in the village."""

    def __init__(self):
        self.center: tuple[int, int, int] | None = None
        self.shelters_built: int = 0
        self.shelter_positions: list[tuple[int, int, int]] = []
        self.crafting_tables: list[tuple[int, int, int]] = []
        self.agent_status: dict[str, dict] = {}
        self.village_chest: dict[str, int] = {}
        self.chest_placed: bool = False
        self.pending_requests: list[dict] = []  # Chat→Action pipeline
        self.pending_trades: list[dict] = []  # trade offers heard in chat
        self.furnace_pos: tuple[int, int, int] | None = None  # shared village furnace
        self.furnace_table_pos: tuple[int, int, int] | None = None  # workshop table
        self.market_until: float = 0.0  # market day active until this timestamp
        # Village chronicle — persistent history written by the council.
        # Survives restarts; gives the civilization a memory of itself.
        self.chronicle: list[str] = []
        self._chronicle_path = os.path.join("data", "village_chronicle.md")
        self._load_chronicle()
        # Village rules — adopted by majority vote (governance)
        self.rules: list[str] = []
        self._state_path = os.path.join("data", "settlement_state.json")
        self._load_state()

    def _load_state(self):
        """Restore settlement infrastructure so the village survives brain restarts."""
        import json
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.shelter_positions = [tuple(p) for p in data.get("shelter_positions", [])]
            self.shelters_built = len(self.shelter_positions)
            self.crafting_tables = [tuple(p) for p in data.get("crafting_tables", [])]
            self.rules = list(data.get("rules", []))
            if data.get("center"):
                self.center = tuple(data["center"])
            logger.info(f"SETTLEMENT: restored state — {self.shelters_built} shelter(s), "
                        f"{len(self.crafting_tables)} table(s), {len(self.rules)} rule(s)")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"SETTLEMENT: state load failed: {e}")

    def save_state(self):
        """Persist settlement infrastructure to disk."""
        import json
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump({
                    "center": list(self.center) if self.center else None,
                    "shelter_positions": [list(p) for p in self.shelter_positions],
                    "crafting_tables": [list(p) for p in self.crafting_tables],
                    "rules": self.rules,
                }, f, indent=1)
        except Exception as e:
            logger.warning(f"SETTLEMENT: state save failed: {e}")

    def _load_chronicle(self):
        """Load past chronicle entries so history survives brain restarts."""
        try:
            with open(self._chronicle_path, "r", encoding="utf-8") as f:
                self.chronicle = [line.lstrip("- ").strip() for line in f
                                  if line.strip().startswith("-")]
            if self.chronicle:
                logger.info(f"CHRONICLE: loaded {len(self.chronicle)} entries of village history")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"CHRONICLE: load failed: {e}")

    def append_chronicle(self, entry: str):
        """Record one line of village history (written by the council LLM)."""
        entry = " ".join(str(entry).split())[:250]
        if not entry or (self.chronicle and self.chronicle[-1] == entry):
            return
        self.chronicle.append(entry)
        try:
            os.makedirs(os.path.dirname(self._chronicle_path), exist_ok=True)
            with open(self._chronicle_path, "a", encoding="utf-8") as f:
                f.write(f"- {entry}\n")
        except Exception as e:
            logger.warning(f"CHRONICLE: persist failed: {e}")
        logger.info(f"CHRONICLE: {entry}")

    def register_shelter(self, x: int, y: int, z: int) -> bool:
        """Register a shelter. Returns False if a shelter already exists within 8 blocks."""
        import math as _m
        for sx, sy, sz in self.shelter_positions:
            if _m.sqrt((x - sx)**2 + (y - sy)**2 + (z - sz)**2) < 8:
                logger.debug(f"SETTLEMENT: skipping duplicate shelter at ({x},{y},{z}) — too close to ({sx},{sy},{sz})")
                return False
        if self.center is None:
            self.center = (x, y, z)
            logger.info(f"SETTLEMENT: center established at ({x},{y},{z})")
        self.shelter_positions.append((x, y, z))
        self.shelters_built = len(self.shelter_positions)
        logger.info(f"SETTLEMENT: shelter #{self.shelters_built} at ({x},{y},{z})")
        self.save_state()
        return True

    def register_crafting_table(self, x: int, y: int, z: int):
        pos = (x, y, z)
        if pos not in self.crafting_tables:
            self.crafting_tables.append(pos)
            logger.info(f"SETTLEMENT: crafting table registered at ({x},{y},{z})")
            self.save_state()

    def update_agent_status(self, name: str, personality: str, intent: str,
                            inventory: dict[str, int],
                            position: tuple[int, int, int]):
        """Update an agent's shared status so other agents can see it."""
        import time
        surplus = [(k, v) for k, v in sorted(inventory.items(), key=lambda x: -x[1]) if v >= 5][:5]
        self.agent_status[name] = {
            "personality": personality,
            "intent": intent[:60],
            "surplus": surplus,
            "position": position,
            "updated": time.time(),
        }

    def get_context_for_prompt(self, agent_name: str | None = None,
                               agent_pos: tuple[int,int,int] | None = None,
                               is_day: bool | None = None) -> str:
        if self.center is None:
            return ""
        cx, cy, cz = self.center
        lines = [f"SETTLEMENT: center ({cx},{cy},{cz}), {self.shelters_built} shelter(s) built."]
        if self.shelters_built == 0:
            lines.append("No shelters yet. Building near the settlement center benefits the whole village.")
        # The village workshop — shared furnace + table at the commons. Directing
        # agents here is what closes the smelt loop: bring ore, smelt at the shared
        # furnace, no need to carry your own.
        if self.furnace_pos:
            fx, fy, fz = self.furnace_pos
            lines.append(f"VILLAGE WORKSHOP: shared furnace at ({fx},{fy},{fz}) — bring ore + coal here to smelt into ingots. Shared crafting table beside it.")
        elif self.crafting_tables:
            tbl_strs = [f"({x},{y},{z})" for x, y, z in self.crafting_tables[:3]]
            lines.append(f"Shared crafting tables: {', '.join(tbl_strs)}")
        if agent_pos:
            import math as _m
            dist = _m.sqrt((agent_pos[0] - cx)**2 + (agent_pos[2] - cz)**2)
            if dist > 150:
                lines.append(f"Your distance to settlement: {dist:.0f} blocks (very far — other agents cannot see, hear, or trade with you).")
            elif dist > 80:
                lines.append(f"Your distance to settlement: {dist:.0f} blocks (far — cooperation and trading not possible at this range).")
            elif dist > 40:
                lines.append(f"Your distance to settlement: {dist:.0f} blocks (moderate — at edge of cooperation range).")
            else:
                lines.append(f"Your distance to settlement: {dist:.0f} blocks.")
        if is_day is not None:
            lines.append(f"Time of day: {'day' if is_day else 'night (hostile mobs spawn)'}.")

        # Village chest contents
        if self.village_chest:
            chest_items = ", ".join(f"{v}x {k}" for k, v in
                sorted(self.village_chest.items(), key=lambda x: -x[1])[:8])
            lines.append(f"VILLAGE CHEST at center: {chest_items}")
        elif self.chest_placed:
            lines.append("VILLAGE CHEST at center: empty")

        # Food stock — neutral fact so agents/council can judge food security
        _FOOD = ("bread", "cooked_beef", "cooked_porkchop", "cooked_chicken",
                 "cooked_mutton", "cooked_cod", "cooked_salmon", "baked_potato",
                 "apple", "carrot", "wheat")
        food_total = sum(self.village_chest.get(f, 0) for f in _FOOD)
        lines.append(f"VILLAGE FOOD STOCK: {food_total} item(s) in chest."
                     + (" The village has no food reserve." if food_total == 0 else ""))

        # Village rules — laws adopted by majority vote; every villager knows them
        if self.rules:
            lines.append("VILLAGE RULES (adopted by vote): "
                         + " | ".join(self.rules[-4:]))

        # Village history — shared cultural memory, written by the council
        if self.chronicle:
            lines.append("VILLAGE HISTORY: " + " | ".join(self.chronicle[-2:]))

        # Pending requests from other agents
        import time as _time
        active_requests = [r for r in self.pending_requests
                           if r.get("from") != agent_name
                           and _time.time() - r.get("time", 0) < 300]
        if active_requests:
            for req in active_requests[:2]:
                lines.append(f"REQUEST from {req['from']}: needs {req['item']}.")

        # Open trade offers — the LLM decides whether a deal is worth accepting
        self.pending_trades = [t for t in self.pending_trades
                               if _time.time() - t.get("time", 0) < 300]
        offers = [t for t in self.pending_trades if t.get("from") != agent_name]
        for t in offers[:2]:
            lines.append(
                f"TRADE OFFER from {t['from']}: gives {t['gives_amount']}x {t['gives']} "
                f"for {t['wants_amount']}x {t['wants']}. If you have {t['wants']} and want "
                f"the deal, move next to {t['from']} and use action_hint 'give' "
                f"(\"give {t['wants']} to {t['from']}\").")
        # Remind the offerer of their own promise so they reciprocate
        own = [t for t in self.pending_trades if t.get("from") == agent_name]
        for t in own[:1]:
            lines.append(
                f"YOUR OPEN TRADE OFFER: you promised {t['gives_amount']}x {t['gives']} "
                f"for {t['wants_amount']}x {t['wants']}. If someone gives you {t['wants']}, "
                f"give them the promised {t['gives']} in return.")

        others = {n: s for n, s in self.agent_status.items()
                  if n != agent_name and _time.time() - s.get("updated", 0) < 120}
        if others:
            lines.append("VILLAGE MEMBERS STATUS:")
            for n, s in others.items():
                role = s["personality"]
                intent = s["intent"]
                surplus_str = ", ".join(f"{v}x {k}" for k, v in s["surplus"][:3]) if s["surplus"] else "nothing notable"
                ox, oy, oz = s["position"]
                if agent_pos:
                    import math as _m2
                    d = _m2.sqrt((agent_pos[0]-ox)**2 + (agent_pos[2]-oz)**2)
                    lines.append(f"  {n} ({role}, {d:.0f}m away): \"{intent}\" | has: {surplus_str}")
                else:
                    lines.append(f"  {n} ({role}): \"{intent}\" | has: {surplus_str}")

        return "\n".join(lines)

    def parse_chat_request(self, sender: str, message: str):
        """Extract resource requests and trade offers from chat messages."""
        import re, time

        # Trade offers: "trade [my] copper for [your] planks", "offer X for Y",
        # "would you trade X for Y". Stored so nearby agents get the offer as an
        # explicit fact and their LLM can decide to accept via the give action.
        trade_m = re.search(
            r'(?:trade|offer|exchange|swap)\s+(?:my\s+|some\s+)?(\d+)?\s*(\w+)\s+for\s+(?:your\s+|some\s+)?(\d+)?\s*(\w+)',
            message, re.IGNORECASE)
        if trade_m:
            give_amt = int(trade_m.group(1)) if trade_m.group(1) else 5
            give_item = trade_m.group(2).lower()
            want_amt = int(trade_m.group(3)) if trade_m.group(3) else 5
            want_item = trade_m.group(4).lower()
            _stop = ("to", "a", "the", "some", "it", "them", "me", "you")
            if give_item not in _stop and want_item not in _stop:
                # Replace older offer from the same sender
                self.pending_trades = [t for t in getattr(self, 'pending_trades', [])
                                       if t.get("from") != sender]
                self.pending_trades.append({
                    "from": sender, "gives": give_item, "gives_amount": give_amt,
                    "wants": want_item, "wants_amount": want_amt, "time": time.time()})
                logger.info(f"SETTLEMENT: trade offer from {sender}: "
                            f"{give_amt}x {give_item} for {want_amt}x {want_item}")
                return

        patterns = [
            r'(?:i )?need\s+(\w+)',
            r'(?:looking for|searching for)\s+(\w+)',
            r'(?:anyone have|does anyone have)\s+(\w+)',
        ]
        for pat in patterns:
            m = re.search(pat, message, re.IGNORECASE)
            if m:
                item = m.group(1).lower()
                if item not in ("to", "a", "the", "some", "help", "safety"):
                    self.pending_requests.append({
                        "from": sender, "item": item, "time": time.time()})
                    # Expire old requests
                    self.pending_requests = [r for r in self.pending_requests
                                             if time.time() - r.get("time", 0) < 300]
                    logger.info(f"SETTLEMENT: request from {sender}: needs {item}")
                    break

    def update_chest(self, contents: dict[str, int]):
        """Sync chest contents from perception data."""
        self.village_chest = {k: v for k, v in contents.items() if v > 0}


class AgentManager:
    """
    Manages all AI agents in the simulation.
    Handles spawning, despawning, and running the cognitive loop for all agents.
    """

    def __init__(self):
        self.agents: dict[str, ValisAgent] = {}
        self._bridge = None
        self._despawned_recently: set[str] = set()  # Prevent auto-recreate race
        self.settlement = Settlement()
        self._council_tick: int = 0
        self._council_running: bool = False

    def set_bridge(self, bridge):
        """Set the WebSocket bridge for agent communication."""
        self._bridge = bridge

    async def reconcile_roster(self):
        """Reconcile brain-side agents with Minecraft NPCs on every (re)connect.

        1. Create brain-side agent objects for all roster entries (no spawn msg).
        2. Wait for perception data to arrive from existing NPCs.
        3. Only send agent_spawn for agents that didn't receive any perception
           (= NPC doesn't exist on the server yet).

        This makes agents survive brain restarts, server restarts, and reconnects.
        """
        try:
            from config import load_roster
            roster = load_roster()
        except Exception as e:
            logger.warning(f"Roster load failed: {e}")
            return

        if not roster:
            return

        newly_created: list[tuple[str, "RosterEntry"]] = []
        for entry in roster:
            if entry.name in self.agents:
                logger.info(f"Roster: {entry.name} already has brain-side agent, skipping")
                continue
            try:
                await self.spawn_agent(entry.name, entry.personality, send_spawn_msg=False)
                newly_created.append((entry.name, entry))
                logger.info(f"Roster: created brain-side agent for {entry.name} ({entry.personality})")
            except Exception as e:
                logger.warning(f"Roster agent creation failed for {entry.name}: {e}")

        if not newly_created:
            logger.info("Roster reconciliation: all agents already exist")
            return

        # Initialize settlement center from spawn positions so agents get
        # distance context from tick 1 (before any shelter is built)
        if self.settlement.center is None and newly_created:
            xs = [e.offset_x for _, e in newly_created]
            zs = [e.offset_z for _, e in newly_created]
            cx = sum(xs) // len(xs)
            cz = sum(zs) // len(zs)
            self.settlement.center = (cx, 64, cz)
            logger.info(f"SETTLEMENT: initial center from spawn positions: ({cx},64,{cz})")

        logger.info(f"Roster: waiting 8s for perception from {len(newly_created)} agent(s)...")
        await asyncio.sleep(8)

        for name, entry in newly_created:
            agent = self.agents.get(name)
            if agent and agent._pending_perception is not None:
                logger.info(f"Roster: {name} received perception — NPC exists on server, no spawn needed")
            else:
                logger.info(f"Roster: {name} got no perception — spawning NPC on server")
                if self._bridge:
                    await self._bridge.send({
                        "type": "agent_spawn", "name": name, "personality": entry.personality,
                        "x": entry.offset_x, "y": 64 + entry.offset_y, "z": entry.offset_z,
                    })

        # Place village chest at settlement center
        if self.settlement.center and not self.settlement.chest_placed and self._bridge:
            cx, cy, cz = self.settlement.center
            await self._bridge.send({
                "type": "place_village_chest", "x": cx, "y": cy, "z": cz,
            })
            self.settlement.chest_placed = True
            logger.info(f"SETTLEMENT: village chest placed at ({cx},{cy},{cz})")

    async def run_village_council(self):
        """Village Council — a meta-LLM call that assigns tasks to agents.

        Runs every 30 agent-ticks (~60 seconds). Sees all agents' positions,
        inventories, and settlement state. Outputs role-based task assignments.
        PIANO-compliant: an LLM decides, not hardcoded logic.
        """
        if self._council_running or len(self.agents) < 2:
            return
        self._council_running = True
        try:
            # Build global state summary
            agent_summaries = []
            for name, agent in self.agents.items():
                p = agent._pending_perception
                if not p:
                    continue
                pos = p.position
                inv = {k: v for k, v in p.inventory.items() if k != "air" and v > 0}
                inv_str = ", ".join(f"{v}x {k}" for k, v in
                    sorted(inv.items(), key=lambda x: -x[1])[:6]) or "empty"
                dist = 0
                if self.settlement.center:
                    import math
                    cx, _, cz = self.settlement.center
                    dist = math.sqrt((pos.get("x",0)-cx)**2 + (pos.get("z",0)-cz)**2)
                agent_summaries.append(
                    f"- {name} ({agent.personality}): pos=({pos.get('x',0):.0f},{pos.get('y',0):.0f},{pos.get('z',0):.0f}), "
                    f"{dist:.0f}m from center, inventory=[{inv_str}]"
                )

            settlement_info = ""
            s = self.settlement
            if s.center:
                cx, cy, cz = s.center
                chest_str = ", ".join(f"{v}x {k}" for k, v in
                    sorted(s.village_chest.items(), key=lambda x: -x[1])[:6]) or "empty"
                settlement_info = (
                    f"Settlement center: ({cx},{cy},{cz}), {s.shelters_built} shelters.\n"
                    f"Village chest: {chest_str}\n"
                    f"Population: {len(self.agents)} villagers (capacity: {MAX_VILLAGERS})."
                )

            history_info = ""
            if s.chronicle:
                history_info = "VILLAGE HISTORY (chronicle so far):\n" + "\n".join(
                    f"- {e}" for e in s.chronicle[-5:])
            if s.rules:
                history_info += "\nVILLAGE RULES (adopted by vote):\n" + "\n".join(
                    f"- {r}" for r in s.rules)

            recruit_info = ""
            if len(self.agents) < MAX_VILLAGERS:
                recruit_info = (
                    'Optional: if the village is thriving (shelters built, surplus in the chest) '
                    'and would benefit from another member, you MAY add a "RECRUIT" key: '
                    '{"name": "UniqueName", "role": "farmer|guard|trader|artist|priest|miner|builder|explorer", '
                    '"reason": "one sentence why the village needs them"}. '
                    'Recruit only when the village can support a new member — not every session.'
                )

            prompt = f"""You are the Village Council — a strategic planner for a Minecraft AI village.
You see ALL agents and the village state. Assign ONE specific task to each agent based on their role.

AGENTS:
{chr(10).join(agent_summaries)}

{settlement_info}

{history_info}

RULES:
- Each agent gets ONE task. Tasks should be specific with coordinates where possible.
- Agents far from center (>80 blocks) should return to center FIRST, then do their task.
- Prioritize: 1) Return to center if far away, 2) Deposit surplus into village chest, 3) Role-specific work near center.
- The gather loop is: go out (max 50 blocks), get resources, RETURN to center, deposit in chest.
- Miner: gather stone/ores. Builder: build shelters/structures. Explorer: scout nearby (max 50 blocks), report back.
- Also add a "CHRONICLE" key: ONE sentence recording the most notable village development since the last entry (for the village history book). Skip it if nothing noteworthy happened.
- Optional: if the village faces a recurring coordination problem, you MAY add a "PROPOSAL" key with ONE short village rule to vote on (e.g. "Always deposit surplus ore in the chest before nightfall"). The villagers will vote; a majority adopts it as law. Propose rarely — only when a real problem needs a rule.
{recruit_info}

Output ONLY a JSON object mapping agent names to task strings:
{{"AgentName": "specific task instruction", ..., "CHRONICLE": "optional history sentence"}}"""

            # Use any agent's LLM provider
            any_agent = next(iter(self.agents.values()))
            import json, re
            try:
                response = await any_agent.llm.chat([
                    {"role": "system", "content": "You are a village planning AI. Output only JSON."},
                    {"role": "user", "content": prompt},
                ])
                json_str = response.strip()
                json_str = re.sub(r'^```(?:json)?\s*', '', json_str)
                json_str = re.sub(r'\s*```$', '', json_str)
                brace_start = json_str.find('{')
                brace_end = json_str.rfind('}')
                if brace_start >= 0 and brace_end > brace_start:
                    json_str = json_str[brace_start:brace_end + 1]
                assignments = json.loads(json_str)

                # Chronicle — the council records village history (persistent)
                chronicle_entry = assignments.pop("CHRONICLE", None)
                if chronicle_entry and isinstance(chronicle_entry, str):
                    self.settlement.append_chronicle(chronicle_entry)

                # Governance — a proposed rule goes to a village-wide vote
                proposal = assignments.pop("PROPOSAL", None)
                if proposal and isinstance(proposal, str) and len(self.settlement.rules) < 8:
                    asyncio.create_task(self.run_village_vote(proposal.strip()[:200]))

                # Recruitment — the council may grow the village when it thrives
                recruit = assignments.pop("RECRUIT", None)
                if recruit and isinstance(recruit, dict) and len(self.agents) < MAX_VILLAGERS:
                    rname = re.sub(r'[^A-Za-z0-9_]', '', str(recruit.get("name", "")))[:16]
                    rrole = str(recruit.get("role", "default")).lower().strip() or "default"
                    rreason = " ".join(str(recruit.get("reason", "")).split())[:150]
                    if rname and rname not in self.agents:
                        logger.info(f"COUNCIL: recruiting new villager {rname} ({rrole}): {rreason}")
                        new_agent = await self.spawn_agent(rname, rrole)
                        if rreason:
                            new_agent.goals.insert(0, f"[Founding purpose] {rreason}")
                        self.settlement.append_chronicle(
                            f"{rname} joined the village as {rrole}" +
                            (f" — {rreason}" if rreason else ""))

                for name, task in assignments.items():
                    agent = self.agents.get(name)
                    if agent:
                        agent._council_assignment = str(task)[:200]
                        agent._council_tick = agent.tick_count
                        agent._cached_decision = None  # force fresh controller call
                logger.info(f"COUNCIL: assigned tasks to {len(assignments)} agents: "
                           + " | ".join(f"{n}: {str(t)[:60]}" for n, t in assignments.items()))
            except Exception as e:
                logger.warning(f"COUNCIL: LLM call failed: {e}")

        finally:
            self._council_running = False

    async def run_village_vote(self, proposal: str):
        """Village-wide vote on a council proposal. Each agent's own LLM votes
        based on its personality and goals — democracy, PIANO-style. A majority
        adopts the rule permanently (persisted, in every prompt from then on).
        """
        if getattr(self, '_vote_running', False) or len(self.agents) < 2:
            return
        self._vote_running = True
        try:
            logger.info(f"VOTE: village votes on proposal: {proposal}")
            votes: dict[str, bool] = {}
            for name, agent in list(self.agents.items()):
                try:
                    response = await agent.llm.chat([
                        {"role": "system",
                         "content": "You are a Minecraft villager voting on a village rule. "
                                    "Answer ONLY with YES or NO followed by one short reason."},
                        {"role": "user",
                         "content": f"You are {name}, a {agent.personality} "
                                    f"(traits: {', '.join(agent.traits) or 'none'}). "
                                    f"Your goals: {'; '.join(agent.goals[:2]) or 'survive'}.\n"
                                    f"Proposed village rule: \"{proposal}\"\n"
                                    f"Existing rules: {'; '.join(self.settlement.rules) or 'none'}\n"
                                    f"Vote YES or NO."},
                    ])
                    vote_yes = response.strip().upper().startswith("YES")
                    votes[name] = vote_yes
                    logger.info(f"VOTE: {name} votes {'YES' if vote_yes else 'NO'} — {response.strip()[:80]}")
                except Exception as e:
                    logger.warning(f"VOTE: {name} could not vote: {e}")
            if not votes:
                return
            yes = sum(1 for v in votes.values() if v)
            adopted = yes > len(votes) / 2
            result_str = f"{yes}/{len(votes)} voted yes"
            if adopted:
                self.settlement.rules.append(proposal)
                self.settlement.save_state()
                self.settlement.append_chronicle(
                    f"The village adopted a new rule by vote ({result_str}): {proposal}")
                logger.info(f"VOTE: ADOPTED ({result_str}): {proposal}")
            else:
                self.settlement.append_chronicle(
                    f"A proposed rule was rejected by vote ({result_str}): {proposal}")
                logger.info(f"VOTE: REJECTED ({result_str}): {proposal}")
            # Every agent remembers the vote
            for name, agent in list(self.agents.items()):
                await agent.memory.add_event(
                    content=f"[Village vote] Proposal '{proposal}' was "
                            f"{'ADOPTED' if adopted else 'rejected'} ({result_str}). "
                            f"I voted {'YES' if votes.get(name) else 'NO'}.",
                    importance=0.7,
                    subject=name, predicate="voted", object=proposal[:80],
                )
        finally:
            self._vote_running = False

    async def spawn_agent(self, name: str, personality: str = "default",
                          send_spawn_msg: bool = True) -> ValisAgent:
        """Create and start a new agent.

        Looks up the personality in agents.yaml to populate traits, goals, and focus.
        """
        if name in self.agents:
            logger.warning(f"Agent {name} already exists, despawning first.")
            await self.despawn_agent(name)

        try:
            from config import get_personality
            spec = get_personality(personality)
            traits = spec["traits"]
            initial_goals = spec["initial_goals"]
            focus = spec["focus"]
        except Exception as e:
            logger.warning(f"Personality lookup failed for '{personality}': {e}")
            traits, initial_goals, focus = [], [], ""

        config = AgentConfig(
            name=name,
            personality=personality,
            data_dir="data",
            tick_rate=2.0,
            traits=traits,
            initial_goals=initial_goals,
            focus=focus,
        )
        agent = ValisAgent(config, bridge=self._bridge)
        agent.settlement = self.settlement
        self.agents[name] = agent
        await agent.start()

        # Send agent_spawn back to Minecraft to create the NPC.
        # Spawn at the settlement center (surface-synced) when one exists —
        # new recruits should appear in the village, not at world origin.
        if self._bridge and send_spawn_msg:
            sx, sy, sz = (0, 64, 0)
            if self.settlement.center:
                cx, cy, cz = self.settlement.center
                import random as _r
                sx, sy, sz = cx + _r.randint(-2, 2), cy, cz + _r.randint(-2, 2)
            await self._bridge.send({"type": "agent_spawn", "name": name,
                                     "personality": personality, "x": sx, "y": sy, "z": sz})

        logger.info(f"Agent spawned: {name} ({personality}, traits={traits}). Total agents: {len(self.agents)}")
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
            pass  # perception delivery logged via tick INFO only
        else:
            # Don't auto-create if recently despawned (race condition)
            if perception.agent_name in self._despawned_recently:
                self._despawned_recently.discard(perception.agent_name)
                return
            # Auto-create agent from perception data (server already has the NPC).
            # If the name is on our roster, use that personality; otherwise default.
            roster_personality = "default"
            try:
                from config import load_roster
                for entry in load_roster():
                    if entry.name == perception.agent_name:
                        roster_personality = entry.personality
                        break
            except Exception:
                pass
            logger.info(f"Auto-creating agent from perception: {perception.agent_name} ({roster_personality})")
            await self.spawn_agent(perception.agent_name, roster_personality, send_spawn_msg=False)
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

            # Village Council — run every 30 ticks (~60s)
            self._council_tick += 1
            if self._council_tick >= 30 and len(self.agents) >= 2:
                self._council_tick = 0
                asyncio.create_task(self.run_village_council())

            await asyncio.sleep(0.1)  # Small delay to prevent busy-loop

    def get_agent_count(self) -> int:
        return len(self.agents)

    def get_all_relationship_data(self) -> dict:
        """Get relationship graph data for all agents (for dashboard)."""
        return {
            name: agent.social_awareness.get_relationship_graph_data()
            for name, agent in self.agents.items()
        }
