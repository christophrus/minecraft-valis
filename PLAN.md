# Project Valis: Minecraft AI Civilization

> A PaperMC server where 20-100 generative AI agents inhabit a Minecraft world, using a synthesized architecture from "Generative Agents: Interactive Simulacra of Human Behavior" (Park et al., 2023) and "Project Sid: Many-agent simulations toward AI civilization" (Altera.AL, 2024).

## References

- **Generative Agents**: https://arxiv.org/abs/2304.03442 — Memory Stream, Reflection, Planning, Observation
- **Project Sid / PIANO**: https://arxiv.org/abs/2411.00114 — PIANO Architecture (Parallel Information Aggregation via Neural Orchestration), Cognitive Controller, Social Awareness, Civilization mechanics

---

## Architecture Review (2026-06-25)

### Generative Agents: Paper vs Implementation

| Paper Concept | Implementation | Grade |
|--------------|----------------|-------|
| **Memory Stream** — record all experiences as natural language | ✅ SQLite + ChromaDB, embedding-based | Good |
| **Retrieval** — weighted by recency × relevance × importance | 🟡 Only n=3 recent memories; no importance scoring | Basic |
| **Reflection** — periodic synthesis into higher-level insights | ✅ Reflection class, fires every ~10 ticks | Seldom consulted in decisions |
| **Planning** — daily plan → hourly segments → actions | 🟡 plan_daily() exists but no temporal decomposition | Fast-path constantly overrides plan |
| **Observation** — structured world perception | ✅ WorldObserver (80 blocks, biomes, entities) | Good |

### PIANO: Paper vs Implementation

| Paper Concept | Implementation | Grade |
|--------------|----------------|-------|
| **Cognitive Controller** — bottleneck for coherence | ✅ Synthesizes Perception + Memory + Goals | Often bypassed by Reflex Layer |
| **Parallel Information Aggregation** — simultaneous inputs | 🟡 Modules polled sequentially, not parallel | asyncio structure present |
| **Action Awareness** — expected vs actual comparison | ✅ Learns from discrepancies, blacklists repeat failures | Good |
| **Social Awareness** — directed sentiment graph | 🟡 Data structure exists, unused (single agent) | Phase 4 |
| **Skill Execution** — translate to Minecraft mechanics | ✅ 9 action types, tool-aware mining, block animation | Extensive |
| **Role Specialization** — agents develop roles | ❌ Not implemented | Phase 4 |
| **Collective Rules** — constitution, voting, taxation | ❌ Not implemented | Phase 4 |
| **Cultural Transmission** — memes, religion, values | ❌ Not implemented | Phase 4 |

### Key Finding

The **Reflex Layer** (fast-path) has grown so much that it dominates the LLM planning flow
(Perception → Retrieval → Plan → Execute). The Generative Agents paper emphasizes:
*"Planning, reflection, and observation each contribute critically to the believability
of agent behavior."* Our agent relies ~80% on deterministic rules and ~20% on LLM planning.

**Recommendation for Phase 4**: Thin out fast-path, strengthen LLM planning pipeline.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────┐
│            Minecraft PaperMC Server              │
│  ┌───────────────────────────────────────────┐  │
│  │          valis-core Plugin                 │  │
│  │  ┌─────────┐ ┌──────────┐ ┌───────────┐  │  │
│  │  │VirtualAgent│WorldObserver│ActionExec  │  │  │
│  │  └─────────┘ └──────────┘ └───────────┘  │  │
│  │  ┌──────────────────────────────────┐    │  │
│  │  │     WebSocket Server             │    │  │
│  │  └──────────────────────────────────┘    │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
                        │
                        │ WebSocket (JSON messages)
                        │
┌─────────────────────────────────────────────────┐
│         Python Agent Brain Service               │
│  ┌──────────────────────────────────────────┐   │
│  │           Agent Loop (asyncio)           │   │
│  │  perceive → retrieve → plan → reflect    │   │
│  │                  ↓                        │   │
│  │              execute                      │   │
│  └──────────────────────────────────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │Memory Stream│ Cognitive │ Social Aware  │   │
│  │(SQLite+   │ │Controller│ (sentiment    │   │
│  │ ChromaDB) │ │(PIANO)   │  graph)       │   │
│  └──────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────────────────────────────────────┐   │
│  │    LLM Provider (OpenAI / Anthropic /    │   │
│  │              Ollama)                      │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

