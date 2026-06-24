package com.valis.perception;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.valis.ValisPlugin;
import com.valis.agent.VirtualAgent;
import net.citizensnpcs.api.npc.NPC;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;

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
        int bd = 100;
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

        return report;
    }

    /**
     * Sample key blocks in the agent's vicinity.
     * Reports block types at the agent's feet level and eye level.
     */
    private JsonArray observeBlocks(Location loc, World world) {
        JsonArray blocks = new JsonArray();
        int r = Math.min(radius, 8); // Limit block sampling to prevent overload
        int bx = loc.getBlockX();
        int by = loc.getBlockY();
        int bz = loc.getBlockZ();

        for (int dx = -r; dx <= r; dx += 2) {
            for (int dz = -r; dz <= r; dz += 2) {
                if (dx == 0 && dz == 0) continue;

                // Sample at feet level and eye level
                for (int dy = -1; dy <= 1; dy++) {
                    Block block = world.getBlockAt(bx + dx, by + dy, bz + dz);
                    Material mat = block.getType();
                    if (mat != Material.AIR && mat != Material.CAVE_AIR && mat != Material.VOID_AIR) {
                        JsonObject b = new JsonObject();
                        b.addProperty("x", bx + dx);
                        b.addProperty("y", by + dy);
                        b.addProperty("z", bz + dz);
                        b.addProperty("type", mat.name());
                        b.addProperty("relative_x", dx);
                        b.addProperty("relative_y", dy);
                        b.addProperty("relative_z", dz);
                        blocks.add(b);

                        if (blocks.size() >= 50) break; // Cap to avoid huge payloads
                    }
                }
                if (blocks.size() >= 50) break;
            }
            if (blocks.size() >= 50) break;
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
