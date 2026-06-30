package com.valis.agent;

import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.execution.ActionExecutor;
import com.valis.perception.WorldObserver;
import net.citizensnpcs.api.CitizensAPI;
import net.citizensnpcs.api.npc.NPC;
import net.citizensnpcs.api.npc.NPCRegistry;
import net.citizensnpcs.trait.SkinTrait;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.entity.EntityType;
import org.bukkit.inventory.ItemStack;
import org.bukkit.scheduler.BukkitTask;

import java.util.HashMap;
import java.util.Map;
import java.util.logging.Logger;

/**
 * Manages a single AI agent as a Citizens NPC.
 * PaperMC 26.1.2 + Citizens2 build 4210 (v26_1_R1 module).
 */
public class VirtualAgent {

    private final ValisPlugin plugin;
    private final Logger log;
    private final String name;
    private final String personality;
    private final Location spawnLocation;

    private NPC npc;
    private BukkitTask perceptionTask;
    private final WorldObserver observer;
    private final ActionExecutor executor;
    private int tickCounter = 0;
    private final Map<String, Integer> inventory = new HashMap<>();

    // Cognitive state for spectator HUD
    private volatile String cognitiveTask = "";
    private volatile String cognitiveReason = "";
    private volatile String cognitiveAction = "";
    private volatile String cognitivePlan = "";

    // Chat buffer — recent messages this agent can "hear" (from nearby NPCs + players)
    private final java.util.List<String> chatBuffer = java.util.Collections.synchronizedList(
            new java.util.ArrayList<>());

    // Chunk loading — NPCs don't trigger chunk loading like players do.
    // We use Paper's plugin chunk ticket API to keep chunks loaded around the agent.
    private int[] _lastChunkCenter = null;
    private java.util.List<int[]> _chunkTickets = null;

    public VirtualAgent(ValisPlugin plugin, String name, String personality, Location spawn) {
        this.plugin = plugin;
        this.log = plugin.getLogger();
        this.name = name;
        this.personality = personality;
        this.spawnLocation = spawn;
        this.observer = new WorldObserver(plugin, this);
        this.executor = new ActionExecutor(plugin, this);
    }

    public void spawn() {
        NPCRegistry registry = CitizensAPI.getNPCRegistry();
        npc = registry.createNPC(EntityType.PLAYER, name);
        npc.getOrAddTrait(SkinTrait.class).setSkinName("Steve");
        npc.spawn(spawnLocation);
        npc.data().set(NPC.Metadata.NAMEPLATE_VISIBLE, true);
        npc.data().set(NPC.Metadata.GLOWING, false);
        // Persist agent metadata
        npc.data().set("valis_personality", personality);
        npc.data().set("valis_inventory", inventory);
        log.info("NPC spawned: " + name);
    }

    /** Reconnect to an existing Citizens NPC after server restart. */
    public static VirtualAgent restore(ValisPlugin plugin, NPC npc) {
        String name = npc.getName();
        String personality = npc.data().get("valis_personality", "default");
        Location loc = npc.getStoredLocation();
        VirtualAgent agent = new VirtualAgent(plugin, name, personality, loc);
        agent.npc = npc;
        // Restore inventory
        Object raw = npc.data().get("valis_inventory");
        if (raw instanceof Map<?, ?> map) {
            for (var entry : map.entrySet()) {
                if (entry.getValue() instanceof Number num) {
                    agent.inventory.put(entry.getKey().toString(), num.intValue());
                }
            }
        }
        plugin.getLogger().info("Restored agent: " + name + " inv=" + agent.inventory);
        return agent;
    }

    public void saveInventory() {
        if (npc != null) {
            npc.data().set("valis_inventory", new HashMap<>(inventory));
        }
    }

    public void despawn() {
        if (perceptionTask != null) perceptionTask.cancel();
        releaseChunkTickets();
        if (npc != null) { npc.despawn(); npc.destroy(); }
    }

    public void startPerceptionLoop() {
        int interval = plugin.getValisConfig().getPerceptionIntervalTicks();
        perceptionTask = plugin.getServer().getScheduler().runTaskTimer(plugin, () -> {
            tickCounter++;
            // Ensure chunks are loaded — NPCs don't trigger chunk loading like players
            ensureChunksLoaded();
            // Passive item pickup — NPCs don't pick up items like players do
            passiveItemPickup();
            JsonObject perception = observer.observe(tickCounter);
            plugin.getWsBridge().sendPerception(name, perception);
        }, 0L, interval);
    }

    private void passiveItemPickup() {
        if (npc == null || !npc.isSpawned()) return;
        var loc = npc.getStoredLocation();
        if (loc == null || loc.getWorld() == null) return;
        for (org.bukkit.entity.Entity entity : loc.getWorld().getNearbyEntities(loc, 2, 2, 2)) {
            if (entity instanceof org.bukkit.entity.Item item && !item.isDead()) {
                var stack = item.getItemStack();
                addToInventory(stack.getType(), stack.getAmount());
                item.remove();
            }
        }
    }