## Key Design Decisions

- **LLM Backend**: Multi-provider, configurable (OpenAI GPT-4o, Anthropic Claude, Ollama/local models)
- **Agent Scale**: Medium (20-100 concurrent agents)
- **Server**: PaperMC (latest stable)
- **Agent Brain Language**: Python (best LLM ecosystem)
- **NPC System**: Citizens2 API (Build 4210, kompatibel mit PaperMC 26.1.2 via `v26_1_R1` NMS-Modul)

## Folder Structure

```
minecraft-valis/
├── PLAN.md                    # This file
├── README.md                  # Project overview
├── server/                    # PaperMC server directory
├── plugin/                    # Gradle-based PaperMC plugin
│   ├── build.gradle.kts
│   ├── settings.gradle.kts
│   └── src/main/java/com/valis/
│       ├── ValisPlugin.java
│       ├── bridge/
│       │   └── WebSocketBridge.java
│       ├── agent/
│       │   └── VirtualAgent.java
│       ├── perception/
│       │   └── WorldObserver.java
│       ├── execution/
│       │   └── ActionExecutor.java
│       └── config/
│           └── ValisConfig.java
├── agent-brain/               # Python agent service
│   ├── pyproject.toml
│   ├── main.py
│   ├── agent.py
│   ├── llm/
│   │   └── providers.py
│   ├── memory/
│   │   ├── memory_stream.py
│   │   └── retrieval.py
│   ├── cognitive/
│   │   ├── perception.py
│   │   ├── planning.py
│   │   ├── reflection.py
│   │   ├── execution.py
│   │   ├── controller.py
│   │   ├── action_awareness.py
│   │   ├── social_awareness.py
│   │   └── goal_generation.py
│   ├── bridge/
│   │   ├── client.py
│   │   └── protocol.py
│   ├── config/
│   │   └── agents.yaml
│   └── dashboard/
│       └── index.html
└── .gitignore
```

## Phased Implementation Plan

### Phase 1: Foundation ✅ 100%
1. ✅ Set up PaperMC server (JDK 21, world config)
2. ✅ Create plugin skeleton (valis-core) with Citizens2 + ProtocolLib
3. ✅ Create Python agent brain service (asyncio)
4. ✅ Establish WebSocket bridge between plugin and agent brain
5. ✅ Spawn first AI-controlled NPC agent in the world

### Phase 2: Core Agent Architecture (Generative Agents) ✅ 85%
6. ✅ Perception module — capture world state (80 blocks, radius 12)
7. ✅ Memory Stream — associative memory with embeddings (SQLite + ChromaDB)
8. 🟡 Retrieval — weighted by recency + relevance (no importance scoring yet)
9. 🟡 Planning — daily schedules + moment-to-moment action selection (no temporal decomposition: "go to X at 10am, mine at 11am" missing)
10. ✅ Reflection — synthesize memories into higher-level insights (~18 cycles/session)
11. ✅ Skill Execution — 9 action types (move_to, mine_block, place_block, craft auto-chain, attack_mob, collect_items, equip, teleport, idle). Tool-aware mining. Block-breaking animation.
12. ✅ Agent loop — perceive → controller → plan → reflect → execute

### Phase 3: PIANO Enhancements (Project Sid) ✅ 60%
13. ✅ Concurrent module execution (asyncio)
14. ✅ Cognitive Controller — bottlenecked decision-making for coherence
15. ✅ Action Awareness — compare expected vs actual outcomes, blacklist repeat failures
16. 🟡 Social Awareness — directed sentiment graph between agents (data structure exists, unused — single agent)
17. ✅ Goal Generation — create objectives from experiences (2 goal types: economic, survival)

### Phase 4: Multi-Agent Civilization 🔲 0%
18. 🔲 Personality & Trait system
19. 🔲 Multi-agent orchestration (2–100 agents)
20. 🔲 Role specialization (professions: lumberjack, miner, builder, farmer)
21. 🔲 Collective rule system (constitution, voting, taxation)
22. 🔲 Cultural transmission (memes, religion, values)
23. 🔲 Economy system (trade, currency, marketplaces)

### Phase 5: Observability & Polish 🟡 30%
24. 🔲 Web dashboard
25. 🔲 Configuration system (YAML/JSON)
26. 🟡 Debug logging (comprehensive: NAV tracking, stuck detection, emergency help, action results, inventory snapshots)
27. 🔲 Performance optimization

