package com.valis.agent;

import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.execution.ActionExecutor;
import com.valis.perception.WorldObserver;
import net.citizensnpcs.api.CitizensAPI;
import net.citizensnpcs.api.npc.NPC;
import net.citizensnpcs.api.npc.NPCRegistry;
import net.citizensnpcs.api.trait.trait.Equipment;
import net.citizensnpcs.trait.SkinTrait;
import org.bukkit.Location;
import org.bukkit.entity.EntityType;
import org.bukkit.scheduler.BukkitTask;

import java.util.logging.Logger;

/**
 * Manages a single AI agent as a Citizens NPC in the Minecraft world.
 * Each agent has an NPC entity, a perception loop, and can execute actions.
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

    public VirtualAgent(ValisPlugin plugin, String name, String personality, Location spawn) {
        this.plugin = plugin;
        this.log = plugin.getLogger();
        this.name = name;
        this.personality = personality;
        this.spawnLocation = spawn;
        this.observer = new WorldObserver(plugin, this);
        this.executor = new ActionExecutor(plugin, this);
    }

    /**
     * Spawn the NPC in the world and set up its appearance.
     */
    public void spawn() {
        NPCRegistry registry = CitizensAPI.getNPCRegistry();
        npc = registry.createNPC(EntityType.PLAYER, name);
        npc.getOrAddTrait(SkinTrait.class).setSkinName("Steve"); // TODO: random skin per personality

        // Spawn at location
        npc.spawn(spawnLocation);

        // Give basic appearance
        npc.data().set(NPC.Metadata.NAMEPLATE_VISIBLE, true);
        npc.data().set(NPC.Metadata.GLOWING, false);

        log.info("NPC spawned: " + name);
    }

    /**
     * Remove the NPC from the world.
     */
    public void despawn() {
        if (perceptionTask != null) {
            perceptionTask.cancel();
        }
        if (npc != null) {
            npc.despawn();
            npc.destroy();
        }
    }

    /**
     * Start the periodic perception loop: observe surroundings and send to brain.
     */
    public void startPerceptionLoop() {
        int interval = plugin.getValisConfig().getPerceptionIntervalTicks();
        perceptionTask = plugin.getServer().getScheduler().runTaskTimer(plugin, () -> {
            tickCounter++;
            JsonObject perception = observer.observe(tickCounter);
            plugin.getWsBridge().sendPerception(name, perception);
        }, 0L, interval);
    }

    /**
     * Execute an action received from the agent brain.
     */
    public void executeAction(String action, JsonObject params) {
        executor.execute(action, params);
    }

    /**
     * Send a chat message from this agent to the world.
     */
    public void sendChat(String text) {
        if (npc != null && npc.isSpawned()) {
            // Broadcast as NPC chat using Citizens API
            npc.getDefaultSpeechController().speak(npc, text);
        }
    }

    // --- Getters ---

    public String getAgentName() { return name; }
    public String getPersonality() { return personality; }
    public NPC getNpc() { return npc; }
    public Location getLocation() {
        return npc != null && npc.isSpawned() ? npc.getStoredLocation() : spawnLocation;
    }
    public WorldObserver getObserver() { return observer; }
    public int getTickCounter() { return tickCounter; }
}
