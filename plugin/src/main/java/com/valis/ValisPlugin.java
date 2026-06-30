package com.valis;

import com.valis.agent.VirtualAgent;
import com.valis.bridge.WebSocketBridge;
import com.valis.config.ValisConfig;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextColor;
import org.bukkit.Bukkit;
import org.bukkit.Material;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.inventory.InventoryClickEvent;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.inventory.Inventory;
import org.bukkit.inventory.ItemStack;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scoreboard.Criteria;
import org.bukkit.scoreboard.DisplaySlot;
import org.bukkit.scoreboard.Objective;
import org.bukkit.scoreboard.Scoreboard;

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
    private final Map<Player, String> spectatingPlayers = new ConcurrentHashMap<>();
    private final Map<Player, Inventory> spectateInventories = new ConcurrentHashMap<>();

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
                // Deliver player chat to all NPCs so they can "hear" it
                for (var agent : agents.values()) {
                    agent.hearChat(event.getPlayer().getName(), event.getMessage());
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

        // Spectator HUD — scoreboard sidebar for cognitive state + live inventory GUI
        getServer().getScheduler().runTaskTimer(this, () -> {
            for (var entry : spectatingPlayers.entrySet()) {
                Player player = entry.getKey();
                VirtualAgent agent = agents.get(entry.getValue());
                if (agent == null || !player.isOnline()) {
                    cleanupSpectator(player);
                    spectatingPlayers.remove(player);
                    continue;
                }

                // --- Scoreboard sidebar: cognitive state ---
                updateSpectatorScoreboard(player, agent);

                // --- Live inventory GUI ---
                updateSpectatorInventory(player, agent);
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
        VirtualAgent agent = agents.get(agentName);
        if (agent != null) {
            // Open inventory GUI immediately
            Inventory inv = createAgentInventoryView(agent);
            spectateInventories.put(player, inv);
            player.openInventory(inv);
        }
    }

    public void stopSpectating(Player player) {
        spectatingPlayers.remove(player);
        cleanupSpectator(player);
    }

    private void cleanupSpectator(Player player) {
        spectateInventories.remove(player);
        // Reset scoreboard to default
        player.setScoreboard(Bukkit.getScoreboardManager().getMainScoreboard());
    }

    private Inventory createAgentInventoryView(VirtualAgent agent) {
        var inv = agent.getInventory();
        int slots = Math.max(9, ((Math.min(inv.size(), 54) + 8) / 9) * 9);
        if (slots > 54) slots = 54;
        Inventory gui = Bukkit.createInventory(null, slots,
                "§b" + agent.getAgentName() + " §7— Inventory");
        fillInventory(gui, inv);
        return gui;
    }

    private void fillInventory(Inventory gui, Map<String, Integer> inv) {
        gui.clear();
        int slot = 0;
        for (var item : inv.entrySet()) {
            if (slot >= gui.getSize()) break;
            Material mat = Material.matchMaterial(item.getKey().toUpperCase());
            if (mat == null || mat == Material.AIR || !mat.isItem()) continue;
            int count = Math.min(item.getValue(), mat.getMaxStackSize());
            gui.setItem(slot++, new ItemStack(mat, count));
        }
    }

    private void updateSpectatorInventory(Player player, VirtualAgent agent) {
        Inventory gui = spectateInventories.get(player);
        if (gui == null) {
            gui = createAgentInventoryView(agent);
            spectateInventories.put(player, gui);
            player.openInventory(gui);
            return;
        }
        // Update contents in-place (no re-open needed)
        var inv = agent.getInventory();
        int neededSlots = Math.max(9, ((Math.min(inv.size(), 54) + 8) / 9) * 9);
        if (neededSlots > gui.getSize()) {
            gui = createAgentInventoryView(agent);
            spectateInventories.put(player, gui);
            player.openInventory(gui);
        } else {
            fillInventory(gui, inv);
        }
    }

    private void updateSpectatorScoreboard(Player player, VirtualAgent agent) {
        Scoreboard board = player.getScoreboard();
        // Create a fresh scoreboard if player doesn't have our custom one
        Objective obj = board.getObjective("valis_hud");
        if (obj == null) {
            board = Bukkit.getScoreboardManager().getNewScoreboard();
            obj = board.registerNewObjective("valis_hud", Criteria.DUMMY,
                    Component.text("§6§l" + agent.getAgentName() + " §e— AI Mind"));
            obj.setDisplaySlot(DisplaySlot.SIDEBAR);
            player.setScoreboard(board);
        }

        // Clear old entries
        for (String e : board.getEntries()) {
            board.resetScores(e);
        }

        String action = agent.getCognitiveAction();
        String reason = agent.getCognitiveReason();
        String task = agent.getCognitiveTask();
        String plan = agent.getCognitivePlan();

        int score = 10;

        // Action
        if (action != null && !action.isEmpty()) {
            obj.getScore("§e⚡ Action:").setScore(score--);
            obj.getScore("§f " + truncate(action, 36)).setScore(score--);
        }

        // Reason
        if (reason != null && !reason.isEmpty()) {
            obj.getScore("§b💭 Reason:").setScore(score--);
            obj.getScore("§7 " + truncate(reason, 36)).setScore(score--);
        }

        // Current task
        if (task != null && !task.isEmpty()) {
            obj.getScore("§a📋 Task:").setScore(score--);
            obj.getScore("§2 " + truncate(task, 36)).setScore(score--);
        }

        // Plan goal
        if (plan != null && !plan.isEmpty()) {
            obj.getScore("§d🎯 Plan:").setScore(score--);
            obj.getScore("§5 " + truncate(plan, 36)).setScore(score--);
        }
    }

    private static String truncate(String s, int max) {
        return s.length() > max ? s.substring(0, max) + "…" : s;
    }
}
