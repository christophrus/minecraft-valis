package com.valis;

import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.jetbrains.annotations.NotNull;

/**
 * Admin commands for controlling the Valis simulation.
 * /valis spawn <name> [personality] - Spawn a new AI agent
 * /valis despawn <name> - Remove an AI agent
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
            sender.sendMessage("§6[Valis] §eUsage: /valis <spawn|despawn|list|status>");
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
                plugin.getWsBridge().sendAgentSpawn(name, personality);
                sender.sendMessage("§6[Valis] §aSpawn request sent for agent: " + name);
            }
            case "despawn" -> {
                if (args.length < 2) {
                    sender.sendMessage("§6[Valis] §eUsage: /valis despawn <name>");
                    return true;
                }
                String name = args[1];
                plugin.getWsBridge().sendAgentDespawn(name);
                sender.sendMessage("§6[Valis] §cDespawn request sent for agent: " + name);
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
