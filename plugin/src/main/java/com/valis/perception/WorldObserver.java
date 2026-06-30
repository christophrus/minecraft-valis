package com.valis.perception;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.agent.VirtualAgent;
import net.citizensnpcs.api.npc.NPC;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.RecipeChoice;
import org.bukkit.inventory.ShapedRecipe;
import org.bukkit.inventory.ShapelessRecipe;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Observes the world around an agent and builds a structured perception report.
 * This is sent to the Python agent brain for decision-making.
 *
 * Inspired by the "Perceive" module from Generative Agents (Park et al., 2023)
 * where agents observe nearby events, objects, and other agents.
 */
public class WorldObserver {

    private final ValisPlugin plugin;
    private final VirtualAgent agent;
    private final int radius;

    public WorldObserver(ValisPlugin plugin, VirtualAgent agent) {
        this.plugin = plugin;
        this.agent = agent;
        this.radius = plugin.getValisConfig().getPerceptionRadius();
    }

    /**
     * Build a perception report for the current tick.
     * Includes: agent position, nearby blocks, nearby entities, time, weather.
     */
    public JsonObject observe(int tick) {
        JsonObject report = new JsonObject();
        Location loc = agent.getLocation();
        World world = loc.getWorld();
        if (world == null) return report;

        // Position
        JsonObject pos = new JsonObject();
        pos.addProperty("x", loc.getBlockX());
        pos.addProperty("y", loc.getBlockY());
        pos.addProperty("z", loc.getBlockZ());
        report.add("position", pos);
        report.addProperty("tick", tick);

        // World state
        report.addProperty("time", world.getTime());
        report.addProperty("is_day", world.getTime() < 13000);
        report.addProperty("weather", world.isThundering() ? "thunder" :
                world.hasStorm() ? "rain" : "clear");

        // Biome
        var biome = world.getBiome(loc);
        report.addProperty("biome", biome.getKey().getKey().replace("_", " "));

        // Nearby biomes (scan 100 blocks in cardinal directions)
        JsonObject nearbyBiomes = new JsonObject();
        int bd = 300;
        int bx = loc.getBlockX(), by = loc.getBlockY(), bz = loc.getBlockZ();
        var northB = world.getBiome(bx, by, bz - bd);
        var southB = world.getBiome(bx, by, bz + bd);
        var eastB = world.getBiome(bx + bd, by, bz);
        var westB = world.getBiome(bx - bd, by, bz);
        nearbyBiomes.addProperty("north", northB.getKey().getKey().replace("_", " "));
        nearbyBiomes.addProperty("south", southB.getKey().getKey().replace("_", " "));
        nearbyBiomes.addProperty("east", eastB.getKey().getKey().replace("_", " "));
        nearbyBiomes.addProperty("west", westB.getKey().getKey().replace("_", " "));
        report.add("nearby_biomes", nearbyBiomes);

        // Nearby blocks (sample key blocks within radius)
        report.add("nearby_blocks", observeBlocks(loc, world));

        // Nearby entities (other agents, players, mobs)
        report.add("nearby_entities", observeEntities(loc, world));

        // Agent state
        report.addProperty("health", 20);
        report.add("inventory", agent.inventoryToJson());

        // Nearby chat — messages this agent has "heard" since last perception tick
        List<String> heardChat = agent.drainChatBuffer();
        if (!heardChat.isEmpty()) {
            JsonArray chatArray = new JsonArray();
            for (String msg : heardChat) {
                chatArray.add(msg);
            }
            report.add("nearby_chat", chatArray);
        }

        // Village chest contents (if chest exists)
        var chestContents = plugin.getVillageChestContents();
        if (!chestContents.isEmpty() || plugin.getVillageChestLocation() != null) {
            JsonObject chestJson = new JsonObject();
            for (var entry : chestContents.entrySet()) {
                chestJson.addProperty(entry.getKey(), entry.getValue());
            }
            report.add("village_chest", chestJson);
            if (plugin.getVillageChestLocation() != null) {
                Location cl = plugin.getVillageChestLocation();
                report.addProperty("village_chest_distance",
                        (int) loc.distance(cl));
            }
        }

        // Craftable items analysis
        try {
            report.add("craftable", observeCraftable());
        } catch (Throwable e) {
            plugin.getLogger().warning("observeCraftable() failed: " + e.getClass().getName() + ": " + e.getMessage());
            JsonObject empty = new JsonObject();
            empty.add("can_craft", new JsonArray());
            empty.add("almost", new JsonArray());
            report.add("craftable", empty);
        }

        return report;
    }

