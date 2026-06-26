package com.valis.bridge;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.agent.VirtualAgent;
import org.bukkit.Location;
import org.java_websocket.WebSocket;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.server.WebSocketServer;

import java.net.InetSocketAddress;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.logging.Logger;

/**
 * WebSocket bridge between the Minecraft server and the Python agent brain service.
 * Handles JSON message protocol for agent lifecycle, perception, and action execution.
 */
public class WebSocketBridge extends WebSocketServer {

    private final ValisPlugin plugin;
    private final Logger log;
    private final Gson gson = new Gson();
    private WebSocket brainConnection;
    private final ExecutorService executor = Executors.newCachedThreadPool();

    public WebSocketBridge(ValisPlugin plugin, int port) {
        super(new InetSocketAddress(port));
        this.plugin = plugin;
        this.log = plugin.getLogger();
    }

    @Override
    public void onOpen(WebSocket conn, ClientHandshake handshake) {
        log.info("Agent brain connected: " + conn.getRemoteSocketAddress());
        brainConnection = conn;
    }

    @Override
    public void onClose(WebSocket conn, int code, String reason, boolean remote) {
        log.info("Agent brain disconnected: " + reason);
        if (conn == brainConnection) {
            brainConnection = null;
        }
    }

    @Override
    public void onMessage(WebSocket conn, String message) {
        executor.submit(() -> handleMessage(message));
    }

    @Override
    public void onError(WebSocket conn, Exception ex) {
        log.warning("WebSocket error: " + ex.getMessage());
    }

    @Override
    public void onStart() {
        log.info("WebSocket bridge listening on port " + getPort());
    }

    public void sendPlayerChat(String playerName, String text) {
        JsonObject msg = new JsonObject();
        msg.addProperty("type", "player_chat");
        msg.addProperty("player", playerName);
        msg.addProperty("text", text);
        send(msg);
    }

    private void send(JsonObject msg) {
        if (brainConnection != null && brainConnection.isOpen()) {
            brainConnection.send(gson.toJson(msg));
        }
    }

    // --- Message Handling ---

    private void handleMessage(String raw) {
        try {
            JsonObject msg = gson.fromJson(raw, JsonObject.class);
            String type = msg.get("type").getAsString();

            switch (type) {
                case "agent_spawn" -> handleAgentSpawn(msg);
                case "agent_despawn" -> handleAgentDespawn(msg);
                case "agent_action" -> handleAgentAction(msg);
                case "agent_chat" -> handleAgentChat(msg);
                case "agent_state" -> handleAgentState(msg);
                default -> log.warning("Unknown message type: " + type);
            }
        } catch (Exception e) {
            log.warning("Failed to parse message: " + e.getMessage());
        }
    }

    private void handleAgentSpawn(JsonObject msg) {
        String name = msg.get("name").getAsString();
        double x = msg.has("x") ? msg.get("x").getAsDouble() : 0;
        double y = msg.has("y") ? msg.get("y").getAsDouble() : 64;
        double z = msg.has("z") ? msg.get("z").getAsDouble() : 0;
        String personality = msg.has("personality") ? msg.get("personality").getAsString() : "default";

        // Run on main thread (Bukkit)
        plugin.getServer().getScheduler().runTask(plugin, () -> {
            // Skip if agent already exists (e.g., spawned via command before brain connected)
            if (plugin.getAgents().containsKey(name)) {
                log.info("Agent already exists, skipping spawn: " + name);
                sendToBrain("agent_spawned", name, null);
                return;
            }

            var world = plugin.getServer().getWorld(plugin.getValisConfig().getWorldName());
            if (world == null) {
                log.severe("World not found: " + plugin.getValisConfig().getWorldName());
                return;
            }
            Location loc = new Location(world, x, y, z);
            VirtualAgent agent = new VirtualAgent(plugin, name, personality, loc);
            agent.spawn();
            plugin.getAgents().put(name, agent);
            log.info("Agent spawned: " + name + " at " + loc.toVector());

            // Confirm spawn back to brain
            sendToBrain("agent_spawned", name, null);

            // Start perception loop
            agent.startPerceptionLoop();
        });
    }

    private void handleAgentDespawn(JsonObject msg) {
        String name = msg.get("name").getAsString();
        plugin.getServer().getScheduler().runTask(plugin, () -> {
            VirtualAgent agent = plugin.getAgents().remove(name);
            if (agent != null) {
                agent.despawn();
                log.info("Agent despawned: " + name);
                sendToBrain("agent_despawned", name, null);
            }
        });
    }

    private void handleAgentAction(JsonObject msg) {
        String name = msg.get("name").getAsString();
        String action = msg.get("action").getAsString();
        JsonObject params = msg.has("params") ? msg.getAsJsonObject("params") : new JsonObject();

        plugin.getServer().getScheduler().runTask(plugin, () -> {
            VirtualAgent agent = plugin.getAgents().get(name);
            if (agent != null) {
                agent.executeAction(action, params);
            }
        });
    }

    private void handleAgentChat(JsonObject msg) {
        String name = msg.get("name").getAsString();
        String text = msg.get("text").getAsString();

        plugin.getServer().getScheduler().runTask(plugin, () -> {
            VirtualAgent agent = plugin.getAgents().get(name);
            if (agent != null) {
                agent.sendChat(text);
            }
        });
    }

    private void handleAgentState(JsonObject msg) {
        String name = msg.get("name").getAsString();
        String task = msg.has("current_task") ? msg.get("current_task").getAsString() : "";
        String reason = msg.has("reason") ? msg.get("reason").getAsString() : "";
        String action = msg.has("action") ? msg.get("action").getAsString() : "";
        String plan = msg.has("plan_summary") ? msg.get("plan_summary").getAsString() : "";

        VirtualAgent agent = plugin.getAgents().get(name);
        if (agent != null) {
            agent.updateCognitiveState(task, reason, action, plan);
        }
    }

    // --- Outgoing Messages ---

    public void sendToBrain(String type, String agentName, JsonObject data) {
        if (brainConnection == null || !brainConnection.isOpen()) return;

        JsonObject msg = new JsonObject();
        msg.addProperty("type", type);
        msg.addProperty("agent_name", agentName);
        if (data != null) msg.add("data", data);

        brainConnection.send(gson.toJson(msg));
    }

    public void sendPerception(String agentName, JsonObject perception) {
        sendToBrain("perception", agentName, perception);
    }

    public void sendAgentSpawn(String name, String personality) {
        JsonObject data = new JsonObject();
        data.addProperty("name", name);
        data.addProperty("personality", personality);
        sendToBrain("spawn_agent", name, data);
    }

    public void sendAgentDespawn(String name) {
        sendToBrain("despawn_agent", name, null);
    }

    public void sendActionResult(String agentName, String action, boolean success, String details) {
        JsonObject data = new JsonObject();
        data.addProperty("action", action);
        data.addProperty("success", success);
        data.addProperty("details", details);
        sendToBrain("action_result", agentName, data);
    }

    public boolean isRunning() {
        return brainConnection != null && brainConnection.isOpen();
    }
}
