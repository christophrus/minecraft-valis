package com.valis;

import org.bukkit.Bukkit;
import org.bukkit.Material;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.inventory.Inventory;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.meta.ItemMeta;
import org.jetbrains.annotations.NotNull;

/**
 * Admin commands for controlling the Valis simulation.
 * /valis spawn <name> [personality] - Spawn a new AI agent
 * /valis despawn <name> - Remove an AI agent
 * /valis spectate <name> - Spectate an AI agent (follow camera)
 * /valis tp <name> - Teleport to an AI agent
 * /valis inv <name> - View agent inventory
 * /valis list - List all active agents
 * /valis status - Show simulation status
 */
public class ValisCommand implements CommandExecutor {

    private final ValisPlugin plugin;

    public ValisCommand(ValisPlugin plugin) {
        this.plugin = plugin;
    }

    @Override
    public boolean onCommand(@NotNull CommandSender sender, @NotNull Command cmd,
                             @NotNull String label, String[] args) {
        if (args.length == 0) {
            sender.sendMessage("§6[Valis] §eUsage: /valis <spawn|despawn|tp|spectate|inv|list|status>");
            return true;
        }

        switch (args[0].toLowerCase()) {
            case "spawn" -> {
                if (args.length < 2) {
                    sender.sendMessage("§6[Valis] §eUsage: /valis spawn <name> [personality]");
                    return true;
                }
                String name = args[1];
                String personality = args.length > 2 ? args[2] : "default";

                // Spawn directly
                var world = plugin.getServer().getWorld(plugin.getValisConfig().getWorldName());
                var loc = world.getSpawnLocation();
                var agent = new com.valis.agent.VirtualAgent(plugin, name, personality, loc);
                agent.spawn();
                plugin.getAgents().put(name, agent);
                agent.startPerceptionLoop();
                sender.sendMessage("§6[Valis] §aAgent spawned: " + name + " at spawn");

                // Also notify the brain
                var data = new com.google.gson.JsonObject();
                data.addProperty("name", name);
                data.addProperty("personality", personality);
                plugin.getWsBridge().sendToBrain("spawn_agent", name, data);
            }
            case "despawn" -> {
                if (args.length < 2) {
                    sender.sendMessage("§6[Valis] §eUsage: /valis despawn <name>");
                    return true;
                }
                String name = args[1];

                // Despawn directly
                var agent = plugin.getAgents().remove(name);
                if (agent != null) {
                    agent.despawn();
                    sender.sendMessage("§6[Valis] §cAgent despawned: " + name);
                    plugin.getWsBridge().sendToBrain("despawn_agent", name, null);
                } else {
                    sender.sendMessage("§6[Valis] §7Agent not found: " + name);
                }
            }
            case "list" -> {
                var agents = plugin.getAgents();
                if (agents.isEmpty()) {
                    sender.sendMessage("§6[Valis] §7No active agents.");
                } else {
                    sender.sendMessage("§6[Valis] §eActive agents (" + agents.size() + "):");
                    for (var entry : agents.entrySet()) {
                        sender.sendMessage("  §7- §f" + entry.getKey());
                    }
                }
            }
            case "spectate" -> {
                if (args.length < 2) {
                    sender.sendMessage("§6[Valis] §eUsage: /valis spectate <name>");
                    return true;
                }
                String name = args[1];
                var agent = plugin.getAgents().get(name);
                if (agent == null) {
                    sender.sendMessage("§6[Valis] §7Agent not found: " + name);
                    return true;
                }
                var player = plugin.getServer().getPlayer(sender.getName());
                if (player == null) {
                    sender.sendMessage("§6[Valis] §cOnly players can use this command.");
                    return true;
                }
                var npc = agent.getNpc();
                if (npc == null || !npc.isSpawned()) {
                    sender.sendMessage("§6[Valis] §7Agent NPC not spawned.");
                    return true;
                }
                var entity = npc.getEntity();
                if (entity == null) {
                    sender.sendMessage("§6[Valis] §7Agent entity not available.");
                    return true;
                }
                player.setGameMode(org.bukkit.GameMode.SPECTATOR);
                player.setSpectatorTarget(entity);
                plugin.startSpectating(player, name);
                sender.sendMessage("§6[Valis] §aNow spectating " + name + ". Use /valis tp <name> to stop.");
            }
            case "tp" -> {
                if (args.length < 2) {
                    sender.sendMessage("§6[Valis] §eUsage: /valis tp <name>");
                    return true;
                }
                String name = args[1];
                var agent = plugin.getAgents().get(name);
                if (agent == null) {
                    sender.sendMessage("§6[Valis] §7Agent not found: " + name);
                    return true;
                }
                var player = plugin.getServer().getPlayer(sender.getName());
                if (player == null) {
                    sender.sendMessage("§6[Valis] §cOnly players can use this command.");
                    return true;
                }
                player.teleport(agent.getLocation());
                player.setGameMode(org.bukkit.GameMode.SURVIVAL);
                plugin.stopSpectating(player);
                sender.sendMessage("§6[Valis] §aTeleported to " + name);
            }
            case "inv" -> {
                if (args.length < 2) {
                    sender.sendMessage("§6[Valis] §eUsage: /valis inv <name>");
                    return true;
                }
                if (!(sender instanceof Player player)) {
                    sender.sendMessage("§6[Valis] §cOnly players can use this command.");
                    return true;
                }
                String name = args[1];
                var agent = plugin.getAgents().get(name);
                if (agent == null) {
                    sender.sendMessage("§6[Valis] §7Agent not found: " + name);
                    return true;
                }
                var inv = agent.getInventory();
                int size = ((Math.max(inv.size(), 9) + 8) / 9) * 9;  // round up to multiple of 9
                if (size > 54) size = 54;
                if (size < 9) size = 9;
                Inventory gui = Bukkit.createInventory(null, size, "§b" + name + "'s Inventory");
                int slot = 0;
                for (var entry : inv.entrySet()) {
                    Material mat = Material.matchMaterial(entry.getKey().toUpperCase());
                    if (mat == null || mat == Material.AIR) continue;
                    int count = Math.min(entry.getValue(), mat.getMaxStackSize());
                    ItemStack item = new ItemStack(mat, count);
                    gui.setItem(slot++, item);
                }
                player.openInventory(gui);
                sender.sendMessage("§6[Valis] §aViewing " + name + "'s inventory.");
            }
            case "status" -> {
                sender.sendMessage("§6[Valis] §eStatus:");
                sender.sendMessage("  §7WebSocket: §f" + (plugin.getWsBridge().isRunning() ? "§aRunning" : "§cStopped"));
                sender.sendMessage("  §7Agents: §f" + plugin.getAgents().size());
            }
            default -> sender.sendMessage("§6[Valis] §cUnknown subcommand: " + args[0]);
        }
        return true;
    }
}
