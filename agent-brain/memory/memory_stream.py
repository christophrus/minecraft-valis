"""
Associative Memory Stream — the core memory system for AI agents.

Based on the Generative Agents paper (Park et al., 2023):
Each agent stores a complete record of experiences as natural language,
synthesizes memories into higher-level reflections, and retrieves them
dynamically to plan behavior.

Memory nodes are stored in SQLite (structured data) and ChromaDB (embeddings
for semantic search).
"""

import datetime
import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("valis.memory")


@dataclass
class ConceptNode:
    """A single node in the associative memory stream.

    Can be an Event (something that happened), a Thought (reflection/insight),
    or a Chat (conversation record).
    """
    node_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    node_type: str = "event"  # "event", "thought", "chat", "plan"
    content: str = ""
    created: datetime.datetime = field(default_factory=datetime.datetime.now)
    expiration: datetime.datetime | None = None
    importance: float = 0.5  # poignancy score (0-1)
    embedding: list[float] | None = None
    keywords: list[str] = field(default_factory=list)
    subject: str = ""
    predicate: str = ""
    object: str = ""  # subject-predicate-object triple
    evidence_ids: list[str] = field(default_factory=list)  # IDs of supporting nodes


class MemoryStream:
    """
    Associative memory stream for a single agent.
    Stores events, thoughts, chats, and plans.
    Provides retrieval based on recency, relevance, and importance.

    Uses SQLite for structured storage and ChromaDB for vector search.
    """

    def __init__(
        self,
        agent_name: str,
        data_dir: str = "data",
        embedding_fn=None,
    ):
        self.agent_name = agent_name
        self.data_dir = Path(data_dir) / agent_name
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._db_path = str(self.data_dir / "memory.db")
        self._embedding_fn = embedding_fn
        self._nodes: dict[str, ConceptNode] = {}

        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database for memory storage."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created TEXT NOT NULL,
                    expiration TEXT,
                    importance REAL DEFAULT 0.5,
                    keywords TEXT DEFAULT '[]',
                    subject TEXT DEFAULT '',
                    predicate TEXT DEFAULT '',
                    object TEXT DEFAULT '',
                    evidence_ids TEXT DEFAULT '[]',
                    embedding BLOB
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON nodes(created)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON nodes(node_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_importance ON nodes(importance)")
            conn.commit()

    async def add_node(self, node: ConceptNode) -> str:
        """Add a memory node to the stream and persist it."""
        # Generate embedding if function is available
        if self._embedding_fn and node.embedding is None:
            try:
                node.embedding = await self._embedding_fn(node.content)
            except Exception as e:
                logger.warning(f"Failed to embed memory node: {e}")

        self._nodes[node.node_id] = node

        # Persist to SQLite
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO nodes
                   (node_id, node_type, content, created, expiration,
                    importance, keywords, subject, predicate, object,
                    evidence_ids, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    node.node_id,
                    node.node_type,
                    node.content,
                    node.created.isoformat(),
                    node.expiration.isoformat() if node.expiration else None,
                    node.importance,
                    json.dumps(node.keywords),
                    node.subject,
                    node.predicate,
                    node.object,
                    json.dumps(node.evidence_ids),
                    json.dumps(node.embedding).encode() if node.embedding else None,
                ),
            )
            conn.commit()

        return node.node_id

    async def add_event(self, content: str, importance: float = 0.5,
                        subject: str = "", predicate: str = "", object: str = "",
                        keywords: list[str] | None = None) -> str:
        """Add an event to the memory stream."""
        node = ConceptNode(
            node_type="event",
            content=content,
            importance=importance,
            subject=subject,
            predicate=predicate,
            object=object,
            keywords=keywords or [],
        )
        return await self.add_node(node)

    async def add_thought(self, content: str, importance: float = 0.5,
                          evidence_ids: list[str] | None = None) -> str:
        """Add a thought/reflection to the memory stream."""
        node = ConceptNode(
            node_type="thought",
            content=content,
            importance=importance,
            evidence_ids=evidence_ids or [],
        )
        return await self.add_node(node)

    async def add_chat(self, content: str, speaker: str, listener: str) -> str:
        """Add a conversation record to the memory stream."""
        node = ConceptNode(
            node_type="chat",
            content=content,
            subject=speaker,
            predicate="talked to",
            object=listener,
            importance=0.3,
        )
        return await self.add_node(node)

    def get_node(self, node_id: str) -> ConceptNode | None:
        """Get a node by ID."""
        if node_id in self._nodes:
            return self._nodes[node_id]
        # Try to load from DB
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            if row:
                node = self._row_to_node(row)
                self._nodes[node_id] = node
                return node
        return None

    def get_recent(self, n: int = 20, node_type: str | None = None) -> list[ConceptNode]:
        """Get the most recent memory nodes."""
        with sqlite3.connect(self._db_path) as conn:
            query = "SELECT * FROM nodes"
            params: list = []
            if node_type:
                query += " WHERE node_type = ?"
                params.append(node_type)
            query += " ORDER BY created DESC LIMIT ?"
            params.append(n)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_node(row) for row in rows]

    def search_by_keywords(self, keywords: list[str], limit: int = 20) -> list[ConceptNode]:
        """Search memory by keyword matching."""
        results = []
        with sqlite3.connect(self._db_path) as conn:
            for kw in keywords:
                rows = conn.execute(
                    "SELECT * FROM nodes WHERE content LIKE ? LIMIT ?",
                    (f"%{kw}%", limit),
                ).fetchall()
                for row in rows:
                    node = self._row_to_node(row)
                    if node.node_id not in {r.node_id for r in results}:
                        results.append(node)
        return results[:limit]

    def get_all_ids(self) -> list[str]:
        """Get all node IDs."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("SELECT node_id FROM nodes").fetchall()
            return [row[0] for row in rows]

    def size(self) -> int:
        """Get total number of nodes."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
            return row[0] if row else 0

    def _row_to_node(self, row: tuple) -> ConceptNode:
        """Convert a database row to a ConceptNode."""
        return ConceptNode(
            node_id=row[0],
            node_type=row[1],
            content=row[2],
            created=datetime.datetime.fromisoformat(row[3]),
            expiration=datetime.datetime.fromisoformat(row[4]) if row[4] else None,
            importance=row[5],
            keywords=json.loads(row[6]) if row[6] else [],
            subject=row[7],
            predicate=row[8],
            object=row[9],
            evidence_ids=json.loads(row[10]) if row[10] else [],
            embedding=json.loads(row[11].decode()) if row[11] else None,
        )
