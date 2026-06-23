# Phase 1 — Setup & Testanleitung

> **Ziel**: PaperMC-Server mit Valis-Plugin starten, Python Agent Brain verbinden, ersten KI-Agenten spawnen und eine vollständige Perception→Plan→Execute-Schleife testen.

---

## 0. Voraussetzungen

| Tool | Version | Check |
|------|---------|-------|
| Java JDK | 21+ | `java -version` |
| Python | 3.11+ | `python --version` |
| Git | beliebig | `git --version` |
| ~2 GB RAM | — | Server + Python |

**Empfohlen für lokale LLM-Tests (optional):**
- [Ollama](https://ollama.com) mit `ollama pull llama3.1` (kostenlos, offline)

---

## 1. Verzeichnisstruktur prüfen

Nach `git clone` sollte dein Workspace so aussehen:

```
minecraft-valis/
├── PLAN.md
├── README.md
├── .gitignore
├── server/
│   ├── plugins/           ← Hier kommt die Plugin-JAR rein
│   └── server.properties.example
├── plugin/                ← Gradle-Projekt (Java)
│   ├── build.gradle.kts
│   ├── settings.gradle.kts
│   └── src/main/java/com/valis/
│       ├── ValisPlugin.java
│       ├── ValisCommand.java
│       ├── agent/VirtualAgent.java
│       ├── bridge/WebSocketBridge.java
│       ├── config/ValisConfig.java
│       ├── execution/ActionExecutor.java
│       ├── perception/WorldObserver.java
│       └── resources/
│           ├── config.yml
│           └── plugin.yml
└── agent-brain/           ← Python-Projekt
    ├── pyproject.toml
    ├── main.py
    ├── agent.py
    ├── llm/providers.py
    ├── memory/
    ├── cognitive/
    ├── bridge/
    └── config/agents.yaml
```

---

## 2. PaperMC-Server aufsetzen

### 2.1 Server-JAR herunterladen

Lade die neueste PaperMC-JAR herunter:

```powershell
# In PowerShell, vom Projekt-Root aus:
cd server

# PaperMC 1.21.3 herunterladen (Build-Nummer kann abweichen)
Invoke-WebRequest -Uri "https://api.papermc.io/v2/projects/paper/versions/1.21.3/builds/82/downloads/paper-1.21.3-82.jar" -OutFile "paper.jar"
```

Falls der Link nicht mehr aktuell ist, besuche https://papermc.io/downloads und lade manuell herunter.

### 2.2 Server vorbereiten

```powershell
# Kopiere die Beispiel-Serverkonfiguration
Copy-Item server.properties.example server.properties

# Akzeptiere die EULA (wichtig!)
@"
eula=true
"@ | Out-File -FilePath eula.txt -Encoding ASCII
```

### 2.3 Abhängigkeiten installieren

Das Valis-Plugin braucht zwei weitere Plugins. Lade sie in `server/plugins/`:

```powershell
cd plugins

# Citizens2 (NPC-API)
Invoke-WebRequest -Uri "https://ci.citizensnpcs.co/job/Citizens2/lastSuccessfulBuild/artifact/dist/target/Citizens-2.0.35-b4046.jar" -OutFile "Citizens.jar"

# ProtocolLib (Packet-Manipulation)
Invoke-WebRequest -Uri "https://github.com/dmulloy2/ProtocolLib/releases/download/5.3.0/ProtocolLib.jar" -OutFile "ProtocolLib.jar"
```

> **Hinweis**: Falls die Citizens- oder ProtocolLib-Links nicht mehr funktionieren, suche auf den jeweiligen GitHub-Release-Seiten nach den aktuellen JARs.

### 2.4 Server testweise starten (ohne Valis-Plugin)

```powershell
cd ..
java -Xmx2G -jar paper.jar nogui
```

Der Server generiert die Welt (`valis_world`) und startet. Warte auf `Done!`, dann beende mit `stop`.

> **Wichtig**: Das erste Mal dauert es ~30 Sekunden, weil die Welt generiert wird.

---

## 3. Valis-Plugin bauen

### 3.1 Gradle Wrapper generieren

```powershell
# Vom Projekt-Root aus:
cd plugin

# Falls du kein Gradle installiert hast, erzeuge den Wrapper:
gradle wrapper --gradle-version 8.10
```

Falls `gradle` nicht gefunden wird, installiere es via `winget install Gradle.Gradle` oder lade es von https://gradle.org/install/.

### 3.2 Plugin kompilieren

```powershell
# Build + fat JAR erzeugen
.\gradlew shadowJar
```

Bei Erfolg erscheint: `BUILD SUCCESSFUL` und die JAR liegt unter:
```
plugin/build/libs/valis-core-0.1.0-SNAPSHOT.jar
```

### 3.3 Plugin in den Server kopieren

```powershell
Copy-Item "build/libs/valis-core-0.1.0-SNAPSHOT.jar" "../server/plugins/"
```

---

## 4. Python Agent Brain aufsetzen

### 4.1 Virtual Environment erstellen

```powershell
# Vom Projekt-Root aus:
cd agent-brain

# Venv anlegen
python -m venv .venv

# Aktivieren (PowerShell)
.venv\Scripts\Activate.ps1

# Falls ExecutionPolicy-Fehler:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 4.2 Abhängigkeiten installieren

```powershell
# Mit pip (falls pyproject.toml nicht via pip install -e . funktioniert)
pip install fastapi uvicorn websockets openai anthropic chromadb numpy pydantic pyyaml httpx python-dotenv
```

Oder falls `pip install -e .` funktioniert:

```powershell
pip install -e .
```

### 4.3 LLM-API-Keys konfigurieren

```powershell
# .env-Datei aus Vorlage erstellen
Copy-Item .env.example .env
```

Öffne `.env` in einem Editor und trage deine API-Keys ein:

```ini
# Mindestens einen dieser Keys brauchst du:

# OpenAI (empfohlen, beste Ergebnisse)
OPENAI_API_KEY=sk-...

# ODER Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# ODER Ollama (lokal, kostenlos) — siehe Abschnitt 6
```

**Für lokale Tests ohne API-Kosten** kannst du Ollama verwenden (siehe Abschnitt 6).

---

## 5. Alles starten & testen

Die Startreihenfolge ist wichtig: **Erst Minecraft-Server, dann Agent Brain**.

### 5.1 Terminal 1 — Minecraft-Server

```powershell
cd server
java -Xmx2G -jar paper.jar nogui
```

Warte bis `Done!` erscheint, dann siehst du auch:
```
[Valis] === Project Valis: AI Civilization ===
[Valis] WebSocket bridge started on port 9876
[Valis] Valis plugin enabled successfully.
```

Falls Fehler erscheinen (`Citizens plugin not found!` o.ä.), prüfe ob Citizens.jar und ProtocolLib.jar in `server/plugins/` liegen.

### 5.2 Terminal 2 — Agent Brain

```powershell
cd agent-brain
.venv\Scripts\Activate.ps1
python main.py
```

Erwartete Ausgabe:
```
=== Project Valis: Agent Brain Service ===
Connecting to Minecraft server at localhost:9876
Connected to Minecraft server.
```

### 5.3 Ersten Agenten spawnen

Im Minecraft-Server-Terminal (oder als OP-Spieler im Chat):

```
valis spawn TestAgent explorer
```

Der Agent Brain loggt:
```
Agent created: TestAgent (explorer) [a1b2c3d4e5f6]
Agent spawned: TestAgent (explorer). Total agents: 1
```

Im Minecraft-Server-Terminal:
```
[Valis] Spawn request sent for agent: TestAgent
[Valis] Agent spawned: TestAgent at ...
```

### 5.4 Prüfen ob der Agent läuft

Im Minecraft-Server-Terminal:

```
valis list
```

Sollte zeigen:
```
[Valis] Active agents (1):
  - TestAgent
```

```
valis status
```

Sollte zeigen:
```
[Valis] Status:
  WebSocket: Running
  Agents: 1
```

---

## 6. Optional: Lokales LLM via Ollama (kostenlos)

Wenn du keine OpenAI/Anthropic-Keys hast, kannst du Ollama nutzen:

### 6.1 Ollama installieren

1. Lade Ollama von https://ollama.com herunter
2. Installiere und starte es
3. Lade ein Modell:

```powershell
ollama pull llama3.1
# Oder ein leichteres Modell:
ollama pull qwen2.5:3b
```

### 6.2 Agent für Ollama konfigurieren

Öffne `agent-brain/agent.py` und ändere in der `AgentConfig` die Defaults — oder besser: Passe `.env` an:

```ini
VALIS_DEFAULT_LLM=ollama
VALIS_DEFAULT_MODEL=llama3.1
```

Und im `agent-brain/config/agents.yaml`:

```yaml
default_agent:
  llm_provider: ollama
  llm_model: llama3.1
```

### 6.3 Ollama Provider testen

```powershell
cd agent-brain
.venv\Scripts\Activate.ps1
python -c "
import asyncio
from llm.providers import create_llm

async def test():
    llm = create_llm('ollama', model='llama3.1')
    resp = await llm.chat([{'role': 'user', 'content': 'Say hello in one word.'}])
    print('Response:', resp)

asyncio.run(test())
"
```

---

## 7. Fehlerbehebung

### "Citizens plugin not found!"
→ `Citizens.jar` und `ProtocolLib.jar` fehlen in `server/plugins/`. Siehe Abschnitt 2.3.

### "WebSocket connection refused"
→ Der Minecraft-Server läuft nicht oder der WebSocket-Port ist falsch. Prüfe `server/plugins/valis-core/config.yml`:
```yaml
websocket:
  port: 9876
```

### "Agent created but no perception data"
→ Der Agent ist gespawnt, aber die Perception-Loop sendet keine Daten. Prüfe:
- Steht der NPC in einer geladenen Welt?
- Ist `perception-interval-ticks` in `config.yml` zu hoch? (Standard: 20 Ticks = 1 Sekunde)

### "openai.APIError: Invalid API key"
→ `.env`-Datei prüfen. Der Key muss mit `sk-` beginnen.

### Python-Modul nicht gefunden
```powershell
# Stelle sicher, dass du im richtigen Verzeichnis bist:
cd agent-brain
.venv\Scripts\Activate.ps1

# Installiere alle Abhängigkeiten:
pip install fastapi uvicorn websockets openai anthropic chromadb numpy pydantic pyyaml httpx python-dotenv
```

### Gradle Build schlägt fehl
```powershell
# Prüfe Java-Version:
java -version  # Muss 21+ sein

# Gradle Wrapper neu generieren:
cd plugin
gradle wrapper --gradle-version 8.10
.\gradlew shadowJar --info  # --info für mehr Details
```

---

## 8. Was passiert unter der Haube?

Wenn alles läuft, passiert folgender Kreislauf:

```
┌─────────────────────────────────────────────────────────┐
│  Minecraft Server                                       │
│                                                         │
│  WorldObserver erfasst:                                 │
│    • Position des NPC                                   │
│    • Blöcke im Umkreis (16 Blöcke)                      │
│    • Entities (andere Agenten, Mobs, Spieler)            │
│    • Tageszeit, Wetter                                  │
│                                                         │
│  → Sendet JSON "perception" über WebSocket               │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Python Agent Brain                                     │
│                                                         │
│  ValisAgent.cognitive_tick():                           │
│    1. CognitiveController.decide()  ← PIANO Bottleneck  │
│    2. GoalGenerator.generate_goals()                    │
│    3. Planner.decide_action()       ← LLM-Aufruf        │
│    4. ActionAwareness.expect()      ← Erwartung setzen  │
│    5. Reflection.reflect()          ← wenn Schwellwert  │
│    6. Memory.add_event()            ← Erfahrung speichern│
│                                                         │
│  → Sendet JSON "agent_action" über WebSocket             │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Minecraft Server                                       │
│                                                         │
│  ActionExecutor führt aus:                              │
│    move_to(x,y,z) → Citizens-Navigation                 │
│    mine_block(x,y,z) → Block abbauen                    │
│    place_block(typ,x,y,z) → Block platzieren            │
│    chat(text) → NPC sagt etwas                          │
│                                                         │
│  → Sendet JSON "action_result" zurück                   │
└─────────────────────────────────────────────────────────┘
```

---

## 9. Nächste Schritte (Phase 2)

Nach erfolgreichem Test von Phase 1:

1. **Retrieval-Qualität prüfen** — Werden relevante Erinnerungen abgerufen?
2. **Planungs-Prompts tunen** — Sind die Aktionsentscheidungen sinnvoll?
3. **Mehrere Agenten spawnen** — `/valis spawn Agent2 farmer` etc.
4. **Erste soziale Interaktionen** — Agenten sollen sich gegenseitig wahrnehmen
5. **Memory-Persistenz testen** — Agent stoppen, neu starten, Erinnerungen noch da?
