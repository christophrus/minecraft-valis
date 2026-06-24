# Project Valis: Minecraft AI Civilization

> A PaperMC server where 20-100 generative AI agents inhabit a Minecraft world, using a synthesized architecture from "Generative Agents: Interactive Simulacra of Human Behavior" (Park et al., 2023) and "Project Sid: Many-agent simulations toward AI civilization" (Altera.AL, 2024).

## References

- **Generative Agents**: https://arxiv.org/abs/2304.03442 — Memory Stream, Reflection, Planning, Observation
- **Project Sid**: https://arxiv.org/abs/2411.00114 — PIANO Architecture (Parallel Information Aggregation via Neural Orchestration)

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

### Phase 1: Foundation ✅
1. ✅ Set up PaperMC server (JDK 21, world config)
2. ✅ Create plugin skeleton (valis-core) with Citizens2 + ProtocolLib
3. ✅ Create Python agent brain service (FastAPI + asyncio)
4. ✅ Establish WebSocket bridge between plugin and agent brain
5. ✅ Spawn first AI-controlled NPC agent in the world

### Phase 2: Core Agent Architecture (Generative Agents) ✅
6. ✅ Perception module — capture world state around each agent
7. ✅ Memory Stream — associative memory with embeddings (SQLite + ChromaDB)
8. ✅ Retrieval — weighted by recency, relevance, importance
9. ✅ Planning — daily schedules + moment-to-moment action selection
10. ✅ Reflection — synthesize memories into higher-level insights
11. ✅ Skill Execution — translate plans to Minecraft mechanics
    - move_to, mine_block, place_block, craft (auto-chain: log→plank→stick→pickaxe)
    - attack_mob, collect_items
12. ✅ Agent loop — perceive → retrieve → plan → reflect → execute

### Phase 3: PIANO Enhancements (Project Sid) ✅
13. ✅ Concurrent module execution (asyncio)
14. ✅ Cognitive Controller — bottlenecked decision-making for coherence
15. ✅ Action Awareness — compare expected vs actual outcomes
16. ✅ Social Awareness — directed sentiment graph between agents
17. ✅ Goal Generation — create objectives from experiences

### Phase 4: Multi-Agent Civilization
18. 🔲 Personality & Trait system
19. 🔲 Multi-agent orchestration (2-100 agents)
20. 🔲 Role specialization emergence
21. 🔲 Collective rule system (constitution, voting, taxation)
22. 🔲 Cultural transmission (memes + religion)

### Phase 5: Observability & Polish
23. 🔲 Web dashboard
24. 🔲 Configuration system (YAML/JSON)
25. 🟡 Logging & debug (comprehensive debug logs active, NAV tracking, stuck detection)
26. 🔲 Performance optimization

## Verification Criteria

- **Phase 1**: ✅ Server starts, plugin loads, WebSocket connects, single NPC spawns
- **Phase 2**: ✅ Agent performs full day-night cycle, executes Minecraft actions
- **Phase 3**: ✅ Agent maintains coherence, learns from outcomes, tracks social sentiments
- **Phase 4**: 🔲 2+ agents coexist, specialize, participate in governance, propagate culture
- **Phase 5**: 🟡 Debug logging active, dashboard/config pending

## Beyond Plan — Additional Features Built

During Phase 2/3 implementation, several unplanned but necessary features were added:

| Feature | Purpose |
|---------|---------|
| **Pre-emptive Crafting (Reflex Layer)** | Auto-crafts log→plank→stick→pickaxe without LLM involvement |
| **Junk Filter + Overrides** | Prevents mining dirt when wood needed, but allows at night/from plan |
| **Stuck Detection + Anti-Stuck Jump** | Detects 5+ ticks at same position, resets nav with random jump |
| **Forest Heading Lock** | When forest biome nearby, locks explore heading for 20-30 steps |
| **Leaves as Wood Indicator** | Counts *_LEAVES in `wood_in_perception`, navigates toward leaves |
| **Far-Target Retry Loop** | After 3 attempts to reach same far block, falls back to nearest wood |
| **Shelter Building (4-block ring)** | N/E/S/W block placement when plan mentions "shelter" |
| **Crafting Table Auto-Place** | Places crafting_table at feet+1 when in inventory |
| **Hunting (attack_mob + collect_items)** | Attacks nearest animal, collects dropped items |
| **NAV Debug Tracking** | NAV-SEND/NAV-PROGRESS/NAV-STALL logs for pathfinder diagnostics |
| **Controller Prompt v2** | Crafting recipes, hunt hint, clear decision rules in LLM prompt |

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
