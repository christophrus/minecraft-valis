package com.valis;

import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.jetbrains.annotations.NotNull;

/**
 * Admin commands for controlling the Valis simulation.
 * /valis spawn <name> [personality] - Spawn a new AI agent
 * /valis despawn <name> - Remove an AI agent
 * /valis spectate <name> - Spectate an AI agent (follow camera)
 * /valis tp <name> - Teleport to an AI agent
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
            sender.sendMessage("§6[Valis] §eUsage: /valis <spawn|despawn|tp|spectate|list|status>");
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
                sender.sendMessage("§6[Valis] §aNow spectating " + name + ". Inventory shown in action bar. Use /valis tp <name> to stop.");
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
