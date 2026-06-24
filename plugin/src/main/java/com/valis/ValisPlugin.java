package com.valis;

import com.valis.agent.VirtualAgent;
import com.valis.bridge.WebSocketBridge;
import com.valis.config.ValisConfig;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.TextColor;
import org.bukkit.Bukkit;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.inventory.InventoryClickEvent;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.plugin.java.JavaPlugin;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.logging.Logger;

/**
 * Main plugin class for Project Valis.
 * Manages the lifecycle of AI agents in the Minecraft world
 * and bridges communication with the Python agent brain service.
 */
public class ValisPlugin extends JavaPlugin {

    private static ValisPlugin instance;
    private Logger log;
    private ValisConfig config;
    private WebSocketBridge wsBridge;
    private final Map<String, VirtualAgent> agents = new ConcurrentHashMap<>();
    private final Map<Player, String> spectatingPlayers = new ConcurrentHashMap<>();  // player -> agent name

    @Override
    public void onEnable() {
        instance = this;
        log = getLogger();
        log.info("=== Project Valis: AI Civilization ===");

        // Load configuration
        saveDefaultConfig();
        config = new ValisConfig(this);

        // Verify dependencies
        if (getServer().getPluginManager().getPlugin("Citizens") == null) {
            log.severe("Citizens plugin not found! Disabling...");
            getServer().getPluginManager().disablePlugin(this);
            return;
        }
        if (getServer().getPluginManager().getPlugin("ProtocolLib") == null) {
            log.severe("ProtocolLib not found! Disabling...");
            getServer().getPluginManager().disablePlugin(this);
            return;
        }

        // Start WebSocket bridge
        int wsPort = config.getWebSocketPort();
        wsBridge = new WebSocketBridge(this, wsPort);
        wsBridge.start();
        log.info("WebSocket bridge started on port " + wsPort);

        // Register commands
        getCommand("valis").setExecutor(new ValisCommand(this));

        // Register chat listener — forward player messages to agent brain
        getServer().getPluginManager().registerEvents(new Listener() {
            @EventHandler
            public void onChat(AsyncPlayerChatEvent event) {
                if (wsBridge != null && wsBridge.isRunning()) {
                    wsBridge.sendPlayerChat(event.getPlayer().getName(), event.getMessage());
                }
            }
            @EventHandler
            public void onInvClick(InventoryClickEvent event) {
                // Prevent stealing from agent inventory view
                var title = event.getView().getTitle();
                if (title.contains("'s Inventory")) {
                    event.setCancelled(true);
                }
            }
        }, this);

        log.info("Valis plugin enabled successfully.");

        // Restore agents from existing Citizens NPCs (persists across restarts)
        var registry = net.citizensnpcs.api.CitizensAPI.getNPCRegistry();
        for (var npc : registry) {
            if (npc.isSpawned() && npc.data().get("valis_personality") != null) {
                var agent = com.valis.agent.VirtualAgent.restore(this, npc);
                agents.put(agent.getAgentName(), agent);
                agent.startPerceptionLoop();
                log.info("Restored agent from NPC: " + agent.getAgentName());
            }
        }

        // Show agent inventory to spectating players every 2 seconds
        getServer().getScheduler().runTaskTimer(this, () -> {
            for (var entry : spectatingPlayers.entrySet()) {
                Player player = entry.getKey();
                VirtualAgent agent = agents.get(entry.getValue());
                if (agent == null || !player.isOnline()) {
                    spectatingPlayers.remove(player);
                    continue;
                }
                var inv = agent.getInventory();
                String text;
                if (inv.isEmpty()) {
                    text = "§7[§b" + agent.getAgentName() + "§7] §fInventory: §7empty";
                } else {
                    StringBuilder sb = new StringBuilder();
                    for (var item : inv.entrySet()) {
                        if (sb.length() > 0) sb.append(" §7|§f ");
                        sb.append(item.getKey()).append(":§e").append(item.getValue());
                    }
                    text = "§7[§b" + agent.getAgentName() + "§7] §f" + sb.toString();
                }
                player.sendActionBar(Component.text(text));
            }
        }, 20L, 40L);  // every 2 seconds
    }

    @Override
    public void onDisable() {
        log.info("Shutting down Valis...");

        // Despawn all agents
        for (VirtualAgent agent : agents.values()) {
            agent.despawn();
        }
        agents.clear();

        // Stop WebSocket bridge
        if (wsBridge != null) {
            try {
                wsBridge.stop();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                log.warning("Interrupted while stopping WebSocket bridge");
            }
        }

        log.info("Valis plugin disabled.");
    }

    public static ValisPlugin getInstance() { return instance; }
    public Map<String, VirtualAgent> getAgents() { return agents; }
    public WebSocketBridge getWsBridge() { return wsBridge; }
    public ValisConfig getValisConfig() { return config; }
    public Map<Player, String> getSpectatingPlayers() { return spectatingPlayers; }

    public void startSpectating(Player player, String agentName) {
        spectatingPlayers.put(player, agentName);
    }

    public void stopSpectating(Player player) {
        spectatingPlayers.remove(player);
    }
}
