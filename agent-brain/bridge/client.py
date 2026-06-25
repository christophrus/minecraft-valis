"""
WebSocket client that connects to the Minecraft plugin bridge.
Routes incoming messages to the appropriate agent handlers
and sends outgoing actions back to the server.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed

from .protocol import parse_message, PerceptionData, ActionResult, AgentAction, AgentChat, SpawnRequest, DespawnRequest

if TYPE_CHECKING:
    from ..agent import AgentManager

logger = logging.getLogger("valis.bridge")


class BridgeClient:
    """
    WebSocket client for communicating with the Minecraft Valis plugin.
    """

    def __init__(self, manager: "AgentManager", host: str, port: int):
        self.manager = manager
        self.uri = f"ws://{host}:{port}"
        self._ws: "websockets.WebSocketClientProtocol | None" = None
        self._connected = False
        self._reconnect_task: asyncio.Task | None = None

    async def connect(self):
        """Connect to the Minecraft WebSocket server with auto-reconnect."""
        while True:
            try:
                logger.info(f"Connecting to {self.uri}...")
                self._ws = await websockets.connect(self.uri, ping_interval=30)
                self._connected = True
                logger.info("Connected to Minecraft server.")
                await self._message_loop()
            except (ConnectionClosed, OSError) as e:
                self._connected = False
                logger.warning(f"Connection lost: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                self._connected = False
                logger.error(f"Connection error: {e}. Reconnecting in 10s...")
                await asyncio.sleep(10)

    async def disconnect(self):
        """Disconnect from the Minecraft server."""
        self._connected = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _message_loop(self):
        """Process incoming messages from the Minecraft server."""
        assert self._ws is not None
        async for raw in self._ws:
            try:
                msg_type, data = parse_message(raw)
                await self._handle_message(msg_type, data)
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def _handle_message(self, msg_type: str, data: dict):
        """Route message to the appropriate handler."""
        match msg_type:
            case "perception":
                # Data is nested: outer has {type, agent_name, data: {tick, position,...}}
                inner = data.get("data", data)
                inner["agent_name"] = data.get("agent_name", inner.get("agent_name", ""))
                perception = PerceptionData.from_json(inner)
                craft_key = tuple(c.get('item') for c in perception.craftable)
                if craft_key != getattr(self, '_last_craft_key', ()):
                    self._last_craft_key = craft_key
                    if perception.craftable:
                        logger.info(f"Craftable changed: can_craft={len(perception.craftable)} items={list(craft_key[:5])}")
                await self.manager.handle_perception(perception)

            case "action_result":
                inner = data.get("data", data)
                inner["agent_name"] = data.get("agent_name", inner.get("agent_name", ""))
                result = ActionResult.from_json(inner)
                await self.manager.handle_action_result(result)

            case "spawn_agent":
                name = data.get("agent_name", data.get("name", ""))
                inner = data.get("data", {})
                personality = inner.get("personality", data.get("personality", "default"))
                await self.manager.spawn_agent(name, personality)

            case "despawn_agent":
                name = data.get("agent_name", data.get("name", ""))
                await self.manager.despawn_agent(name)

            case "agent_spawned":
                name = data.get("agent_name", "")
                logger.info(f"Agent spawned confirmed: {name}")

            case "agent_despawned":
                name = data.get("agent_name", "")
                logger.info(f"Agent despawned confirmed: {name}")

            case "player_chat":
                player = data.get("player", "?")
                text = data.get("text", "")
                logger.info(f"[PLAYER] {player}: {text}")
                await self.manager.handle_player_instruction(player, text)

            case "chat":
                agent_name = data.get("agent_name", data.get("name", ""))
                text = data.get("text", data.get("data", {}).get("text", ""))
                logger.info(f"[CHAT] {agent_name}: {text}")

            case _:
                logger.debug(f"Unhandled message type: {msg_type}")

    # --- Sending ---

    async def send(self, data: dict):
        """Send a JSON message to the Minecraft server."""
        if self._ws and self._connected:
            await self._ws.send(json.dumps(data))

    async def send_action(self, action: AgentAction):
        logger.info(f"Sending action: {action.agent_name} -> {action.action} {action.params}")
        await self.send(action.to_json())

    async def send_chat(self, chat: AgentChat):
        logger.info(f"Agent {chat.agent_name} chat: {chat.text}")
        await self.send(chat.to_json())

    async def send_spawn(self, request: SpawnRequest):
        await self.send(request.to_json())

    async def send_despawn(self, request: DespawnRequest):
        await self.send(request.to_json())
