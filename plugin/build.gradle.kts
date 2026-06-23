// Project Valis - PaperMC Plugin
// A plugin that enables AI agents to inhabit and interact in a Minecraft world

plugins {
    id("java")
    id("com.gradleup.shadow") version "8.3.0"
}

group = "com.valis"
version = "0.1.0-SNAPSHOT"

java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(21))
}

repositories {
    mavenCentral()
    maven("https://repo.papermc.io/repository/maven-public/")
    maven("https://repo.citizensnpcs.co/")
    maven("https://repo.dmulloy2.net/repository/public/")
}

dependencies {
    compileOnly("io.papermc.paper:paper-api:1.21.3-R0.1-SNAPSHOT")
    compileOnly("net.citizensnpcs:citizens-main:2.0.35-SNAPSHOT") {
        exclude(group = "*", module = "*")
    }
    compileOnly("com.comphenix.protocol:ProtocolLib:5.3.0")

    // WebSocket server for agent brain communication
    implementation("org.java-websocket:Java-WebSocket:1.5.7")

    // JSON processing
    implementation("com.google.code.gson:gson:2.11.0")
}

tasks {
    shadowJar {
        archiveClassifier.set("")
        relocate("org.java_websocket", "com.valis.libs.websocket")
        relocate("com.google.gson", "com.valis.libs.gson")
    }

    build {
        dependsOn(shadowJar)
    }

    processResources {
        filesMatching("plugin.yml") {
            expand("version" to version)
        }
    }
}
