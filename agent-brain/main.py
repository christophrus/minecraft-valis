"""
Project Valis - Agent Brain Service
Main entry point. Starts the FastAPI server and WebSocket bridge client.

The agent brain service receives perception data from Minecraft agents,
runs cognitive processing (memory, planning, reflection), and sends
actions back to the Minecraft server.
"""

import asyncio
import logging
import os
import signal
import sys

# Make agent-brain/ importable from any working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