    private static final Material[] RELEVANT_ITEMS = {
        Material.OAK_PLANKS, Material.BIRCH_PLANKS, Material.SPRUCE_PLANKS,
        Material.JUNGLE_PLANKS, Material.ACACIA_PLANKS, Material.DARK_OAK_PLANKS,
        Material.CHERRY_PLANKS, Material.MANGROVE_PLANKS,
        Material.STICK, Material.CRAFTING_TABLE,
        Material.WOODEN_PICKAXE, Material.WOODEN_AXE, Material.WOODEN_SHOVEL, Material.WOODEN_SWORD,
        Material.STONE_PICKAXE, Material.STONE_AXE, Material.STONE_SHOVEL, Material.STONE_SWORD,
        Material.IRON_PICKAXE, Material.IRON_AXE, Material.IRON_SHOVEL, Material.IRON_SWORD,
        Material.FURNACE, Material.CHEST, Material.TORCH,
        Material.OAK_DOOR, Material.BIRCH_DOOR, Material.SPRUCE_DOOR,
        Material.WOODEN_HOE, Material.STONE_HOE, Material.IRON_HOE,
        Material.SHIELD, Material.BUCKET, Material.SHEARS,
        Material.LADDER, Material.OAK_FENCE, Material.OAK_BOAT,
        Material.BOW, Material.ARROW, Material.BREAD,
    };

    /**
     * Analyze which items the agent can craft with its current inventory.
     * Returns JSON with "can_craft" (have all materials) and "almost" (missing 1-2 items).
     */
    private JsonObject observeCraftable() {
        JsonObject result = new JsonObject();
        JsonArray canCraft = new JsonArray();
        JsonArray almost = new JsonArray();
        Map<String, Integer> inv = agent.getInventory();

        for (Material target : RELEVANT_ITEMS) {
            var recipes = Bukkit.getRecipesFor(new ItemStack(target));
            for (var recipe : recipes) {
                Map<String, Integer> needed = new HashMap<>();
                boolean parsed = false;

                if (recipe instanceof ShapedRecipe shaped) {
                    parsed = parseShapedRecipe(shaped, inv, needed);
                } else if (recipe instanceof ShapelessRecipe shapeless) {
                    parsed = parseShapelessRecipe(shapeless, inv, needed);
                }
                if (!parsed || needed.isEmpty()) continue;

                int resultAmount = recipe.getResult().getAmount();
                Map<String, Integer> missing = new HashMap<>();
                for (var entry : needed.entrySet()) {
                    int has = inv.getOrDefault(entry.getKey(), 0);
                    if (has < entry.getValue()) {
                        missing.put(entry.getKey(), entry.getValue() - has);
                    }
                }

                if (missing.isEmpty()) {
                    JsonObject item = new JsonObject();
                    item.addProperty("item", target.name().toLowerCase());
                    item.addProperty("amount", resultAmount);
                    StringBuilder cost = new StringBuilder();
                    for (var entry : needed.entrySet()) {
                        if (!cost.isEmpty()) cost.append(" + ");
                        cost.append(entry.getValue()).append(" ").append(entry.getKey());
                    }
                    item.addProperty("cost", cost.toString());
                    canCraft.add(item);
                    break;
                } else if (missing.size() <= 2) {
                    int totalMissing = missing.values().stream().mapToInt(Integer::intValue).sum();
                    if (totalMissing <= 4) {
                        JsonObject item = new JsonObject();
                        item.addProperty("item", target.name().toLowerCase());
                        item.addProperty("amount", resultAmount);
                        StringBuilder missingStr = new StringBuilder();
                        for (var entry : missing.entrySet()) {
                            if (!missingStr.isEmpty()) missingStr.append(" + ");
                            missingStr.append(entry.getValue()).append(" ").append(entry.getKey());
                        }
                        item.addProperty("missing", missingStr.toString());
                        almost.add(item);
                        break;
                    }
                }
            }
        }
        result.add("can_craft", canCraft);
        result.add("almost", almost);
        return result;
    }

    private boolean parseShapedRecipe(ShapedRecipe shaped, Map<String, Integer> inv,
                                       Map<String, Integer> needed) {
        // Count how many times each key character appears in the shape pattern
        Map<Character, Integer> keyCounts = new HashMap<>();
        for (String row : shaped.getShape()) {
            for (char c : row.toCharArray()) {
                if (c != ' ') {
                    keyCounts.merge(c, 1, Integer::sum);
                }
            }
        }

        var choices = shaped.getChoiceMap();
        for (var entry : choices.entrySet()) {
            var choice = entry.getValue();
            if (choice == null) continue;
            int count = keyCounts.getOrDefault(entry.getKey(), 1);
            String matched = matchChoice(choice, inv, needed);
            if (matched == null) {
                matched = matchChoiceAny(choice);
            }
            if (matched == null) return false;
            needed.merge(matched, count, Integer::sum);
        }
        return true;
    }

    private boolean parseShapelessRecipe(ShapelessRecipe shapeless, Map<String, Integer> inv,
                                          Map<String, Integer> needed) {
        for (var choice : shapeless.getChoiceList()) {
            String matched = matchChoice(choice, inv, needed);
            if (matched == null) {
                matched = matchChoiceAny(choice);
            }
            if (matched == null) return false;
            needed.merge(matched, 1, Integer::sum);
        }
        return true;
    }

