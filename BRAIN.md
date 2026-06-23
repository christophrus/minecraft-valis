# Project Valis — Brain Dump

> Lebendiges Wissenstagebuch. Wird bei jeder Session aktualisiert.
> Stand: 2026-06-24 — Phase 1 ✅, erster Agent läuft, Perception-Flow debuggt

---

## 1. Architektur-Entscheidungen

| Entscheidung | Gewählt | Begründung |
|-------------|---------|------------|
| Server-Typ | PaperMC 26.1.2 (Build #72) | Einzige Version, die mit Citizens2 Build 4210 läuft |
| Java-Runtime | JDK 25 (Eclipse Adoptium Temurin) | Paper 26.x erfordert Java 25+ |
| Java-Build | JDK 21 (Zulu) für Gradle | Gradle 8.10 braucht ≤ JDK 21 |
| NPC-System | Citizens2 (Build 4210) | A*-Pathfinding, Player-Skins, getestet |
| Agent Brain | Python 3.11+ (asyncio) | Bestes LLM-Ökosystem |
| Plugin-Bridge | WebSocket (java-websocket ↔ Python websockets) | Bidirektional, JSON-Protokoll |
| LLM-Backend | Ollama (Mistral 7B) / DeepSeek / OpenAI / Anthropic | Multi-Provider. Default: Ollama lokal. DeepSeek empfohlen für Qualität+Kosten. |
| Plugin-API | `paper-api:1.21.4-R0.1-SNAPSHOT` | Paper 26.x API nicht in Maven; 1.21.4 rückwärtskompatibel |

---

## 2. Citizens2 Kompatibilität — Die ganze Saga

### Das Problem
Citizens2 Build 4210 meldete `CITIZENS_INCOMPATIBLE` auf PaperMC 1.21.1, 1.21.3, 1.21.4.

### Root Cause (nach Quellcode-Analyse)
`NMS.loadBridge()` in `CitizensDev/Citizens2` → `main/src/main/java/net/citizensnpcs/util/NMS.java:764`:

```java
int[] version = SpigotUtil.getVersion(); // z.B. {1, 21, 4} für Paper 1.21.4
switch (version[1]) {
    case 21:
        if (version[2] < 9)       rev = "v1_21_R5";  // Paper 1.21.0–1.21.8 → NICHT IN JAR
        else if (version[2] < 11) rev = "v1_21_R6";  // Paper 1.21.9–1.21.10
        else                      rev = "v1_21_R7";  // Paper 1.21.11+
}
switch (version[0]) {
    case 26:
    case 27:
        rev = "v26_" + version[1] + "_R1";  // Paper 26.x → IN JAR ✅
}
```

- Paper 1.21.x → `v1_21_R5` wird gewählt, aber Build 4210 enthält nur `v1_21_R7` → `ClassNotFoundException`
- Paper 26.1.2 → `v26_1_R1` wird gewählt, ist im JAR → funktioniert!

### Citizens JAR-Inhalt (Build 4210)
Nur `v1_21_R7`, `v26_1_R1`, `v26_2_R1` sind enthalten. `v1_21_R5` fehlt.

### Was NICHT funktioniert hat
- PaperMC 1.21.1 → ❌ (`v1_21_R5` nicht in JAR)
- PaperMC 1.21.3 → ❌ (`v1_21_R5` nicht in JAR)
- PaperMC 1.21.4 → ❌ (`v1_21_R5` nicht in JAR)
- Citizens Version-Check deaktivieren → ❌ (geht nicht, `loadBridge()` ist hardcoded)
- ArmorStand-Workaround → funktionierte als Fallback, aber ohne Pathfinding

### Aktueller funktionierender Stack
```
PaperMC 26.1.2 (Build #72) + JDK 25
├── Citizens2 v2.0.43 (Build 4210) → v26_1_R1 NMS ✅
├── ProtocolLib v5.4.0 ✅
└── valis-core v0.1.0-SNAPSHOT ✅
```

---

## 3. Server-Start-Prozedur

### PaperMC herunterladen
```powershell
# Paper 26.1.2 Build #72
Invoke-WebRequest -Uri "https://fill-data.papermc.io/v1/objects/0555a0b0468a5198d8fb1a16e1f9e95c81a917a2dc8f2e09867b4044742f6401/paper-26.1.2-72.jar" -OutFile "server/paper.jar"
```

### Citizens2 herunterladen
```powershell
Invoke-WebRequest -Uri "https://ci.citizensnpcs.co/job/Citizens2/lastSuccessfulBuild/artifact/dist/target/Citizens-2.0.43-b4210.jar" -OutFile "server/plugins/Citizens.jar"
```

### Server starten (WICHTIG!)
```powershell
# Paper 26.x braucht JDK 25! NICHT JDK 21 verwenden!
Start-Process -FilePath "C:\Users\lorus\AppData\Local\Programs\Eclipse Adoptium\jdk-25.0.1.8-hotspot\bin\java.exe" `
    -ArgumentList "-Xmx2G", "-jar", "d:\Github\minecraft-valis\server\paper.jar", "nogui" `
    -WorkingDirectory "d:\Github\minecraft-valis\server" -NoNewWindow -Wait
```

⚠️ **Falle**: Der `cd`-Befehl in PowerShell-Terminals wird vom Tool manchmal nicht übernommen.
→ Immer `Start-Process` mit explizitem `-WorkingDirectory` verwenden!

---

## 4. Plugin-Build-Prozedur

```powershell
$env:JAVA_HOME = "C:\Program Files\Zulu\zulu-21"  # JDK 21 für Gradle!
cd d:\Github\minecraft-valis\plugin
.\gradlew.bat clean shadowJar
Copy-Item "build\libs\valis-core-0.1.0-SNAPSHOT.jar" "..\server\plugins\" -Force
```

### Gradle-Wrapper
- Wrapper manuell erstellt: `gradle/wrapper/gradle-wrapper.properties` + `gradlew.bat`
- Gradle 8.10, braucht JDK 21 (nicht JDK 25!)
- `JAVA_HOME` muss auf JDK 21 zeigen, sonst: `FAILURE: 25.0.1`

---

## 5. Plugin-Dateien (was wo ist)

| Datei | Zweck |
|-------|-------|
| `ValisPlugin.java` | Plugin-Lifecycle, Agent-Registry, Citizens/ProtocolLib-Checks |
| `ValisCommand.java` | `/valis spawn|despawn|list|status` |
| `ValisConfig.java` | YAML-Konfiguration (WebSocket-Port, Perception-Radius) |
| `WebSocketBridge.java` | Bidirektionale JSON-Kommunikation mit Python |
| `VirtualAgent.java` | Citizens-NPC-Manager (Spawn, Despawn, Perception-Loop) |
| `WorldObserver.java` | Welt-Beobachtung (Blöcke, Entities, Zeit, Wetter) |
| `ActionExecutor.java` | Aktionsausführung (moveTo, mineBlock, placeBlock, chat) |

---

## 6. Python Agent Brain

### Wichtige Dateien
| Datei | Zweck |
|-------|-------|
| `main.py` | Async-Einstiegspunkt, WebSocket-Connect, Tick-Loop |
| `agent.py` | `ValisAgent` + `AgentManager` (Cognitive Loop) |
| `llm/providers.py` | Multi-LLM: OpenAI, Anthropic, DeepSeek, Ollama |
| `memory/memory_stream.py` | Assoziativer Memory (SQLite + Embeddings) |
| `memory/retrieval.py` | Gewichtete Retrieval (Recency × Relevance × Importance) |
| `cognitive/controller.py` | PIANO Cognitive Controller (Bottleneck) |
| `cognitive/planning.py` | Tagesplan + Aktionsauswahl |
| `cognitive/reflection.py` | Schwellenwert-Reflexion |
| `cognitive/social_awareness.py` | Sentiment-Graph zwischen Agenten |
| `cognitive/action_awareness.py` | Erwartete vs. tatsächliche Ergebnisse |
| `cognitive/goal_generation.py` | Ziel-Generierung |
| `bridge/protocol.py` | JSON-Nachrichtenprotokoll |
| `bridge/client.py` | WebSocket-Client |

### Setup
```powershell
cd agent-brain
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install fastapi uvicorn websockets openai anthropic chromadb numpy pydantic pyyaml httpx python-dotenv
```

---

## 7. Bekannte Fallstricke

1. **JDK-Version**: Paper 26.x braucht JDK 25, Gradle braucht JDK 21. Nicht verwechseln!
2. **CWD**: `Start-Process` mit `-WorkingDirectory` ist Pflicht; `cd` reicht nicht.
3. **EULA**: `eula.txt` muss `eula=true` enthalten.
4. **Port-Konflikt**: Alten Server vor Neustart killen (`Get-Process java | Stop-Process -Force`).
5. **Paper-Remapping**: Paper 26.x braucht KEIN Plugin-Remapping (Mojang Mappings nativ).
6. **Citizens `speak()` API**: Hat sich in neueren Versionen geändert (braucht `SpeechContext`).
   → Workaround: Chat-Nachricht direkt via `player.sendMessage()` broadcasten.
7. **`getCommand("valis")`**: Muss in `plugin.yml` unter `commands:` registriert sein, sonst NPE.

---

## 8. Phase 2 — Status

| Aufgabe | Status |
|---------|--------|
| Ollama (lokal, Mistral) getestet | ✅ Funktioniert |
| Ersten Agenten spawnen | ✅ `valis spawn TestAgent explorer` |
| NPC erscheint in Minecraft | ✅ Citizens-NPC am Spawn |
| Brain ↔ Server WebSocket | ✅ Verbindet, reconnectet |
| Perception-Datenfluss | ⚠️ Brain empfängt Daten, Cognitive Loop wartet |
| LLM-gesteuerte Aktion ausgeführt | ❌ Noch nicht beobachtet |
| Memory-Persistenz | ❌ Noch nicht getestet |
| Mehrere Agenten (2-5) | ❌ Noch nicht |

---

## 9. Git-Commits (Referenz)

| Commit | Beschreibung |
|--------|-------------|
| `a29efb9` | Initial commit: Plan und Projekt-Scaffolding |
| `46dbff1` | Phase 1: Plugin-Skeleton, Python Agent Brain, WebSocket-Bridge |
| `ddf52a7` | SETUP.md mit Setup- & Testanleitung |
| `dab7d81` | Fix: Citizens entfernt → ArmorStand (Paper 1.21.1) |
| `f6145b6` | **Phase 1 COMPLETE**: PaperMC 26.1.2 + Citizens2 + alles aktiv |
| `fc36d4d` | BRAIN.md + Repo-Memory für Cross-Session-Wissen |

---

## 10. Bugs dieser Session (2026-06-24) — ALLE BEHOBEN

| Bug | Ursache | Fix |
|-----|---------|-----|
| `ImportError: attempted relative import` | Python als Script statt Package gestartet | `sys.path.insert(0, ...)` in main.py + try/except in kognitiven Modulen |
| `Missing credentials (OpenAI)` | `AgentConfig` hart auf `openai` | Liest jetzt `VALIS_DEFAULT_LLM`/`VALIS_DEFAULT_MODEL` aus `.env` |
| Agent-Name leer beim Spawn | JSON `data`-Feld verschachtelt | `bridge/client.py`: liest `agent_name` + `data.personality` |
| NPC wurde nicht gespawnt | `/valis spawn` nur an Brain, keine Antwort | Direkter Spawn in `ValisCommand.java` + Brain-Benachrichtigung |
| Powershell `&`-Syntax | Pfad mit Leerzeichen braucht `&` davor | Immer `& "pfad\java.exe"` |

---

## 11. Startbefehle (REFERENZ)

### Terminal 1 — Minecraft Server
```powershell
Get-Process -Name "java" -ErrorAction SilentlyContinue | Stop-Process -Force
Set-Location "d:\Github\minecraft-valis\server"
& "C:\Users\lorus\AppData\Local\Programs\Eclipse Adoptium\jdk-25.0.1.8-hotspot\bin\java.exe" -Xmx2G -jar "d:\Github\minecraft-valis\server\paper.jar" nogui
```

### Terminal 2 — Agent Brain
```powershell
Set-Location "d:\Github\minecraft-valis\agent-brain"
& ".venv\Scripts\python.exe" main.py
```

### Agent spawnen (in Terminal 1 nach Brain-Connect)
```
valis spawn TestAgent explorer
valis list
valis status
```

### Plugin neu bauen
```powershell
$env:JAVA_HOME = "C:\Program Files\Zulu\zulu-21"
Set-Location "d:\Github\minecraft-valis\plugin"
.\gradlew.bat shadowJar
Copy-Item "build\libs\valis-core-0.1.0-SNAPSHOT.jar" "..\server\plugins\" -Force
```

---

## 12. Offene Fragen & Ideen

- Perception-Tick: Warum produziert der Brain keine Cognitive-Tick-Logs? Timeout oder kein Perception-Event?
- `chromadb` wurde nicht installiert — Embeddings funktionieren nicht, Memory Retrieval ohne Vektor-Suche
- PaperMC 26.x API-Artefakt in Maven? Aktuell `1.21.4` als Workaround
- ProtocolLib 5.4.0 warnt "Version 26.1.2 not tested"
- Docker-Container für reproduzierbare Umgebung?
