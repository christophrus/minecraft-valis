package com.valis.execution;

import com.comphenix.protocol.PacketType;
import com.comphenix.protocol.ProtocolLibrary;
import com.comphenix.protocol.events.PacketContainer;
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
                case "equip" -> equip(params);
                case "look_at" -> lookAt(params);
                case "chat" -> chat(params);
                case "teleport" -> teleport(params);
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
            // Cancel any existing navigation first
            if (npc.getNavigator().isNavigating()) {
                npc.getNavigator().cancelNavigation();
            }
            var navParams = npc.getNavigator().getLocalParameters();
            navParams.avoidWater(false);
            navParams.range(100);
            navParams.distanceMargin(1.5);
            navParams.stuckAction(net.citizensnpcs.api.ai.TeleportStuckAction.INSTANCE);
            navParams.stationaryTicks(60);
            npc.getNavigator().setTarget(target);
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "move_to",
                    true, "navigating to " + x + "," + target.getBlockY() + "," + z);
        }
    }

    /**
     * Mine/break a block at the specified position.
     * Plays a block-breaking animation (stages 0-9 over ~1 second) via ProtocolLib,
     * then actually breaks the block. Uses the best available tool from inventory.
     */
    private void mineBlock(JsonObject params) {
        int x = (int) params.get("x").getAsDouble();
        int y = (int) params.get("y").getAsDouble();
        int z = (int) params.get("z").getAsDouble();

        World world = agent.getLocation().getWorld();
        if (world == null) return;

        Block block = world.getBlockAt(x, y, z);
        var blockType = block.getType();
        if (blockType == Material.AIR || blockType == Material.BEDROCK) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "mine_block",
                    false, "cannot mine " + blockType.name());
            return;
        }

        // Pick the best tool for this block from agent's inventory
        Material bestTool = getBestTool(blockType);

        // Visually equip the tool on the NPC so players can see it
        if (bestTool != null) {
            var entity = agent.getNpc().getEntity();
            if (entity instanceof org.bukkit.entity.LivingEntity living) {
                living.getEquipment().setItemInMainHand(
                        new org.bukkit.inventory.ItemStack(bestTool));
            }
        }

        // Make NPC face the block being mined — respawn if entity lost
        var rawEntity = agent.getNpc().getEntity();
        if (rawEntity == null && !agent.getNpc().isSpawned()) {
            agent.getNpc().spawn(new Location(world, x, y + 1, z));
            rawEntity = agent.getNpc().getEntity();
        }
        if (rawEntity == null) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "mine_block", false, "NPC entity not loaded");
            return;
        }
        final var npcEntity = rawEntity;
        int entityId = npcEntity.getEntityId();
        var matName = blockType.name();
        Location blockCenter = new Location(world, x + 0.5, y + 0.5, z + 0.5);
        agent.getNpc().faceLocation(blockCenter);

        // Play block-breaking animation stages 0-9 over ~1.1 seconds
        new BukkitRunnable() {
            int stage = 0;

            @Override
            public void run() {
                if (stage > 9) {
                    // Animation complete — actually break the block
                    var toolStack = bestTool != null
                            ? new org.bukkit.inventory.ItemStack(bestTool)
                            : null;
                    var drops = block.getDrops(toolStack);
                    block.breakNaturally();
                    for (var drop : drops) {
                        agent.addToInventory(drop.getType(), drop.getAmount());
                    }
                    if (drops.isEmpty()) {
                        agent.addToInventory(blockType, 1);
                    }
                    plugin.getWsBridge().sendActionResult(agent.getAgentName(), "mine_block",
                            true, "mined " + matName + " at " + x + "," + y + "," + z
                            + (bestTool != null ? " with " + bestTool.name().toLowerCase() : ""));
                    this.cancel();
                    return;
                }

                // Arm swing animation — makes the NPC visually swing its arm
                if (npcEntity instanceof org.bukkit.entity.LivingEntity living) {
                    living.swingMainHand();
                }

                // Block crack overlay (stages 0-9)
                try {
                    Class<?> nmsBlockPos = Class.forName("net.minecraft.core.BlockPos");
                    Object nmsPos = nmsBlockPos.getConstructor(int.class, int.class, int.class)
                            .newInstance(x, y, z);
                    Class<?> nmsPacketClass = Class.forName(
                            "net.minecraft.network.protocol.game.ClientboundBlockDestructionPacket");
                    Object nmsPacket = nmsPacketClass.getConstructor(int.class, nmsBlockPos, int.class)
                            .newInstance(entityId, nmsPos, stage);
                    PacketContainer packet = new PacketContainer(
                            PacketType.Play.Server.BLOCK_BREAK_ANIMATION, nmsPacket);

                    for (var player : world.getPlayers()) {
                        if (player.getLocation().distanceSquared(
                                new Location(world, x, y, z)) < 64 * 64) {
                            ProtocolLibrary.getProtocolManager().sendServerPacket(player, packet);
                        }
                    }
                } catch (Exception e) {
                    log.warning("Block break animation packet failed: " + e.getMessage());
                }

                stage++;
            }
        }.runTaskTimer(plugin, 0L, 2L);  // 2 ticks = ~100ms between stages, total ~1s for 10 stages
    }

    /**
     * Returns the best tool Material from the agent's inventory for mining the given block type,
     * or null if no suitable tool is available (bare hands).
     */
    private Material getBestTool(Material blockType) {
        var inv = agent.getInventory();
        String blockName = blockType.name().toLowerCase();

        // Stone/ores → pickaxe
        if (blockName.contains("stone") || blockName.contains("ore") || blockName.contains("cobble")
                || blockName.contains("iron") || blockName.contains("gold") || blockName.contains("diamond")
                || blockName.contains("coal") || blockName.contains("lapis") || blockName.contains("redstone")
                || blockName.contains("emerald") || blockName.contains("copper")
                || blockName.contains("obsidian") || blockName.contains("granite")
                || blockName.contains("diorite") || blockName.contains("andesite")
                || blockName.contains("netherrack") || blockName.contains("deepslate")
                || blockName.contains("tuff") || blockName.contains("sandstone")
                || blockName.contains("furnace") || blockName.contains("iron_bars")) {
            for (var entry : inv.entrySet()) {
                String key = entry.getKey().toLowerCase();
                if (key.contains("pickaxe") && entry.getValue() > 0) {
                    return Material.matchMaterial(entry.getKey().toUpperCase());
                }
            }
        }

        // Wood/logs/planks → axe
        if (blockName.contains("log") || blockName.contains("wood") || blockName.contains("planks")
                || blockName.contains("crafting_table") || blockName.contains("fence")
                || blockName.contains("door") || blockName.contains("trapdoor")
                || blockName.contains("chest") || blockName.contains("bamboo")) {
            for (var entry : inv.entrySet()) {
                String key = entry.getKey().toLowerCase();
                if (key.contains("axe") && entry.getValue() > 0) {
                    return Material.matchMaterial(entry.getKey().toUpperCase());
                }
            }
        }

        // Dirt/sand/gravel/clay → shovel
        if (blockName.contains("dirt") || blockName.contains("sand") || blockName.contains("gravel")
                || blockName.contains("clay") || blockName.contains("snow")
                || blockName.contains("grass_block") || blockName.contains("mud")) {
            for (var entry : inv.entrySet()) {
                String key = entry.getKey().toLowerCase();
                if (key.contains("shovel") && entry.getValue() > 0) {
                    return Material.matchMaterial(entry.getKey().toUpperCase());
                }
            }
        }

        return null;  // No suitable tool
    }

    /**
     * Equip an item in the NPC's main hand (visible to all players).
     */
    private void equip(JsonObject params) {
        String itemName = params.has("item") ? params.get("item").getAsString().toLowerCase() : "";
        Material mat = Material.matchMaterial(itemName.toUpperCase());
        if (mat == null) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "equip",
                    false, "unknown item: " + itemName);
            return;
        }
        if (!agent.hasInInventory(itemName, 1)) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "equip",
                    false, "not in inventory: " + itemName);
            return;
        }
        // NPC entity is a LivingEntity (Citizens uses player-like entities)
        var entity = agent.getNpc().getEntity();
        if (entity instanceof org.bukkit.entity.LivingEntity living) {
            living.getEquipment().setItemInMainHand(
                    new org.bukkit.inventory.ItemStack(mat));
        }
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "equip",
                true, "equipped " + itemName);
    }

    /**
     * Place a block at the specified position.
     */
    private void placeBlock(JsonObject params) {
        if (!params.has("x") || !params.has("y") || !params.has("z") || !params.has("block_type")) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                    false, "missing params (need x, y, z, block_type)");
            return;
        }
        int x = (int) params.get("x").getAsDouble();
        int y = (int) params.get("y").getAsDouble();
        int z = (int) params.get("z").getAsDouble();
        String blockType = params.get("block_type").getAsString();

        World world = agent.getLocation().getWorld();
        if (world == null) return;

        Block block = world.getBlockAt(x, y, z);
        Material existing = block.getType();
        boolean replaceable = existing == Material.AIR
                || existing == Material.CAVE_AIR
                || existing == Material.VOID_AIR
                || existing == Material.SHORT_GRASS
                || existing == Material.TALL_GRASS
                || existing == Material.FERN
                || existing == Material.LARGE_FERN
                || existing == Material.DEAD_BUSH
                || existing == Material.SNOW
                || existing == Material.VINE
                || existing == Material.GRASS_BLOCK
                || existing == Material.DIRT
                || existing == Material.GRAVEL
                || existing.name().contains("LEAF_LITTER")
                || existing.name().contains("LEAVES");
        if (!replaceable) {
            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                    false, "position occupied by " + existing.name());
            return;
        }

        try {
            Material mat = Material.valueOf(blockType.toUpperCase());
            if (!mat.isBlock()) {
                plugin.getWsBridge().sendActionResult(agent.getAgentName(), "place_block",
                        false, blockType + " is not a placeable block");
                return;
            }
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
        String itemName = params.has("item") ? params.get("item").getAsString().toLowerCase().trim() : "";
        // Strip quantity prefix like "4x stick" → "stick"
        if (itemName.matches("\\d+x\\s+.*")) {
            itemName = itemName.replaceFirst("\\d+x\\s+", "");
        }
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
                // Check if recipe needs a crafting table (shape exceeds 2x2 grid)
                String[] shape = shaped.getShape();
                boolean needs3x3 = shape.length > 2;
                if (!needs3x3) {
                    for (String row : shape) {
                        if (row.length() > 2) { needs3x3 = true; break; }
                    }
                }
                if (needs3x3) {
                    // 3x3 recipe — requires a nearby crafting table
                    Location agentLoc = agent.getNpc() != null && agent.getNpc().isSpawned()
                            ? agent.getNpc().getEntity().getLocation() : null;
                    if (agentLoc != null) {
                        boolean tableNearby = false;
                        int radius = 4;
                        for (int dx = -radius; dx <= radius; dx++) {
                            for (int dy = -2; dy <= 2; dy++) {
                                for (int dz = -radius; dz <= radius; dz++) {
                                    Block b = agentLoc.getWorld().getBlockAt(
                                            agentLoc.getBlockX() + dx,
                                            agentLoc.getBlockY() + dy,
                                            agentLoc.getBlockZ() + dz);
                                    if (b.getType() == Material.CRAFTING_TABLE) {
                                        tableNearby = true;
                                        break;
                                    }
                                }
                                if (tableNearby) break;
                            }
                            if (tableNearby) break;
                        }
                        if (!tableNearby) {
                            plugin.getWsBridge().sendActionResult(agent.getAgentName(), "craft",
                                    false, "need nearby crafting_table for " + itemName);
                            return;
                        }
                    }
                }
                // Use getChoiceMap() to support material variants (e.g. any plank type for crafting_table)
                var choices = shaped.getChoiceMap();
                // For each slot, find which material the agent actually has
                // Map: slot -> (matched material name, amount needed)
                var slotMatches = new java.util.HashMap<Character, String>();
                // Count total needed per matched material
                var totalNeeded = new java.util.HashMap<String, Integer>();
                boolean hasAll = true;

                for (var entry : choices.entrySet()) {
                    var choice = entry.getValue();
                    if (choice == null) continue;
                    String matchedMat = null;
                    if (choice instanceof org.bukkit.inventory.RecipeChoice.MaterialChoice matChoice) {
                        // Try each valid material variant against inventory
                        for (Material mat : matChoice.getChoices()) {
                            String matName = mat.name().toLowerCase();
                            int alreadyNeeded = totalNeeded.getOrDefault(matName, 0);
                            Integer has = agent.getInventory().get(matName);
                            if (has != null && has > alreadyNeeded) {
                                matchedMat = matName;
                                break;
                            }
                        }
                    } else if (choice instanceof org.bukkit.inventory.RecipeChoice.ExactChoice exact) {
                        for (var stack : exact.getChoices()) {
                            String matName = stack.getType().name().toLowerCase();
                            int alreadyNeeded = totalNeeded.getOrDefault(matName, 0);
                            Integer has = agent.getInventory().get(matName);
                            if (has != null && has > alreadyNeeded) {
                                matchedMat = matName;
                                break;
                            }
                        }
                    }
                    if (matchedMat == null) { hasAll = false; break; }
                    slotMatches.put(entry.getKey(), matchedMat);
                    totalNeeded.merge(matchedMat, 1, Integer::sum);
                }

                if (!hasAll) continue;
                // Verify totals
                for (var needed : totalNeeded.entrySet()) {
                    Integer has = agent.getInventory().get(needed.getKey());
                    if (has == null || has < needed.getValue()) { hasAll = false; break; }
                }
                if (!hasAll) continue;

                // Consume ingredients
                for (var needed : totalNeeded.entrySet()) {
                    agent.removeFromInventory(needed.getKey(), needed.getValue());
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
     * Teleport the NPC to a specified position, finding safe ground.
     */
    private void teleport(JsonObject params) {
        int x = (int) params.get("x").getAsDouble();
        int y = (int) params.get("y").getAsDouble();
        int z = (int) params.get("z").getAsDouble();

        NPC npc = agent.getNpc();
        if (npc == null || !npc.isSpawned()) return;

        World world = npc.getStoredLocation().getWorld();
        if (world == null) return;

        // Find safe ground: scan downward from target Y, then upward
        int safeY = y;
        for (int dy = 0; dy < 20; dy++) {
            Block below = world.getBlockAt(x, y - dy, z);
            Block atFeet = world.getBlockAt(x, y - dy + 1, z);
            Block atHead = world.getBlockAt(x, y - dy + 2, z);
            if (below.getType().isSolid()
                    && !atFeet.getType().isSolid()
                    && !atHead.getType().isSolid()) {
                safeY = y - dy + 1;
                break;
            }
        }

        Location target = new Location(world, x + 0.5, safeY, z + 0.5);
        npc.teleport(target, PlayerTeleportEvent.TeleportCause.PLUGIN);
        // Cancel any ongoing navigation
        if (npc.getNavigator().isNavigating()) {
            npc.getNavigator().cancelNavigation();
        }
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "teleport",
                true, "teleported to " + x + "," + safeY + "," + z);
    }

    /**
     * Do nothing (idle action).
     */
    private void idle() {
        plugin.getWsBridge().sendActionResult(agent.getAgentName(), "idle",
                true, "idle");
    }
}
