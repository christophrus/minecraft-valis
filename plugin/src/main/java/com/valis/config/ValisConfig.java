package com.valis.config;

import com.valis.ValisPlugin;

/**
 * Configuration management for the Valis plugin.
 * Reads settings from config.yml.
 */
public class ValisConfig {

    private final ValisPlugin plugin;
    private final int wsPort;
    private final int perceptionRadius;
    private final int perceptionIntervalTicks;
    private final String worldName;

    public ValisConfig(ValisPlugin plugin) {
        this.plugin = plugin;
        plugin.saveDefaultConfig();

        var config = plugin.getConfig();
        this.wsPort = config.getInt("websocket.port", 9876);
        this.perceptionRadius = config.getInt("agent.perception-radius", 16);
        this.perceptionIntervalTicks = config.getInt("agent.perception-interval-ticks", 20);
        this.worldName = config.getString("world.name", "valis_world");
    }

    public int getWebSocketPort() { return wsPort; }
    public int getPerceptionRadius() { return perceptionRadius; }
    public int getPerceptionIntervalTicks() { return perceptionIntervalTicks; }
    public String getWorldName() { return worldName; }
}
