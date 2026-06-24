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
    private final Map<String, Integer> inventory = new HashMap<>();  // material_name -> count

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
        if (npc != null) { npc.despawn(); npc.destroy(); }
    }

    public void startPerceptionLoop() {
        int interval = plugin.getValisConfig().getPerceptionIntervalTicks();
        perceptionTask = plugin.getServer().getScheduler().runTaskTimer(plugin, () -> {
            tickCounter++;
            JsonObject perception = observer.observe(tickCounter);
            plugin.getWsBridge().sendPerception(name, perception);
        }, 0L, interval);
    }

    public void executeAction(String action, JsonObject params) {
        executor.execute(action, params);
    }

    public void sendChat(String text) {
        if (npc != null && npc.isSpawned()) {
            var loc = npc.getStoredLocation();
            if (loc != null && loc.getWorld() != null) {
                loc.getWorld().getPlayers().forEach(player ->
                    player.sendMessage("§7[§b" + name + "§7]§f " + text)
                );
            }
        }
    }

    public String getAgentName() { return name; }
    public String getPersonality() { return personality; }
    public NPC getNpc() { return npc; }
    public Location getLocation() {
        return npc != null && npc.isSpawned() ? npc.getStoredLocation() : spawnLocation;
    }
    public WorldObserver getObserver() { return observer; }
    public int getTickCounter() { return tickCounter; }

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
