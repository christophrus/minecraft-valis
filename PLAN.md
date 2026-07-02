# Project Valis: Minecraft AI Civilization

> A PaperMC server where 20-100 generative AI agents inhabit a Minecraft world, using a synthesized architecture from "Generative Agents: Interactive Simulacra of Human Behavior" (Park et al., 2023) and "Project Sid: Many-agent simulations toward AI civilization" (Altera.AL, 2024).

## References

- **Generative Agents**: https://arxiv.org/abs/2304.03442 — Memory Stream, Reflection, Planning, Observation
- **Project Sid / PIANO**: https://arxiv.org/abs/2411.00114 — PIANO Architecture (Parallel Information Aggregation via Neural Orchestration), Cognitive Controller, Social Awareness, Civilization mechanics

---

## Milestone — Emergent Civilization Loop (2026-07-01)

**Multi-Agent ist live.** 3 personalisierte Agenten (MinerBob, BuilderAlice, ScoutCarol) mit Traits und Rollen kooperieren über eine geteilte Ökonomie. In einem mehrstündigen Lauf (session 20260630-231553, ~4h, 979 Ticks) hat sich der vollständige Zivilisations-Kreislauf geschlossen:

> **Village Council** weist rollenbasierte Aufgaben zu → Agent **sammelt** Holz/Stein → **deponiert** in den geteilten Dorf-Chest → anderer Agent **entnimmt** Planks und **baut** ein Shelter.

Messbare Ergebnisse in diesem Lauf:

| Metrik | Ergebnis |
|--------|----------|
| Baumstämme gesammelt | 30 (oak + cherry) |
| Tech-Tree | Holz → Werkzeuge (4× Pickaxe, Schwert, Axt) → **Stein + Eisen** |
| Platzierte Blöcke | **166** (dirt, planks, cobblestone, torch) |
| Gebaute Shelter | **8 registriert** (u.a. eines vom LLM selbst designt) |
| Chest-Ökonomie | 60 Deposits + 17 Withdrawals |
| Council-Sessions | 32 |

**Der Weg dorthin — drei Kernbefunde:** Das PIANO-Hirn war nie der Engpass. Die Blocker waren jedes Mal der *mechanische Untergrund*, der die vom LLM bereits gefassten Absichten ins Leere laufen ließ:

1. **Perception-Blindheit** — die 80-Block-Wahrnehmung füllte sich mit Dirt/Stone, bevor ein Log/Erz erreicht wurde. → Split in 60 reguläre + 20 High-Value-Slots (Logs/Ores immer sichtbar).
2. **Vergrabener Chest** — Center/Chest hartcodiert auf y=64, Agenten stehen aber auf y=78-Hügeloberfläche → Chest 14 Blöcke unter der Erde, unerreichbar. → Plugin snapt den Chest auf die echte Oberfläche (`getHighestBlockYAt`) und meldet die Position zurück; das Hirn synct den Center.
3. **Leash Catch-22** — die einzigen Bäume liegen 70+ Blöcke entfernt in Bergen; der feste 60-Block-Leash zog leerhändige Agenten zurück, *bevor* sie in Sichtweite eines Baums kamen. → Adaptiver Leash: 110 Blöcke solange leerhändig sammelnd, 60 sobald Beute getragen wird (dann heimkehren).

Alle drei Fixes sind PIANO-konform: reine Wahrnehmungs-/Navigations-Mechanik, keine Entscheidungs-Overrides.

**Verbleibende Rauheiten (Feinschliff, keine Blocker):** Churn (Agenten minen gelegentlich eigene Crafting-Tables/Chests wieder ab), gelegentlich kaputtes Blueprint-JSON vom LLM, Shelter sind funktionale 3×3-Hütten statt Mehrraum-Strukturen, ~25 Deposit-Fails/Lauf verbleiben.

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

### Gesamtbewertung (nach Implementierung 2026-06-25)

