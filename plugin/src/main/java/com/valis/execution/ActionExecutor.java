package com.valis.execution;

import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.agent.VirtualAgent;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.entity.ArmorStand;
import org.bukkit.util.Vector;

import java.util.logging.Logger;

/**
 * Executes actions commanded by the agent brain in the Minecraft world.
 * Translates high-level action descriptions into Minecraft mechanics.
 *
 * Actions correspond to the "Skill Execution" module in the PIANO architecture
 * (Project Sid, Altera.AL 2024).
 */
public class ActionExecutor {

    private final ValisPlugin plugin;
    private final VirtualAgent agent;
    private final Logger log;

    public ActionExecutor(ValisPlugin plugin, VirtualAgent agent) {
        this.plugin = plugin;
        this.agent = agent;
        this.log = plugin.getLogger();
    }

    /**
     * Execute an action with parameters.
     */
    public void execute(String action, JsonObject params) {
        try {
            switch (action.toLowerCase()) {
                case "move_to" -> moveTo(params);
                case "mine_block" -> mineBlock(params);
                case "place_block" -> placeBlock(params);
                case "look_at" -> lookAt(params);
                case "chat" -> chat(params);
                case "idle" -> idle();
                default -> {
                    log.warning("Unknown action for " + agent.getAgentName() + ": " + action);
                    plugin.getWsBridge().sendActionResult(agent.getAgentName(), action,
                            false, "unknown action: " + action);
                }
            }
        } catch (Exception e) {
            log.warning("Action failed for " + agent.getAgentName() + ": " + action + " - " + e.getMessage());
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), action,
                    false, e.getMessage());
        }
    }

    /**
     * Move the agent toward a target position via teleport steps.
     */
    private void moveTo(JsonObject params) {
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();

        ArmorStand entity = agent.getEntity();
        if (entity != null && entity.isValid()) {
            Location current = entity.getLocation();
            Location target = new Location(current.getWorld(), x + 0.5, y, z + 0.5);

            // Move in steps toward target (5 blocks per tick max)
            Vector direction = target.toVector().subtract(current.toVector());
            double distance = direction.length();
            if (distance > 5) {
                direction.normalize().multiply(5);
                target = current.clone().add(direction);
            }

            // Face the movement direction
            target.setDirection(direction);
            agent.teleport(target);

            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "move_to",
                    true, "moved toward " + x + "," + y + "," + z);
        }
    }

    /**
     * Mine/break a block at the specified position.
     */
    private void mineBlock(JsonObject params) {
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();

        World world = agent.getLocation().getWorld();
        if (world == null) return;

        Block block = world.getBlockAt(x, y, z);
        if (block.getType() == Material.AIR || block.getType() == Material.BEDROCK) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "mine_block",
                    false, "cannot mine " + block.getType().name());
            return;
        }

        // Simulate block breaking: drop items, set to air
        block.breakNaturally();
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "mine_block",
                true, "mined " + block.getType().name() + " at " + x + "," + y + "," + z);
    }

    /**
     * Place a block at the specified position.
     */
    private void placeBlock(JsonObject params) {
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        String blockType = params.get("block_type").getAsString();

        World world = agent.getLocation().getWorld();
        if (world == null) return;

        Block block = world.getBlockAt(x, y, z);
        if (block.getType() != Material.AIR) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                    false, "position occupied by " + block.getType().name());
            return;
        }

        try {
            Material mat = Material.valueOf(blockType.toUpperCase());
            block.setType(mat);
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                    true, "placed " + blockType + " at " + x + "," + y + "," + z);
        } catch (IllegalArgumentException e) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                    false, "unknown block type: " + blockType);
        }
    }

    /**
     * Make the agent look at a target position.
     */
    private void lookAt(JsonObject params) {
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();

        ArmorStand entity = agent.getEntity();
        if (entity != null && entity.isValid()) {
            Location from = entity.getLocation();
            Location to = new Location(from.getWorld(), x, y, z);
            from.setDirection(to.toVector().subtract(from.toVector()));
            entity.teleport(from);
        }
    }

    /**
     * Send a chat message.
     */
    private void chat(JsonObject params) {
        String message = params.get("message").getAsString();
        agent.sendChat(message);
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "chat",
                true, "said: " + message);
    }

    /**
     * Do nothing (idle action).
     */
    private void idle() {
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "idle",
                true, "idle");
    }
}
