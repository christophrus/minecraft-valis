# Project Valis: Minecraft AI Civilization

> A PaperMC server where 20-100 generative AI agents inhabit a Minecraft world, using a synthesized architecture from "Generative Agents: Interactive Simulacra of Human Behavior" (Park et al., 2023) and "Project Sid: Many-agent simulations toward AI civilization" (Altera.AL, 2024).

## References

- **Generative Agents**: https://arxiv.org/abs/2304.03442 — Memory Stream, Reflection, Planning, Observation
- **Project Sid / PIANO**: https://arxiv.org/abs/2411.00114 — PIANO Architecture (Parallel Information Aggregation via Neural Orchestration), Cognitive Controller, Social Awareness, Civilization mechanics

---

## Architecture Review (2026-06-25)

### Generative Agents (Park et al. 2023): Paper vs Implementation

| Paper Concept | Unsere Implementierung | Status | Lücke |
|--------------|----------------------|--------|-------|
| **Memory Stream** — alle Erfahrungen als natürliche Sprache speichern | ✅ SQLite + ChromaDB, embedding-basiert | ✅ Gut | — |
| **Retrieval** — gewichtet nach recency × relevance × importance (Poignancy 1-10 via LLM, exponential decay 0.995^h, Embedding-Cosinus) | 🟡 Nur n=3 letzte Memories; kein Importance-Scoring, kein Decay | ⚠️ Grundlegend | Importance-Scoring fehlt komplett; Retrieval-Formel nicht implementiert |
| **Reflection** — Synthese zu höherwertigen Einsichten (ausgelöst wenn ∑importance > 150, generiert Fragen, Baum-Struktur: Beobachtung → Reflexion → Meta-Reflexion) | 🟡 Reflection-Klasse vorhanden, feuert alle ~10 Ticks | ⚠️ Vorhanden, aber wirkungslos | Kein Importance-Trigger, keine Frage-Generierung, Reflexionen fließen nicht zurück in Entscheidungen |
| **Planning** — hierarchische Zerlegung: Tagesplan → Stundenblöcke → 5-15 Min Aktionen; reaktives Umplanen bei unerwarteten Ereignissen | 🟡 plan_daily() existiert, aber keine temporale Zerlegung | ⚠️ Grundlegend | Keine Stunden-/Minutenblöcke, kein reaktives Umplanen; Fast-Path überschreibt Plan zu ~80% |
| **Observation** — strukturierte Weltwahrnehmung mit Aufmerksamkeitssteuerung | ✅ WorldObserver (80 Blöcke, Biome, Entities) | ✅ Gut | — |
| **Agent-Konversation** — Agenten initiieren/führen/beenden Gespräche basierend auf Beziehungen | ❌ Nicht implementiert (Single-Agent) | ❌ Fehlt | Voraussetzung für Multi-Agent (Phase 4) |
| **Emergente soziale Dynamiken** — Informationsdiffusion, spontane Events (z.B. Valentine's Day Party) | ❌ Nicht möglich (Single-Agent) | ❌ Fehlt | Erst mit Multi-Agent + funktionierendem Planning testbar |

### PIANO / Project Sid (Altera.AL 2024): Paper vs Implementation

| Paper Concept | Unsere Implementierung | Status | Lücke |
|--------------|----------------------|--------|-------|
| **Cognitive Controller (CC)** — Informations-Bottleneck für kohärente Entscheidungen; konditioniert alle Output-Module | ✅ Synthesiert Perception + Memory + Goals | 🟡 Teilweise | CC wird zu oft vom Reflex Layer umgangen; kein "strong conditioning" der Output-Module |
| **10 parallele Module** — Memory, Action Awareness, Goal Generation, Social Awareness, Talking, Skill Execution + 4 weitere, laufen auf verschiedenen Zeitskalen | 🟡 ~6 Module vorhanden, sequentiell gepollt | ⚠️ Grundlegend | Module nicht wirklich parallel; asyncio-Struktur vorhanden aber ungenutzt |
| **Action Awareness** — Soll/Ist-Vergleich, verhindert Halluzinations-Kaskaden | ✅ Lernt aus Diskrepanzen, blacklistet Wiederholungsfehler | ✅ Gut | — |
| **Social Awareness** — gerichteter Sentiment-Graph (Pearson r=0.807 bei 5+ Beobachtern); asymmetrische Beziehungen | 🟡 Datenstruktur existiert, ungenutzt (Single-Agent) | ⚠️ Skeleton | Kein Sentiment-Tracking, keine Beziehungsdynamik |
| **Skill Execution** — Mining, Crafting, Smelting, Animal Husbandry, Combat, Navigation, Trading | ✅ 9 Aktionstypen, Tool-aware Mining, Block-Animation, Crafting-Chains | ✅ Sehr gut | Smelting + Animal Husbandry + Trading fehlen |
| **Goal Generation** — soziale + individuelle Ziele alle 5-10s basierend auf Beobachtung anderer | ✅ 2 Zieltypen (economic, survival) | 🟡 Teilweise | Keine sozialen Ziele, keine Beobachtung anderer Agenten |
| **Talking Module** — Sprach-Interpretation und -Generierung für Inter-Agent-Kommunikation | ❌ Nicht implementiert | ❌ Fehlt | Voraussetzung für Social Awareness + Collective Rules |
| **Role Specialization** — Rollen emergieren aus sozialen Zielen + 5-Goal-Window (Farmer, Miner, Guard, Builder, Explorer...) | ❌ Nicht implementiert | ❌ Phase 4 | Benötigt Social Awareness + Multi-Agent |
| **Collective Rules** — Verfassung, Abstimmung (bei t=420s), Steuern (20%), 25 Constituents + 3 Influencer + 1 Election Manager | ❌ Nicht implementiert | ❌ Phase 4 | Benötigt Talking + Multi-Agent |
| **Cultural Transmission** — Meme-Propagation (Konversation → Keywords), Religion (Pastafarianism-Experiment: Priester → direkte + indirekte Konvertiten) | ❌ Nicht implementiert | ❌ Phase 4 | Benötigt Talking + Social Awareness |
| **Skalierung** — 500 Agenten / 9000s, bis 1000+ | ❌ Nur 1 Agent | ❌ Phase 4 | Architektur-Engpass: ein Python-Prozess pro Agent |

### Gesamtbewertung

| Bereich | Abdeckung | Anmerkung |
|---------|-----------|-----------|
| **Generative Agents** | ~45% | Memory Stream + Observation gut; Retrieval/Reflection/Planning nur als Skelett; kein Multi-Agent |
| **Project Sid / PIANO** | ~30% | Action Awareness + Skill Execution stark; CC vorhanden aber untergraben; Social/Collective/Cultural komplett offen |
| **Gesamt-Zielerreichung** | ~35% | Solide Single-Agent-Grundlage, aber die Paper-definierten Kernmechanismen (Importance-basiertes Retrieval, hierarchisches Planning, Inter-Agent-Kommunikation) fehlen oder sind wirkungslos |

### Kritische Befunde

1. **Reflex Layer dominiert LLM-Pipeline (~80/20)**: Die Generative Agents Ablation-Studie zeigt, dass Planning, Reflection und Observation *jeweils kritisch* für glaubwürdiges Verhalten sind. Unser Fast-Path umgeht diese Komponenten systematisch, was die Architektur de facto zu einem regelbasierten System mit gelegentlichem LLM-Aufruf reduziert.

2. **Retrieval ist der größte Einzelmangel**: Ohne Importance-Scoring (Poignancy 1-10), ohne exponentielles Decay, ohne gewichtete Kombination mit Embedding-Relevanz kann der Agent keine kontextuell passenden Erinnerungen abrufen. Das Paper nutzt `score = α·recency + β·relevance + γ·importance` — wir nutzen nur "die letzten 3".

3. **Reflection ohne Rückkopplung**: Die Reflection-Klasse erzeugt Einsichten, aber diese fließen nicht zurück in den Entscheidungsprozess. Im Paper bilden Reflexionen einen Baum (Beobachtungen → Reflexionen → Meta-Reflexionen), der bei Retrieval bevorzugt wird.

4. **Planning ohne temporale Struktur**: `plan_daily()` erzeugt einen Tagesplan, aber keine Stunden-/Minutenblöcke. Der Plan wird nicht reaktiv angepasst, und der Fast-Path überschreibt ihn in den meisten Fällen.

5. **Single-Agent-Limit**: Sowohl Generative Agents (25 Agenten) als auch Project Sid (500-1000+) definieren Multi-Agent-Interaktion als Kern des Systems. Ohne Multi-Agent können Social Awareness, Collective Rules, Cultural Transmission und emergente Dynamiken nicht entstehen.

### Empfehlungen (Priorität)

1. **Retrieval-Formel implementieren** (Phase 2 Completion): Importance-Scoring via LLM, exponential decay, gewichtete Kombination → größter Impact auf Agent-Qualität
2. **Planning hierarchisch machen** (Phase 2 Completion): Tagesplan → Stundenblöcke → 5-15 Min Aktionen; Fast-Path nur für Notfälle (Mob-Angriff, Nacht)
3. **Reflection in Retrieval einbinden** (Phase 2 Completion): Reflexionen als hochwertige Memories in Retrieval einbeziehen
4. **Fast-Path reduzieren** (Phase 3→4 Übergang): Nur für unmittelbare Gefahren beibehalten, Rest über LLM-Pipeline
5. **Talking Module + Multi-Agent** (Phase 4 Start): Voraussetzung für alle Civilization-Features

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

### Phase 2: Core Agent Architecture (Generative Agents) 🟡 60%
6. ✅ Perception module — capture world state (80 blocks, radius 12)
7. ✅ Memory Stream — associative memory with embeddings (SQLite + ChromaDB)
8. 🟡 Retrieval — nur n=3 letzte Memories; **fehlt**: Importance-Scoring (Poignancy 1-10), exponential decay, gewichtete Formel (α·recency + β·relevance + γ·importance)
9. 🟡 Planning — plan_daily() existiert, **fehlt**: temporale Zerlegung (→ Stunden → 5-15 Min), reaktives Umplanen; Fast-Path überschreibt Plan ~80%
10. 🟡 Reflection — Klasse vorhanden (~18 Zyklen/Session), **fehlt**: Importance-Trigger (∑importance > 150), Frage-Generierung, Rückkopplung in Retrieval/Entscheidungen
11. ✅ Skill Execution — 9 Aktionstypen (move_to, mine_block, place_block, craft auto-chain, attack_mob, collect_items, equip, teleport, idle). Tool-aware Mining. Block-Breaking Animation.
12. ✅ Agent loop — perceive → controller → plan → reflect → execute

### Phase 3: PIANO Enhancements (Project Sid) 🟡 40%
13. 🟡 Concurrent module execution — asyncio-Struktur vorhanden, Module aber sequentiell gepollt (Paper: parallel auf verschiedenen Zeitskalen)
14. 🟡 Cognitive Controller — Bottleneck vorhanden, wird aber vom Reflex Layer zu oft umgangen (Paper: CC "strongly conditions" alle Output-Module)
15. ✅ Action Awareness — compare expected vs actual outcomes, blacklist repeat failures
16. 🟡 Social Awareness — directed sentiment graph (Skeleton, ungenutzt — single agent)
17. 🟡 Goal Generation — 2 Zieltypen (economic, survival); **fehlt**: soziale Ziele basierend auf Beobachtung anderer Agenten

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
- **Phase 2**: 🟡 Agent performs full day-night cycle, executes Minecraft actions; Retrieval/Planning/Reflection als Skelett vorhanden aber nicht paper-konform (keine Importance-Formel, keine temporale Zerlegung, keine Reflexions-Rückkopplung)
- **Phase 3**: 🟡 Controller + ActionAwareness + GoalGen funktional; CC wird vom Reflex Layer untergraben; Module nicht wirklich parallel; SocialAwareness ungenutzt
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