    /**
     * Keep a 3x3 chunk area around the NPC loaded using Paper's chunk ticket API.
     * Without real players, NPCs can't trigger chunk loading — the world outside
     * spawn chunks would remain unloaded, breaking perception and navigation.
     */
    private void ensureChunksLoaded() {
        if (npc == null || !npc.isSpawned()) return;
        var loc = npc.getStoredLocation();
        if (loc == null || loc.getWorld() == null) return;
        var world = loc.getWorld();

        int cx = loc.getBlockX() >> 4;
        int cz = loc.getBlockZ() >> 4;

        // Release old tickets when NPC moves to a new chunk area
        if (_lastChunkCenter == null || _lastChunkCenter[0] != cx || _lastChunkCenter[1] != cz) {
            releaseChunkTickets();
            _lastChunkCenter = new int[]{cx, cz};
        }

        // Add chunk tickets for 3x3 chunks (48x48 blocks) around the NPC
        if (_chunkTickets == null) _chunkTickets = new java.util.ArrayList<>();
        if (_chunkTickets.isEmpty()) {
            int radius = 1;
            for (int dx = -radius; dx <= radius; dx++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    try {
                        boolean added = world.addPluginChunkTicket(cx + dx, cz + dz, plugin);
                        if (added) _chunkTickets.add(new int[]{cx + dx, cz + dz});
                    } catch (Exception ignored) {}
                }
            }
        }
    }

    private void releaseChunkTickets() {
        if (_chunkTickets == null || _chunkTickets.isEmpty()) return;
        var loc = npc != null ? npc.getStoredLocation() : null;
        if (loc == null || loc.getWorld() == null) return;
        var world = loc.getWorld();
        for (var ticket : _chunkTickets) {
            try {
                world.removePluginChunkTicket(ticket[0], ticket[1], plugin);
            } catch (Exception ignored) {}
        }
        _chunkTickets.clear();
    }

    public void executeAction(String action, JsonObject params) {
        executor.execute(action, params);
    }

    public void sendChat(String text) {
        if (npc != null && npc.isSpawned()) {
            var loc = npc.getStoredLocation();
            if (loc != null && loc.getWorld() != null) {
                // Show to all players in the world
                loc.getWorld().getPlayers().forEach(player ->
                    player.sendMessage("§7[§b" + name + "§7]§f " + text)
                );
                // Deliver to all other NPC agents so they can "hear" it
                for (VirtualAgent other : plugin.getAgents().values()) {
                    if (other == this) continue;
                    other.hearChat(name, text);
                }
            }
        }
    }

    public void hearChat(String speaker, String text) {
        chatBuffer.add("[" + speaker + "] " + text);
        if (chatBuffer.size() > 10) chatBuffer.remove(0);
    }

    public java.util.List<String> drainChatBuffer() {
        var copy = new java.util.ArrayList<>(chatBuffer);
        chatBuffer.clear();
        return copy;
    }

    public String getAgentName() { return name; }
    public String getPersonality() { return personality; }
    public NPC getNpc() { return npc; }
    public Location getLocation() {
        return npc != null && npc.isSpawned() ? npc.getStoredLocation() : spawnLocation;
    }
    public WorldObserver getObserver() { return observer; }
    public int getTickCounter() { return tickCounter; }

    public void updateCognitiveState(String task, String reason, String action, String plan) {
        this.cognitiveTask = task;
        this.cognitiveReason = reason;
        this.cognitiveAction = action;
        this.cognitivePlan = plan;
    }

    public String getCognitiveTask() { return cognitiveTask; }
    public String getCognitiveReason() { return cognitiveReason; }
    public String getCognitiveAction() { return cognitiveAction; }
    public String getCognitivePlan() { return cognitivePlan; }

    // --- Inventory ---
    public Map<String, Integer> getInventory() { return inventory; }

    public void addToInventory(Material mat, int amount) {
        String name = mat.name().toLowerCase();
        inventory.merge(name, amount, Integer::sum);
        saveInventory();
    }

    public boolean removeFromInventory(String material, int amount) {
        Integer current = inventory.get(material);
        if (current == null || current < amount) return false;
        int remaining = current - amount;
        if (remaining <= 0) inventory.remove(material);
        else inventory.put(material, remaining);
        saveInventory();
        return true;
    }

    public boolean hasInInventory(String material, int amount) {
        Integer current = inventory.get(material.toLowerCase());
        return current != null && current >= amount;
    }

    public JsonObject inventoryToJson() {
        JsonObject json = new JsonObject();
        for (var entry : inventory.entrySet()) {
            json.addProperty(entry.getKey(), entry.getValue());
        }
        return json;
    }
}
