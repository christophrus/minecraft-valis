package com.valis.execution;

import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.agent.VirtualAgent;
import net.citizensnpcs.api.npc.NPC;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.event.player.PlayerTeleportEvent;

import java.util.HashMap;
import java.util.Map;
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
                case "craft" -> craft(params);
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
     * Teleport the NPC to a target position (reliable, instant).
     */
    private void moveTo(JsonObject params) {
        int x = (int) params.get("x").getAsDouble();
        int y = (int) params.get("y").getAsDouble();
        int z = (int) params.get("z").getAsDouble();

        NPC npc = agent.getNpc();
        if (npc != null && npc.isSpawned()) {
            Location target = new Location(npc.getStoredLocation().getWorld(), x + 0.5, y, z + 0.5);
            World world = target.getWorld();
            // Find safe ground below target
            for (int dy = 0; dy < 10; dy++) {
                Block check = world.getBlockAt(x, y - dy, z);
                if (check.getType().isSolid() && check.getType() != Material.WATER && check.getType() != Material.LAVA) {
                    target.setY(check.getY() + 1);
                    break;
                }
            }
            npc.teleport(target, PlayerTeleportEvent.TeleportCause.PLUGIN);
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "move_to",
                    true, "teleported to " + x + "," + target.getBlockY() + "," + z);
        }
    }

    /**
     * Mine/break a block at the specified position.
     */
    private void mineBlock(JsonObject params) {
        int x = (int) params.get("x").getAsDouble();
        int y = (int) params.get("y").getAsDouble();
        int z = (int) params.get("z").getAsDouble();

        World world = agent.getLocation().getWorld();
        if (world == null) return;

        Block block = world.getBlockAt(x, y, z);
        if (block.getType() == Material.AIR || block.getType() == Material.BEDROCK) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "mine_block",
                    false, "cannot mine " + block.getType().name());
            return;
        }

        // Simulate block breaking: drop items, set to air
        var matName = block.getType().name();
        var drops = block.getDrops();
        block.breakNaturally();
        for (var drop : drops) {
            agent.addToInventory(drop.getType(), drop.getAmount());
        }
        if (drops.isEmpty()) {
            agent.addToInventory(block.getType(), 1);
        }
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "mine_block",
                true, "mined " + matName + " at " + x + "," + y + "," + z);
    }

    /**
     * Place a block at the specified position.
     */
    private void placeBlock(JsonObject params) {
        int x = (int) params.get("x").getAsDouble();
        int y = (int) params.get("y").getAsDouble();
        int z = (int) params.get("z").getAsDouble();
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
            if (!agent.removeFromInventory(blockType, 1)) {
                plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                        false, "missing " + blockType + " in inventory");
                return;
            }
            block.setType(mat);
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                    true, "placed " + blockType + " at " + x + "," + y + "," + z);
        } catch (IllegalArgumentException e) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                    false, "unknown block type: " + blockType);
        }
    }

    /**
     * Simple crafting system. Converts raw materials to products.
     * Recipes: wood -> planks(4), cobblestone -> stone_pickaxe(1, costs 3),
     *          planks + sticks -> wooden_pickaxe(1, costs 3 planks + 2 sticks)
     */
    private static final Map<String, Map<String, Integer>> RECIPES = new HashMap<>();
    static {{
        // Result material name -> {ingredient_name: amount}
        RECIPES.put("oak_planks", Map.of("oak_log", 1));
        RECIPES.put("birch_planks", Map.of("birch_log", 1));
        RECIPES.put("spruce_planks", Map.of("spruce_log", 1));
        RECIPES.put("stick", Map.of("oak_planks", 2));
        RECIPES.put("crafting_table", Map.of("oak_planks", 4));
        RECIPES.put("wooden_pickaxe", Map.of("oak_planks", 3, "stick", 2));
        RECIPES.put("stone_pickaxe", Map.of("cobblestone", 3, "stick", 2));
        RECIPES.put("wooden_axe", Map.of("oak_planks", 3, "stick", 2));
        RECIPES.put("stone_axe", Map.of("cobblestone", 3, "stick", 2));
        RECIPES.put("wooden_sword", Map.of("oak_planks", 2, "stick", 1));
        RECIPES.put("stone_sword", Map.of("cobblestone", 2, "stick", 1));
    }}

    private void craft(JsonObject params) {
        String item = params.has("item") ? params.get("item").getAsString().toLowerCase() : "";
        var recipe = RECIPES.get(item);
        if (recipe == null) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "craft",
                    false, "unknown recipe: " + item + ". Known: " + String.join(", ", RECIPES.keySet()));
            return;
        }
        for (var entry : recipe.entrySet()) {
            if (!agent.removeFromInventory(entry.getKey(), entry.getValue())) {
                plugin.getWsBridge().sendActionResult(agent.getAgentName(), "craft",
                        false, "missing " + entry.getKey() + " (need " + entry.getValue() + ")");
                return;
            }
        }
        int count = item.equals("stick") ? 4 : 1;
        agent.addToInventory(Material.valueOf(item.toUpperCase()), count);
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "craft",
                true, "crafted " + count + "x " + item);
    }

    /**
     * Make the NPC look at a target position.
     */
    private void lookAt(JsonObject params) {
        int x = (int) params.get("x").getAsDouble();
        int y = (int) params.get("y").getAsDouble();
        int z = (int) params.get("z").getAsDouble();

        NPC npc = agent.getNpc();
        if (npc != null && npc.isSpawned()) {
            Location from = npc.getStoredLocation();
            Location to = new Location(from.getWorld(), x, y, z);
            from.setDirection(to.toVector().subtract(from.toVector()));
            npc.teleport(from, null);
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
