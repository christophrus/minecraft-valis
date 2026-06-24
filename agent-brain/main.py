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
        except (ValueError, OSError):
            os.remove(_PID_FILE)  # stale lock

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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
# Silence noisy library debug logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)


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
    await bridge.connect()

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

    await stop_event.wait()
    tick_task.cancel()
    await bridge.disconnect()
    logger.info("Agent brain service stopped.")


if __name__ == "__main__":
    asyncio.run(main())
