# Project Valis

> A Minecraft server where generative AI agents collaboratively build an emergent AI civilization.

Based on the research from:

- **Generative Agents: Interactive Simulacra of Human Behavior** (Park et al., 2023) — [arXiv:2304.03442](https://arxiv.org/abs/2304.03442)
- **Project Sid: Many-agent simulations toward AI civilization** (Altera.AL, 2024) — [arXiv:2411.00114](https://arxiv.org/abs/2411.00114)

## Status

Multiple LLM-driven agents now run a **cooperative civilization loop**: a Village Council assigns
role-based tasks → agents gather resources → deposit into a shared village chest → other agents
withdraw and build. In a sustained multi-hour run the agents progressed through the full tech tree
(wood → tools → stone → iron), built **8 shelters** — including one the LLM designed itself via the
blueprint system — and ran 32 council sessions with 60+ shared-chest deposits.

The core PIANO principle is preserved throughout: **the LLM decides, the code never hard-codes the
decision** — heuristics only provide neutral facts and carry out intentions the agent already formed.

See [PLAN.md](PLAN.md) for the full architecture review and phased roadmap.

## Architecture

A **PaperMC Java server plugin** manages NPC agents in Minecraft (via Citizens2). A **Python agent
brain service** runs the cognitive architecture — memory, planning, reflection, social awareness,
the PIANO cognitive controller — powered by LLMs (OpenAI, Anthropic, or local models). The two
communicate over WebSocket.

**[→ Setup & Testanleitung (Phase 1)](SETUP.md)**

## Quick Start

### Prerequisites

- JDK 21+
- Python 3.11+
- PaperMC (latest) — auto-downloaded on first server run

### Setup

```bash
# 1. Clone & enter
git clone <repo-url> && cd minecraft-valis

# 2. Start PaperMC server (downloads server jar on first run)
cd server && java -jar paper.jar nogui

# 3. Set up Python agent brain
cd ../agent-brain
python -m venv .venv
.venv\Scripts\activate  # or source .venv/bin/activate
pip install -e .

# 4. Configure LLM keys
cp .env.example .env
# Edit .env with your API keys

# 5. Launch agent brain
python main.py
```

### Building the plugin

```bash
cd plugin
JAVA_HOME=/path/to/zulu-21 ./gradlew shadowJar
# copy build/libs/valis-core-0.1.0-SNAPSHOT.jar → server/plugins/
```

## Project Structure

```
minecraft-valis/
├── PLAN.md                    # Full architecture review & roadmap
├── README.md                  # This file
├── server/                    # PaperMC server + plugins
├── plugin/                    # Gradle-based PaperMC plugin (Java)
├── agent-brain/               # Python agent brain service
│   ├── agent.py               # ValisAgent + AgentManager + Settlement
│   ├── cognitive/             # controller, planning, reflection, execution, …
│   ├── memory/                # memory stream + weighted retrieval
│   └── config/                # spawn roster, agent personalities
└── .gitignore
```

## License

MIT