    private String matchChoice(RecipeChoice choice, Map<String, Integer> inv,
                                Map<String, Integer> currentNeeded) {
        if (choice instanceof RecipeChoice.MaterialChoice matChoice) {
            for (Material mat : matChoice.getChoices()) {
                String name = mat.name().toLowerCase();
                int alreadyNeeded = currentNeeded.getOrDefault(name, 0);
                if (inv.getOrDefault(name, 0) > alreadyNeeded) {
                    return name;
                }
            }
        } else if (choice instanceof RecipeChoice.ExactChoice exact) {
            for (var stack : exact.getChoices()) {
                String name = stack.getType().name().toLowerCase();
                int alreadyNeeded = currentNeeded.getOrDefault(name, 0);
                if (inv.getOrDefault(name, 0) > alreadyNeeded) {
                    return name;
                }
            }
        }
        return null;
    }

    private String matchChoiceAny(RecipeChoice choice) {
        if (choice instanceof RecipeChoice.MaterialChoice matChoice) {
            var choices = matChoice.getChoices();
            return choices.isEmpty() ? null : choices.get(0).name().toLowerCase();
        } else if (choice instanceof RecipeChoice.ExactChoice exact) {
            var choices = exact.getChoices();
            return choices.isEmpty() ? null : choices.get(0).getType().name().toLowerCase();
        }
        return null;
    }

    /**
     * Sample key blocks in the agent's vicinity.
     * Reports block types at the agent's feet level and eye level.
     */
    private static final java.util.Set<Material> HIGH_VALUE_BLOCKS = java.util.Set.of(
        Material.OAK_LOG, Material.BIRCH_LOG, Material.SPRUCE_LOG, Material.JUNGLE_LOG,
        Material.ACACIA_LOG, Material.DARK_OAK_LOG, Material.CHERRY_LOG, Material.MANGROVE_LOG,
        Material.COAL_ORE, Material.IRON_ORE, Material.COPPER_ORE, Material.GOLD_ORE,
        Material.DIAMOND_ORE, Material.LAPIS_ORE, Material.REDSTONE_ORE,
        Material.DEEPSLATE_COAL_ORE, Material.DEEPSLATE_IRON_ORE, Material.DEEPSLATE_COPPER_ORE,
        Material.DEEPSLATE_GOLD_ORE, Material.DEEPSLATE_DIAMOND_ORE,
        Material.CRAFTING_TABLE, Material.FURNACE, Material.CHEST
    );

    private JsonArray observeBlocks(Location loc, World world) {
        JsonArray blocks = new JsonArray();
        JsonArray highValueBlocks = new JsonArray();
        int r = Math.min(radius, 12);
        int bx = loc.getBlockX();
        int by = loc.getBlockY();
        int bz = loc.getBlockZ();

        for (int dx = -r; dx <= r; dx += 1) {
            for (int dz = -r; dz <= r; dz += 1) {
                if (dx == 0 && dz == 0) continue;
                int wx = bx + dx;
                int wz = bz + dz;
                if (!world.isChunkLoaded(wx >> 4, wz >> 4)) continue;
                for (int dy = -1; dy <= 8; dy++) {
                    Block block = world.getBlockAt(wx, by + dy, wz);
                    Material mat = block.getType();
                    if (mat == Material.AIR || mat == Material.CAVE_AIR || mat == Material.VOID_AIR)
                        continue;
                    JsonObject b = new JsonObject();
                    b.addProperty("x", wx);
                    b.addProperty("y", by + dy);
                    b.addProperty("z", wz);
                    b.addProperty("type", mat.name());
                    b.addProperty("relative_x", dx);
                    b.addProperty("relative_y", dy);
                    b.addProperty("relative_z", dz);
                    if (HIGH_VALUE_BLOCKS.contains(mat)) {
                        if (highValueBlocks.size() < 20) highValueBlocks.add(b);
                    } else {
                        if (blocks.size() < 60) blocks.add(b);
                    }
                }
                if (blocks.size() >= 60 && highValueBlocks.size() >= 20) break;
            }
            if (blocks.size() >= 60 && highValueBlocks.size() >= 20) break;
        }
        // High-value blocks always included — they were invisible before because
        // 80 dirt/stone blocks filled the cap before any log/ore was reached.
        for (int i = 0; i < highValueBlocks.size(); i++) {
            blocks.add(highValueBlocks.get(i));
        }
        return blocks;
    }

    /**
     * Observe nearby entities: other Valis agents, players, and significant mobs.
     */
    private JsonArray observeEntities(Location loc, World world) {
        JsonArray entities = new JsonArray();
        int r = Math.min(radius, 16);

        for (Entity entity : world.getNearbyEntities(loc, r, r, r)) {
            if (entity.getUniqueId().equals(agent.getNpc() != null ?
                    agent.getNpc().getUniqueId() : null)) continue;

            JsonObject e = new JsonObject();
            e.addProperty("type", entity.getType().name());
            e.addProperty("name", entity.getName());
            e.addProperty("x", entity.getLocation().getBlockX());
            e.addProperty("y", entity.getLocation().getBlockY());
            e.addProperty("z", entity.getLocation().getBlockZ());
            e.addProperty("distance", loc.distance(entity.getLocation()));

            if (entity instanceof Player player) {
                e.addProperty("is_player", true);
                e.addProperty("player_name", player.getName());
            } else {
                e.addProperty("is_player", false);
            }

            entities.add(e);
        }

        return entities;
    }
}
