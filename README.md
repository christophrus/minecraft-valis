"# Project Valis

> A Minecraft server where generative AI agents collaboratively build an emergent AI civilization.

Based on the research from:

- **Generative Agents: Interactive Simulacra of Human Behavior** (Park et al., 2023) — [arXiv:2304.03442](https://arxiv.org/abs/2304.03442)
- **Project Sid: Many-agent simulations toward AI civilization** (Altera.AL, 2024) — [arXiv:2411.00114](https://arxiv.org/abs/2411.00114)

## Architecture

A **PaperMC Java server plugin** manages NPC agents in Minecraft. A **Python agent brain service** runs the cognitive architecture — memory, planning, reflection, social awareness — powered by LLMs (OpenAI, Anthropic, or local models). The two communicate via WebSocket.

See [PLAN.md](PLAN.md) for the full implementation roadmap.

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

## Project Structure

```
minecraft-valis/
├── PLAN.md                    # Full implementation plan
├── server/                    # PaperMC server
├── plugin/                    # Gradle-based PaperMC plugin (Java)
├── agent-brain/               # Python agent service
└── .gitignore
```

## License

MIT" 
