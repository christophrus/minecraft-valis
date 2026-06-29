"""
Project Valis - Agent Brain Service
Main entry point. Starts the FastAPI server and WebSocket bridge client.

The agent brain service receives perception data from Minecraft agents,
runs cognitive processing (memory, planning, reflection), and sends
actions back to the Minecraft server.
"""

import asyncio
import atexit
import logging
import os
import signal
import sys

# Make agent-brain/ importable from any working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- PID lock: prevent double starts ---
_PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".brain.pid")

def _check_pid_lock():
    if os.path.exists(_PID_FILE):
        with open(_PID_FILE) as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            print(f"ERROR: Brain already running (PID {old_pid}). Stop it first.")
            sys.exit(1)
        except (ValueError, OSError, SystemError):
            os.remove(_PID_FILE)  # stale lock or Windows error

def _write_pid_lock():
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def _remove_pid_lock():
    try:
        os.remove(_PID_FILE)
    except OSError:
        pass

_check_pid_lock()
_write_pid_lock()
atexit.register(_remove_pid_lock)

from dotenv import load_dotenv
load_dotenv()

from bridge.client import BridgeClient
from agent import AgentManager

logger = logging.getLogger("valis")
logger.setLevel(logging.DEBUG)  # Let DEBUG through to file handler only

# Console: INFO+ only, no DEBUG noise
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logging.getLogger().addHandler(_console)
logging.getLogger().setLevel(logging.DEBUG)  # Root allows DEBUG to reach file handler
# Silence noisy library debug logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

# --- Debug session log file (DEBUG level, timestamped, valis-only) ---
from datetime import datetime

_debug_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_logs")
os.makedirs(_debug_log_dir, exist_ok=True)
_debug_log_path = os.path.join(
    _debug_log_dir,
    f"debug-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
)

_debug_handler = logging.FileHandler(_debug_log_path, encoding="utf-8")
_debug_handler.setLevel(logging.DEBUG)
_debug_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
))

class ValisDebugFilter(logging.Filter):
    """Only let valis.* loggers through to debug file."""
    def filter(self, record):
        return record.name.startswith("valis")

_debug_handler.addFilter(ValisDebugFilter())
logging.getLogger().addHandler(_debug_handler)

logger.info(f"Debug session log: {_debug_log_path}")


async def main():
    """Main entry point. Connects to Minecraft server and starts agent loop."""
    ws_host = os.getenv("VALIS_WS_HOST", "localhost")
    ws_port = int(os.getenv("VALIS_WS_PORT", "9876"))

    logger.info("=== Project Valis: Agent Brain Service ===")
    logger.info(f"Connecting to Minecraft server at {ws_host}:{ws_port}")

    manager = AgentManager()
    bridge = BridgeClient(manager, ws_host, ws_port)
    manager.set_bridge(bridge)

    # Start agent tick loop FIRST (before blocking connect)
    tick_task = asyncio.create_task(manager.run_tick_loop())

    # Connect to Minecraft WebSocket (this runs the message loop)
    try:
        await bridge.connect()
    except asyncio.CancelledError:
        logger.info("WebSocket connection cancelled")
        tick_task.cancel()
        return

    # Handle shutdown gracefully
    stop_event = asyncio.Event()

    def shutdown():
        logger.info("Shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        tick_task.cancel()
        try:
            await bridge.disconnect()
        except Exception:
            pass
        # Log LLM token usage summary
        try:
            from llm.providers import log_session_summary
            log_session_summary()
        except Exception:
            pass
        logger.info("Agent brain service stopped.")


if __name__ == "__main__":
    asyncio.run(main())