| Bereich | Abdeckung | Anmerkung |
|---------|-----------|-----------|
| **Generative Agents** | ~70% | Memory Stream + Observation + Retrieval (gewichtet) + Reflection (LLM) + hierarchisches Planning implementiert; fehlt: reaktives Umplanen, Multi-Agent-Konversation |
| **Project Sid / PIANO** | ~45% | Action Awareness + Skill Execution + CC (mit Retrieval/Reflections) stark; Social/Collective/Cultural komplett offen (Single-Agent) |
| **Gesamt-Zielerreichung** | ~55% | Paper-konforme Kernarchitektur (Importance-Scoring, gewichtetes Retrieval, LLM-Reflections, hierarchisches Planning) jetzt implementiert; nächster Engpass: Multi-Agent |

### Kritische Befunde

1. ~~**Reflex Layer dominiert LLM-Pipeline (~80/20)**~~ → ✅ **BEHOBEN**: Fast-Path jetzt nur bei `priority ≥ 0.7` (Gefahr), Crafting, oder Stuck. LLM-Planner ist Primärpfad. Erwartete Aufteilung: ~50/50 LLM/Fast-Path.

2. ~~**Retrieval ohne Importance-Scoring**~~ → ✅ **BEHOBEN**: LLM-basiertes Importance-Scoring (Poignancy 1-10, normiert auf 0-1) bei Memory-Erstellung. Controller + Planner nutzen gewichtetes Retrieval `(α·recency + β·relevance + γ·importance)`.

3. ~~**Reflection ohne Rückkopplung**~~ → ✅ **BEHOBEN**: Reflections werden als "thought"-Nodes mit LLM-gescorerter Importance gespeichert. Controller lädt die letzten 3 Reflections explizit. Focal Points werden via LLM generiert.

