package com.valis.execution;

import com.comphenix.protocol.PacketType;
import com.comphenix.protocol.ProtocolLibrary;
import com.comphenix.protocol.events.PacketContainer;
import com.comphenix.protocol.wrappers.BlockPosition;
import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.agent.VirtualAgent;
import net.citizensnpcs.api.npc.NPC;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.event.player.PlayerTeleportEvent;
import org.bukkit.scheduler.BukkitRunnable;

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
                case "attack_mob" -> attackMob(params);
                case "collect_items" -> collectItems();
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
     * Attack the nearest entity of a specified type (or any mob if not specified).
     */
    private void attackMob(JsonObject params) {
        String targetType = params.has("type") ? params.get("type").getAsString().toUpperCase() : null;
        World world = agent.getLocation().getWorld();
        if (world == null) return;
        Location loc = agent.getLocation();
        double bestDist = Double.MAX_VALUE;
        org.bukkit.entity.Entity bestTarget = null;
        for (org.bukkit.entity.Entity entity : world.getNearbyEntities(loc, 5, 5, 5)) {
            if (entity.getUniqueId().equals(agent.getNpc() != null ? agent.getNpc().getUniqueId() : null)) continue;
            if (entity instanceof org.bukkit.entity.Player) continue; // Don't attack players
            if (!(entity instanceof org.bukkit.entity.LivingEntity)) continue;
            if (targetType != null && !entity.getType().name().equalsIgnoreCase(targetType)) continue;
            double dist = loc.distance(entity.getLocation());
            if (dist < bestDist) { bestDist = dist; bestTarget = entity; }
        }
        if (bestTarget instanceof org.bukkit.entity.LivingEntity living) {
            living.damage(4.0); // ~2 hearts per hit
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "attack_mob",
                    true, "attacked " + bestTarget.getType().name() + " (" + String.format("%.1f", bestDist) + "m)");
        } else {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "attack_mob",
                    false, "no target found");
        }
    }

    /**
     * Collect nearby dropped items on the ground.
     */
    private void collectItems() {
        World world = agent.getLocation().getWorld();
        if (world == null) return;
        Location loc = agent.getLocation();
        int collected = 0;
        for (org.bukkit.entity.Entity entity : world.getNearbyEntities(loc, 3, 3, 3)) {
            if (entity instanceof org.bukkit.entity.Item item) {
                agent.addToInventory(item.getItemStack().getType(), item.getItemStack().getAmount());
                item.remove();
                collected++;
            }
        }
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "collect_items",
                true, "collected " + collected + " items");
    }

    /**
     * Navigate NPC using Citizens pathfinding with water enabled.
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
            // Enable water navigation
            npc.getNavigator().getLocalParameters().avoidWater(false);
            npc.getNavigator().setTarget(target);
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "move_to",
                    true, "navigating to " + x + "," + target.getBlockY() + "," + z);
        }
    }

    /**
     * Mine/break a block at the specified position.
     * Plays a block-breaking animation (stages 0-9 over ~1 second) via ProtocolLib,
     * then actually breaks the block.
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

        // Use NPC entity ID as the "breaker" entity for the animation
        int entityId = agent.getNpc().getEntity().getEntityId();
        BlockPosition pos = new BlockPosition(x, y, z);
        var matName = block.getType().name();

        // Play block-breaking animation stages 0-9 over ~1.1 seconds
        new BukkitRunnable() {
            int stage = 0;

            @Override
            public void run() {
                if (stage > 9) {
                    // Animation complete — actually break the block
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
                    this.cancel();
                    return;
                }

                // Send block break animation packet to all nearby players
                try {
                    PacketContainer packet = new PacketContainer(PacketType.Play.Server.BLOCK_BREAK_ANIMATION);
                    packet.getIntegers().write(0, entityId);
                    packet.getBlockPositionModifier().write(0, pos);
                    packet.getIntegers().write(1, stage);

                    for (var player : world.getPlayers()) {
                        if (player.getLocation().distanceSquared(
                                new Location(world, x, y, z)) < 64 * 64) {
                            ProtocolLibrary.getProtocolManager().sendServerPacket(player, packet);
                        }
                    }
                } catch (Exception e) {
                    log.warning("Failed to send block break animation packet: " + e.getMessage());
                }

                stage++;
            }
        }.runTaskTimer(plugin, 0L, 2L);  // 2 ticks = ~100ms between stages, total ~1s for 10 stages
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
     * Dynamic crafting using Bukkit's vanilla recipe system.
     * No hardcoded recipes — the server already knows all Minecraft recipes.
     */

    private void craft(JsonObject params) {
        String itemName = params.has("item") ? params.get("item").getAsString().toLowerCase() : "";
        Material resultMat = Material.matchMaterial(itemName.toUpperCase());
        if (resultMat == null) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "craft",
                    false, "unknown item: " + itemName);
            return;
        }
        // Look up vanilla recipe from Bukkit
        var recipes = org.bukkit.Bukkit.getRecipesFor(new org.bukkit.inventory.ItemStack(resultMat));
        boolean crafted = false;
        for (var recipe : recipes) {
            if (recipe instanceof org.bukkit.inventory.ShapedRecipe shaped) {
                var ingredients = shaped.getIngredientMap();
                boolean hasAll = true;
                for (var entry : ingredients.entrySet()) {
                    if (entry.getValue() == null || entry.getValue().getType() == Material.AIR) continue;
                    String matName = entry.getValue().getType().name().toLowerCase();
                    int needed = entry.getValue().getAmount();
                    Integer has = agent.getInventory().get(matName);
                    if (has == null || has < needed) { hasAll = false; break; }
                }
                if (!hasAll) continue;
                for (var entry : ingredients.entrySet()) {
                    if (entry.getValue() == null || entry.getValue().getType() == Material.AIR) continue;
                    agent.removeFromInventory(entry.getValue().getType().name().toLowerCase(), entry.getValue().getAmount());
                }
                agent.addToInventory(resultMat, shaped.getResult().getAmount());
                plugin.getWsBridge().sendActionResult(agent.getAgentName(), "craft",
                        true, "crafted " + shaped.getResult().getAmount() + "x " + itemName);
                crafted = true;
                break;
            }
        }
        if (!crafted && itemName.endsWith("_planks")) {
            String logName = itemName.replace("_planks", "_log");
            if (agent.getInventory().containsKey(logName)) {
                agent.removeFromInventory(logName, 1);
                agent.addToInventory(resultMat, 4);
                plugin.getWsBridge().sendActionResult(agent.getAgentName(), "craft", true, "crafted 4x " + itemName);
                crafted = true;
            }
        }
        if (!crafted) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "craft",
                    false, "missing ingredients for " + itemName);
        }
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
