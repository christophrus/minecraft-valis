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

        if hint in ("mine", "place"):
            target = None
            
            # Priority 1: LLM intent target (if valid and not recently mined)
            if intent_target and pos_key(intent_target) not in self._recently_mined:
                target = intent_target
                logger.debug(f"FAST-PATH: TARGET=intent -> {target.get('type','?')} at ({target.get('x')},{target.get('y')},{target.get('z')})")
            
            # Priority 2: Wood/log blocks nearby (only for mine, not place!)
            if target is None and hint == "mine":
                wood_blocks = [b for b in blocks if b.get("type", "").upper() in 
                              ("OAK_LOG", "BIRCH_LOG", "SPRUCE_LOG", "JUNGLE_LOG", "ACACIA_LOG", 
                               "DARK_OAK_LOG", "CHERRY_LOG", "MANGROVE_LOG", "OAK_WOOD", "BIRCH_WOOD")]
                wood_blocks = [b for b in wood_blocks if pos_key(b) not in self._recently_mined]
                if wood_blocks:
                    target = min(wood_blocks, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
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
                                break
                        if target: break
            
            # Priority 4: Nearest solid block (fallback)
            if target is None:
                solid_blocks = [b for b in blocks if b.get("type", "AIR") not in ("AIR", "CAVE_AIR", "VOID_AIR", "WATER", "LAVA")]
                solid_blocks = [b for b in solid_blocks if pos_key(b) not in self._recently_mined]
                if solid_blocks:
                    target = min(solid_blocks, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
            
            if target:
                target_type = target.get("type", "").upper()
                # For place: use agent's foot Y as base; for mine: use target's Y
                if hint == "place":
                    tx, ty, tz = target.get("x", px), py, target.get("z", pz)
                else:
                    tx, ty, tz = target.get("x", px), target.get("y", py - 1), target.get("z", pz)
                
                # If hint is "mine" but target is junk and we need wood → explore instead
                has_wood = any(k in ("oak_log","birch_log","spruce_log","acacia_log","dark_oak_log","cherry_log") 
                              for k in perception.inventory)
                if hint == "mine" and not has_wood and target_type in ("DIRT","GRASS_BLOCK","STONE","COBBLESTONE","SAND","GRAVEL","SHORT_GRASS","ANDESITE","DIORITE","GRANITE","TUFF","DEEPSLATE"):
                    pass  # Fall through to move/explore
                elif hint == "mine":
                    tkey = f"{int(tx)},{int(ty)},{int(tz)}"
                    if tkey in self._recently_placed:
                        logger.debug(f"FAST-PATH: skip mine of recently placed block at ({int(tx)},{int(ty)},{int(tz)})")
                        pass
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
                    # Find free air above target, skip recently failed Y levels
                    above_y = None
                    for dy in range(1, 6):
                        test_y = int(ty) + dy
                        test_key = f"{int(tx)},{test_y},{int(tz)}"
                        if test_key in self._recently_failed_place:
                            continue
                        blocked = any(b.get("x",0)==int(tx) and b.get("y",0)==test_y and b.get("z",0)==int(tz)
                                      and b.get("type","").upper() not in ("AIR","CAVE_AIR","VOID_AIR") for b in blocks)
                        if not blocked:
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

        if hint == "craft":
            logger.debug(f"FAST-PATH: craft hint -> returning None for LLM fallback")
            return None  # Let LLM decide

        if hint in ("move", "explore", "mine", "place"):
            import math
            # Don't interrupt ongoing navigation — idle directly (skip LLM fallback)
            if hasattr(self, '_nav_target') and self._nav_target:
                tx, ty, tz = self._nav_target
                dist = math.sqrt((px - tx)**2 + (py - ty)**2 + (pz - tz)**2)
                elapsed = time.time() - getattr(self, '_nav_start', 0)
                if dist > 3 and elapsed < 8:
                    logger.debug(f"FAST-PATH: waiting for nav, dist={dist:.1f} elapsed={elapsed:.1f}s")
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
            
            # Priority 2: Wood nearby while exploring → stop and mine
            wood_nearby = [b for b in blocks if b.get("type","").upper() in 
                          ("OAK_LOG","BIRCH_LOG","SPRUCE_LOG","JUNGLE_LOG","ACACIA_LOG",
                           "DARK_OAK_LOG","CHERRY_LOG","MANGROVE_LOG")]
            if wood_nearby and pos_key(wood_nearby[0]) not in self._recently_mined:
                t = min(wood_nearby, key=lambda b: abs(b.get("x",0)-px) + abs(b.get("y",0)-py) + abs(b.get("z",0)-pz))
                self._nav_target = None
                return AgentAction(agent_name="", action="mine_block",
                    params={"x": int(t.get("x",px)), "y": int(t.get("y",py-1)), "z": int(t.get("z",pz))})
            
            # Priority 3: Plan coordinates
            plan_text = " ".join(self.planner.daily_plan)
            coords = re.findall(r'\((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', plan_text)
            if coords and random.random() < 0.7:
                tx, ty, tz = map(int, random.choice(coords))
                self._nav_target = (tx, ty, tz)
                self._nav_start = time.time()
                return AgentAction(agent_name="", action="move_to",
                                   params={"x": tx, "y": ty, "z": tz})
            
            # Priority 4: Systematic exploration
            if not hasattr(self, '_explore_heading'):
                self._explore_heading = None
                self._explore_steps = 0
            nb = perception.nearby_biomes
            has_wood = any(k in ("oak_log","birch_log","spruce_log","acacia_log","dark_oak_log") 
                          for k in perception.inventory)
            if not has_wood and nb and self._explore_steps == 0:
                forest_dirs = []
                for d, b in nb.items():
                    if "forest" in b or "taiga" in b or "jungle" in b or "grove" in b or "wood" in b:
                        dir_map = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
                        if d in dir_map:
                            forest_dirs.append(dir_map[d])
                if forest_dirs:
                    self._explore_heading = random.choice(forest_dirs)
            self._explore_steps += 1
            if self._explore_steps > random.randint(8, 15):
                self._explore_heading = None
                self._explore_steps = 0
            if self._explore_heading is None:
                self._explore_heading = random.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
            dx, dz = self._explore_heading
            dist = random.randint(15, 30)
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
                 "DARK_OAK_LOG","CHERRY_LOG","MANGROVE_LOG"))
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
                    # If we have logs but no planks → craft planks
                    if inv.get("oak_log", 0) >= 1 and inv.get("oak_planks", 0) < 4:
                        logger.debug(f"FALLBACK-HEURISTIC: crafting planks (log={inv.get('oak_log',0)} planks={inv.get('oak_planks',0)})")
                        parsed = AgentAction(agent_name="", action="craft", params={"item": "oak_planks"})
                    # If we have planks but no sticks → craft sticks
                    elif inv.get("oak_planks", 0) >= 2 and inv.get("stick", 0) < 4:
                        logger.debug(f"FALLBACK-HEURISTIC: crafting sticks")
                        parsed = AgentAction(agent_name="", action="craft", params={"item": "stick"})
                    # If we have sticks and planks but no pickaxe
                    elif inv.get("stick", 0) >= 2 and inv.get("oak_planks", 0) >= 3 and not any("pickaxe" in k for k in inv):
                        logger.debug(f"FALLBACK-HEURISTIC: crafting wooden_pickaxe")
                        parsed = AgentAction(agent_name="", action="craft", params={"item": "wooden_pickaxe"})
                    else:
                        logger.debug(f"FALLBACK-HEURISTIC: nothing to craft, staying idle")

            # Warn if agent position jumped to spawn (Citizens pathfinder bug)
            if perception and parsed and parsed.action == "move_to":
                ppos = perception.position
                if abs(ppos.get("x", 0)) <= 2 and abs(ppos.get("z", 0)) <= 2:
                    logger.warning(f"Agent {self.name} at spawn ({ppos.get('x')},{ppos.get('y')},{ppos.get('z')}) — possible pathfinder reset")

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