## Verification Criteria

- **Phase 1**: ✅ Server starts, plugin loads, WebSocket connects, single NPC spawns
- **Phase 2**: ✅ Agent performs full day-night cycle, executes Minecraft actions; 🟡 planning uses LLM but lacks temporal decomposition
- **Phase 3**: ✅ Controller + ActionAwareness + GoalGen functional; 🟡 SocialAwareness unused (single agent)
- **Phase 4**: 🔲 2+ agents coexist, specialize in roles, participate in governance, propagate culture
- **Phase 5**: 🟡 Debug logs comprehensive; dashboard/config pending

## Beyond Plan — Additional Features Built

During Phase 2/3 implementation, several unplanned but necessary features were added:

| Feature | Purpose | Paper Reference |
|---------|---------|----------------|
| **Pre-emptive Crafting (Reflex Layer)** | Auto-crafts log→plank→stick→pickaxe→axe without LLM | Skill Execution (Sid) |
| **Junk Filter + Overrides** | Prevents mining dirt when wood needed; allows at night/from plan | Observation Filter (GA) |
| **Stuck Detection + Anti-Stuck Jump** | Detects 5+ ticks at same position; STUCK-DIG → anti-stuck jump | Error Recovery |
| **Forest Heading Lock** | Locks explore heading for 20-30 steps when forest nearby | Exploration Heuristic |
| **Leaves as Wood Indicator** | Counts *_LEAVES in `wood_in_perception`, navigates toward leaves | Perception Heuristic |
| **Far-Target Retry Loop (3×)** | After 3 attempts to reach same far block, falls back to nearest | Plan Adaptation |
| **Shelter Building (4-block ring)** | N/E/S/W block placement when plan mentions "shelter" | Skill Execution |
| **Crafting Table Auto-Place** | Places crafting_table at feet+1 when in inventory | Reflex Automation |
| **Hunting + Collect** | attack_mob → collect_items (auto-collects dropped items) | Skill Execution |
| **NAV Debug Tracking** | NAV-SEND/PROGRESS/STALL/ESCAPE logs for pathfinder diagnostics | Diagnostics |
| **Emergency LLM Help** | When stuck → sends problem report to LLM for escape instructions | Error Recovery |
| **Tool-Aware Mining** | getBestTool() selects pickaxe/axe/shovel by block type; auto-equips | Skill Execution |
| **Plugin Chunk Tickets** | Keeps chunks around NPC loaded via Paper API (no players needed) | Infrastructure |
| **Block-Breaking Animation** | ProtocolLib stages 0-9 over ~1s with NMS packet construction | Visual Feedback |
| **STUCK-ESCAPE Teleport** | Teleports agent out of stuck position when all else fails | Error Recovery |
| **Craft→Idle Deadlock Detection** | Detects 3× craft→idle loops, clears craft cooldowns | Reflex Tuning |

## Excluded Scope (Future)

- Human players as interactive participants
- Computer vision on Minecraft screen (use structured world state)
- Full crafting recipe tree (core items first)
- Currency-based economy
- Cross-server agent migration

## Citizens2 Compatibility Analysis (2026-06-24)

**Status**: ✅ **SOLVED** — PaperMC 26.1.2 + Citizens2 Build 4210 funktionieren!

**Root cause analysis**: Citizens2 `NMS.loadBridge()` wählt das NMS-Modul basierend auf `SpigotUtil.getVersion()`:
```java
case 21:
    if (version[2] < 9)       rev = "v1_21_R5";   // Paper 1.21.x
    ...
switch (version[0]) {
    case 26:
    case 27:
        rev = "v26_" + version[1] + "_R1";         // Paper 26.x ← DAS!
}
```

PaperMC 26.1.2 → `version[0]=26, version[1]=1` → `v26_1_R1` ✅  
PaperMC 1.21.4 → `version[0]=1, version[1]=21, version[2]=4` → `v1_21_R5` ❌ (nicht in JAR)

**Server-Konfiguration**:
- PaperMC: 26.1.2 Build #72
- Java: JDK 25+ (Eclipse Adoptium Temurin-25)
- Plugin-API: `1.21.4-R0.1-SNAPSHOT` (Bukkit-API ist rückwärtskompatibel)
- Gradle: `JAVA_HOME=C:\Program Files\Zulu\zulu-21` für Build, JDK 25 für Runtime
