package com.valis.agent;

import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.execution.ActionExecutor;
import com.valis.perception.WorldObserver;
import org.bukkit.Location;
import org.bukkit.World;
import org.bukkit.entity.ArmorStand;
import org.bukkit.inventory.ItemStack;
import org.bukkit.Material;
import org.bukkit.scheduler.BukkitTask;
import org.bukkit.util.EulerAngle;

import java.util.logging.Logger;

/**
 * Manages a single AI agent as an armor stand entity with a player head
 * in the Minecraft world.
 *
 * Citizens proved incompatible with PaperMC 1.21.1, so we use a
 * lightweight approach: invisible armor stand + player head = visible agent.
 */
public class VirtualAgent {

    private final ValisPlugin plugin;
    private final Logger log;
    private final String name;
    private final String personality;
    private final Location spawnLocation;

    private ArmorStand entity;
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
     * Spawn the agent entity in the world.
     */
    public void spawn() {
        World world = spawnLocation.getWorld();
        if (world == null) {
            log.severe("Cannot spawn agent " + name + ": world is null");
            return;
        }

        entity = world.spawn(spawnLocation, ArmorStand.class);

        // Configure appearance to look like a player
        entity.setVisible(true);
        entity.setCustomName(name);
        entity.setCustomNameVisible(true);
        entity.setGravity(true);
        entity.setBasePlate(false);
        entity.setArms(false);
        entity.setSmall(false);

        // Put a player head on the armor stand
        var head = new ItemStack(Material.PLAYER_HEAD);
        entity.getEquipment().setHelmet(head);

        entity.setHeadPose(new EulerAngle(0, 0, 0));

        log.info("Agent entity spawned: " + name);
    }

    /**
     * Remove the agent entity from the world.
     */
    public void despawn() {
        if (perceptionTask != null) {
            perceptionTask.cancel();
        }
        if (entity != null && entity.isValid()) {
            entity.remove();
        }
        entity = null;
    }

    /**
     * Start the periodic perception loop.
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
     * Execute an action from the agent brain.
     */
    public void executeAction(String action, JsonObject params) {
        executor.execute(action, params);
    }

    /**
     * Send a chat message as this agent.
     */
    public void sendChat(String text) {
        if (entity != null && entity.isValid()) {
            Location loc = entity.getLocation();
            if (loc.getWorld() != null) {
                String formatted = "§7[§b" + name + "§7]§f " + text;
                loc.getWorld().getPlayers().forEach(player ->
                    player.sendMessage(formatted)
                );
            }
        }
    }

    /** Teleport the entity to a new location. */
    public void teleport(Location location) {
        if (entity != null && entity.isValid()) {
            entity.teleport(location);
        }
    }

    // --- Getters ---

    public String getAgentName() { return name; }
    public String getPersonality() { return personality; }
    public ArmorStand getEntity() { return entity; }
    public Location getLocation() {
        if (entity != null && entity.isValid()) {
            return entity.getLocation();
        }
        return spawnLocation;
    }
    public WorldObserver getObserver() { return observer; }
    public int getTickCounter() { return tickCounter; }
}
