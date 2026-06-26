package com.valis;

import com.valis.agent.VirtualAgent;
import com.valis.bridge.WebSocketBridge;
import com.valis.config.ValisConfig;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextColor;
import net.kyori.adventure.text.format.TextDecoration;
import net.kyori.adventure.title.Title;
import java.time.Duration;
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

        // Spectator HUD — shows agent cognitive state + inventory
        getServer().getScheduler().runTaskTimer(this, () -> {
            for (var entry : spectatingPlayers.entrySet()) {
                Player player = entry.getKey();
                VirtualAgent agent = agents.get(entry.getValue());
                if (agent == null || !player.isOnline()) {
                    spectatingPlayers.remove(player);
                    continue;
                }

                // Action Bar: inventory summary
                var inv = agent.getInventory();
                String invText;
                if (inv.isEmpty()) {
                    invText = "§7Inventory: §8empty";
                } else {
                    StringBuilder sb = new StringBuilder();
                    for (var item : inv.entrySet()) {
                        if (sb.length() > 0) sb.append(" §7| ");
                        sb.append("§f").append(item.getKey()).append("§7:§e").append(item.getValue());
                    }
                    invText = sb.toString();
                }
                player.sendActionBar(Component.text(invText));

                // Title: cognitive state (action + reason + task)
                String action = agent.getCognitiveAction();
                String reason = agent.getCognitiveReason();
                String task = agent.getCognitiveTask();
                String plan = agent.getCognitivePlan();

                if (action != null && !action.isEmpty()) {
                    // Title line: current action
                    Component title = Component.text("⚡ ", NamedTextColor.YELLOW)
                            .append(Component.text(action, NamedTextColor.WHITE));

                    // Subtitle: reason + current plan goal
                    Component subtitle;
                    if (reason != null && !reason.isEmpty()) {
                        subtitle = Component.text("💭 ", NamedTextColor.AQUA)
                                .append(Component.text(reason, NamedTextColor.GRAY));
                        if (plan != null && !plan.isEmpty()) {
                            subtitle = subtitle.append(Component.text("  📋 ", NamedTextColor.GREEN))
                                    .append(Component.text(plan, NamedTextColor.DARK_GREEN));
                        }
                    } else {
                        subtitle = Component.empty();
                    }

                    Title.Times times = Title.Times.times(
                            Duration.ZERO, Duration.ofSeconds(3), Duration.ofMillis(500));
                    player.showTitle(Title.title(title, subtitle, times));
                }
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
