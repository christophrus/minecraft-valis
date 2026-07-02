"""
Valis Dashboard — live web view of the AI civilization.

Runs a small FastAPI app inside the brain service's asyncio loop:
  GET /            → single-page dashboard (index.html)
  GET /api/state   → JSON snapshot of agents, settlement, chronicle, events, LLM usage

The event feed is captured via a logging ring buffer — zero changes to agent code.
"""

import logging
import os
import time
from collections import deque

logger = logging.getLogger("valis.dashboard")

# --- Event ring buffer: captures INFO+ valis.* log records for the feed ---

_EVENTS: deque = deque(maxlen=300)

_FEED_KEYWORDS = (
    "COUNCIL", "CHRONICLE", "SETTLEMENT", "BLUEPRINT", "FAST-BUILD", "BUILD:",
    "action result", "chat:", "spawned", "despawned", "recruiting",
    "REFLECTION: stored", "APM:", "learned:",
)


class _EventFeedHandler(logging.Handler):
    """Collect notable valis.* INFO+ records into a ring buffer."""

    def emit(self, record: logging.LogRecord):
        try:
            if not record.name.startswith("valis"):
                return
            msg = record.getMessage()
            if record.levelno >= logging.WARNING or any(k in msg for k in _FEED_KEYWORDS):
                _EVENTS.append({
                    "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                    "level": record.levelname,
                    "msg": msg[:300],
                })
        except Exception:
            pass  # never let the feed break logging


def install_event_feed():
    handler = _EventFeedHandler()
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)


# --- State snapshot ---

def _agent_snapshot(agent) -> dict:
    p = agent._pending_perception
    decision = getattr(agent, "_cached_decision", None)
    pos, inv, health, biome = {}, {}, None, ""
    if p:
        pos = {k: int(v) for k, v in p.position.items()}
        inv = {k: v for k, v in p.inventory.items() if v > 0}
        health = p.health
        biome = p.biome
    return {
        "name": agent.name,
        "personality": agent.personality,
        "traits": agent.traits,
        "tick": agent.tick_count,
        "position": pos,
        "biome": biome,
        "health": health,
        "inventory": inv,
        "intent": getattr(decision, "intent", "") if decision else "",
        "reason": getattr(decision, "reason", "") if decision else "",
        "action_hint": getattr(decision, "action_hint", "") if decision else "",
        "priority": getattr(decision, "priority", 0) if decision else 0,
        "council_assignment": getattr(agent, "_council_assignment", "") or "",
        "current_task": getattr(agent.planner, "current_task", "") or "",
        "goals": list(agent.goals[:3]),
        "beliefs": [{"text": b.get("text", ""), "source": b.get("source", "")}
                    for b in (getattr(agent, "beliefs", None) or [])],
    }


def build_state(manager) -> dict:
    s = manager.settlement
    settlement = {
        "center": list(s.center) if s.center else None,
        "shelters_built": s.shelters_built,
        "shelter_positions": [list(x) for x in s.shelter_positions],
        "crafting_tables": [list(x) for x in s.crafting_tables],
        "chest": dict(s.village_chest),
        "chronicle": list(s.chronicle),
        "rules": list(getattr(s, "rules", [])),
        "pending_requests": [
            {"from": r.get("from", "?"), "item": r.get("item", "?")}
            for r in s.pending_requests
            if time.time() - r.get("time", 0) < 300
        ],
    }
    try:
        from llm.providers import get_session_token_totals
        llm = get_session_token_totals()
    except Exception:
        llm = {}
    return {
        "timestamp": time.strftime("%H:%M:%S"),
        "population": len(manager.agents),
        "max_villagers": _get_max_villagers(),
        "agents": [_agent_snapshot(a) for a in manager.agents.values()],
        "settlement": settlement,
        "events": list(_EVENTS)[-120:][::-1],  # newest first
        "llm": llm,
    }


def _get_max_villagers() -> int:
    try:
        from agent import MAX_VILLAGERS
        return MAX_VILLAGERS
    except Exception:
        return 6


# --- FastAPI app ---

def create_app(manager):
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, JSONResponse

    app = FastAPI(title="Valis Dashboard", docs_url=None, redoc_url=None)
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

    @app.get("/")
    async def index():
        return FileResponse(html_path, media_type="text/html")

    @app.get("/api/state")
    async def state():
        try:
            return JSONResponse(build_state(manager))
        except Exception as e:
            logger.warning(f"DASHBOARD: state snapshot failed: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    return app


async def run_dashboard(manager, host: str = "127.0.0.1", port: int = 8765):
    """Run the dashboard server inside the existing asyncio loop."""
    import uvicorn
    install_event_feed()
    config = uvicorn.Config(
        create_app(manager), host=host, port=port,
        log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info(f"DASHBOARD: live at http://{host}:{port}")
    await server.serve()