4. ~~**Planning ohne temporale Struktur**~~ → ✅ **TEILWEISE BEHOBEN**: Hierarchisches Planning (## Goal → Sub-Tasks). `hourly_tasks` + `advance_task()`. **Offen**: reaktives Umplanen bei unerwarteten Ereignissen.

5. **Single-Agent-Limit** (OFFEN): Sowohl Generative Agents (25 Agenten) als auch Project Sid (500-1000+) definieren Multi-Agent-Interaktion als Kern des Systems. Ohne Multi-Agent können Social Awareness, Collective Rules, Cultural Transmission und emergente Dynamiken nicht entstehen.

### Implementierte Verbesserungen (2026-06-25)

| Empfehlung | Status | Implementierung |
|-----------|--------|----------------|
| **1. Importance-Scoring via LLM** | ✅ Implementiert | `memory_stream.py`: `score_importance()` ruft LLM auf (Poignancy 1-10, normiert auf 0-1); Fallback auf Keyword-Heuristik. `agent.py`: `_score_importance_llm()` als Provider-Funktion. |
| **2. Controller nutzt Retrieval + Reflections** | ✅ Implementiert | `controller.py`: `decide()` nutzt jetzt `agent.retrieval.retrieve()` (gewichtet: recency × relevance × importance) statt `get_recent(n=3)`; bindet Reflection-Insights + Daily Plan ein. |
| **3. Hierarchisches Planning** | ✅ Implementiert | `planning.py`: `plan_daily()` erzeugt hierarchischen Plan (## Goal → Sub-Tasks); neues `hourly_tasks`-Feld; `advance_task()` für Task-Progression; `_parse_hierarchical_plan()`. |
| **4. Fast-Path reduziert** | ✅ Implementiert | `agent.py`: Fast-Path nur bei `priority >= 0.7` (Gefahr), `craft`-Hint, oder Stuck; sonst LLM-Planner als Primärpfad; Fast-Path als letzter Fallback. |
| **5. Reflection mit LLM** | ✅ Implementiert | `reflection.py`: `_generate_focal_points_llm()` generiert Fragen via LLM; Insights werden mit LLM-gescorerter Importance gespeichert; Threshold auf 50.0 erhöht. |

### Überprüfbare Ziele

| Ziel | Metrik | Akzeptanzkriterium | Prüfmethode |
|------|--------|-------------------|-------------|
| **LLM-Nutzung steigt** | % Ticks mit LLM-Planner vs Fast-Path | ≥ 50% LLM-basierte Aktionen (vorher ~20%) | Debug-Log: `LLM-PATH:` vs `FAST-PATH:` Einträge zählen |
| **Importance-Varianz** | Std-Abweichung der Memory-Importance-Scores | σ > 0.15 (vorher 0.0, alle Werte 0.5) | SQLite: `SELECT AVG(importance), STDEV(importance) FROM nodes` |
| **Reflection-Qualität** | Reflections enthalten Bezug zu konkreten Erfahrungen | ≥ 60% der Insights referenzieren spezifische Items/Orte | Debug-Log: `REFLECTION: stored insight` Einträge prüfen |
| **Plan-Befolgung** | Agent führt Tasks aus dem Daily Plan aus | ≥ 3 Tasks pro Tagesplan werden tatsächlich bearbeitet | Debug-Log: Korrelation zwischen `daily plan:` und ausgeführten Aktionen |
| **Kohärenz** | Agent wiederholt nicht dieselbe gescheiterte Aktion | < 5% Wiederholung von blacklisted Actions | Debug-Log: `blacklisted` Einträge nach Implementierung |

### Verbleibende Empfehlungen

1. **Talking Module + Multi-Agent** (Phase 4 Start): Voraussetzung für alle Civilization-Features
2. **Task-Advancing**: Agent sollte automatisch zum nächsten hourly_task wechseln, wenn aktueller Task erledigt ist (basierend auf Action-Awareness-Feedback)
3. **Reactive Replanning**: Wenn unerwartetes Ereignis eintritt (Mob-Angriff, neues Biom), Plan automatisch aktualisieren

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

### Phase 2: Core Agent Architecture (Generative Agents) ✅ 80%
6. ✅ Perception module — capture world state (80 blocks, radius 12)
7. ✅ Memory Stream — associative memory with embeddings (SQLite + ChromaDB) + LLM-basiertes Importance-Scoring (Poignancy 1-10)
8. ✅ Retrieval — gewichtete Formel (α·recency + β·relevance + γ·importance) mit exponential decay; Controller + Planner nutzen Retrieval-Modul
9. 🟡 Planning — hierarchischer Plan (Tagesplan → Sub-Tasks) implementiert; **fehlt**: reaktives Umplanen bei unerwarteten Ereignissen
10. ✅ Reflection — LLM-basierte Focal Points, Importance-gescorte Insights, Rückkopplung in Controller-Entscheidungen via Retrieval
11. ✅ Skill Execution — 9 Aktionstypen (move_to, mine_block, place_block, craft auto-chain, attack_mob, collect_items, equip, teleport, idle). Tool-aware Mining. Block-Breaking Animation.
12. ✅ Agent loop — perceive → controller → plan → reflect → execute; LLM-first mit Fast-Path nur für Notfälle

### Phase 3: PIANO Enhancements (Project Sid) 🟡 65%
13. 🟡 Concurrent module execution — asyncio-Struktur vorhanden, Module aber sequentiell gepollt (Paper: parallel auf verschiedenen Zeitskalen)
14. ✅ Cognitive Controller — Bottleneck mit gewichtetem Retrieval + Reflections + Plan-Kontext; Fast-Path auf Notfälle reduziert
15. ✅ Action Awareness — compare expected vs actual outcomes, blacklist repeat failures
16. 🟡 Social Awareness — Agenten hören + verarbeiten Chat aller anderen (`nearby_chat`), Chat→Action-Pipeline routet Requests; **fehlt**: gerichteter Sentiment-Graph, Beziehungsdynamik
17. 🟡 Goal Generation — economic + survival + Council-Assignments; **fehlt**: eigenständig generierte soziale Ziele aus Beobachtung anderer

### Phase 4: Multi-Agent Civilization 🟡 45%
18. ✅ Personality & Trait system — `spawn_roster.yaml` / `agents.yaml`: Traits + initial_goals pro Agent (determined/creative/adventurous …)
19. ✅ Multi-agent orchestration — 3 Agenten laufen parallel, je eigener Cognitive Loop; `AgentManager` + `reconcile_roster`
20. ✅ Role specialization — Miner / Builder / Explorer, config-getrieben (noch nicht emergent aus sozialen Zielen)
21. 🟡 Collective coordination — **Village Council**: Meta-LLM weist alle ~30 Ticks strategische, rollenbasierte Aufgaben zu (PIANO-konform, sieht globalen State); **fehlt**: Verfassung, Abstimmung, Steuern
22. 🟡 Cultural transmission — **Dorf-Chronik**: der Council schreibt persistente Geschichte (`data/village_chronicle.md`, überlebt Neustarts); die letzten Einträge fließen in jeden Agenten-Prompt als geteiltes kulturelles Gedächtnis; **fehlt**: Memes, Religion, Werte-Propagation zwischen Agenten
23. 🟡 Economy system — **geteilter Dorf-Chest**: deposit/withdraw, Round-Trip-Missionen, Chat→Action-Requests; funktionierender Sammel→Teilen→Bauen-Loop; **fehlt**: Währung, Handel, Marktplätze
24. ✅ Emergent building — **LLM-Blueprint-System**: Agenten entwerfen eigene Gebäude aus verfügbarem Material (`_generate_blueprint`); Fallback auf 3×3-Shelter; 8 Shelter in einem Lauf gebaut
25. ✅ Population growth — der Council darf bei florierendem Dorf **neue Dorfbewohner rekrutieren** (`RECRUIT`): Name, Rolle (farmer/guard/trader/artist/priest/…) und Gründungszweck vom LLM erfunden; Spawn am Dorfzentrum; Cap `MAX_VILLAGERS=6`; jede Rekrutierung wird in der Chronik verewigt

### Phase 5: Observability & Polish 🟡 30%
24. 🔲 Web dashboard
25. 🔲 Configuration system (YAML/JSON)
26. 🟡 Debug logging (comprehensive: NAV tracking, stuck detection, emergency help, action results, inventory snapshots)
27. 🔲 Performance optimization

## Verification Criteria

- **Phase 1**: ✅ Server starts, plugin loads, WebSocket connects, single NPC spawns
- **Phase 2**: ✅ Paper-konforme Kernarchitektur: LLM Importance-Scoring, gewichtetes Retrieval (recency×relevance×importance), LLM-Reflection mit Focal Points, hierarchisches Planning; 🟡 fehlt: reaktives Umplanen
- **Phase 3**: 🟡 CC mit Retrieval+Reflections+Plan; Fast-Path auf Notfälle reduziert; SocialAwareness ungenutzt (Single-Agent); Module sequentiell statt parallel
- **Phase 4**: 🟡 3 Agenten koexistieren, spezialisieren in Rollen, kooperieren über geteilte Ökonomie + Village Council, bauen 8 Shelter (Sammel→Teilen→Bauen-Loop validiert); 🔲 offen: Governance (Verfassung/Voting), Cultural Transmission, Währung
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

### Multi-Agent & Civilization Features (2026-06-30 → 07-01)

| Feature | Purpose | Paper Reference |
|---------|---------|----------------|
| **Village Council** | Meta-LLM sieht globalen State, weist alle ~30 Ticks rollenbasierte Aufgaben zu | Collective Rules (Sid) |
| **Shared Village Chest** | Geteilte Ökonomie: deposit/withdraw; auf echte Oberfläche gesnapt | Economy (Sid) |
| **Round-Trip Missions** | Adaptiver Leash (110 leerhändig / 60 mit Beute) — sammeln fern, heimbringen | Goal Generation |
| **Chat→Action Pipeline** | Settlement parst Chat ("I need cobblestone") und routet Requests an andere Agenten | Talking Module (Sid) |
| **LLM Blueprint System** | Agenten entwerfen eigene Gebäude aus verfügbarem Material (statt fixem Muster) | Skill Execution / Emergence |
| **Split Perception Cap** | 60 reguläre + 20 High-Value-Slots — Logs/Ores nie von Dirt verdrängt | Observation Filter (GA) |
| **Surface-Snapped Chest** | `getHighestBlockYAt` verhindert vergrabenen, unerreichbaren Chest | Infrastructure |
| **Settlement State** | Geteilter Center/Status/Chest/Requests über alle Agenten | Collective Memory |
| **Village Chronicle** | Persistente Dorfgeschichte, vom Council-LLM geschrieben, überlebt Neustarts, in jedem Agenten-Prompt | Cultural Transmission (Sid) |
| **Council Recruitment** | LLM entscheidet, wann das Dorf wächst — erfindet Name, Rolle, Gründungszweck neuer Bewohner | Civilization Growth (Sid) |

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
