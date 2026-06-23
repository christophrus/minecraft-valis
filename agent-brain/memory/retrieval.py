"""
Memory Retrieval — weighted retrieval from the associative memory stream.

Based on the Generative Agents paper (Park et al., 2023):
Retrieval scores each memory node by a weighted combination of:
- recency: exponential decay based on time since creation
- relevance: cosine similarity of embeddings
- importance: the poignancy/importance score of the memory

score = w_recency * recency + w_relevance * relevance + w_importance * importance
"""

import math
import logging
from typing import Callable

import numpy as np

from .memory_stream import ConceptNode, MemoryStream

logger = logging.getLogger("valis.memory.retrieval")


class MemoryRetrieval:
    """
    Implements weighted memory retrieval from the associative memory stream.
    """

    def __init__(
        self,
        memory_stream: MemoryStream,
        recency_weight: float = 1.0,
        relevance_weight: float = 1.0,
        importance_weight: float = 1.0,
        recency_decay: float = 0.99,  # per-minute decay rate
    ):
        self.memory = memory_stream
        self.recency_w = recency_weight
        self.relevance_w = relevance_weight
        self.importance_w = importance_weight
        self.recency_decay = recency_decay

    async def retrieve(
        self,
        query: str,
        limit: int = 10,
        node_type: str | None = None,
        embedding_fn: Callable | None = None,
    ) -> list[ConceptNode]:
        """
        Retrieve the top-k relevant memories for a query.

        Args:
            query: The current context/goal to retrieve memories for.
            limit: Maximum number of memories to return.
            node_type: Optional filter by node type.
            embedding_fn: Async function to generate embedding for the query.

        Returns:
            List of ConceptNode sorted by retrieval score (descending).
        """
        # Get all candidate nodes
        candidates = self.memory.get_recent(n=200, node_type=node_type)
        if not candidates:
            return []

        now = self._now_timestamp()

        # Generate query embedding if we have an embedding function
        query_embedding = None
        if embedding_fn:
            try:
                query_embedding = await embedding_fn(query)
            except Exception as e:
                logger.warning(f"Failed to embed query: {e}")

        scored = []
        for node in candidates:
            score = self._score_node(node, query_embedding, now)
            scored.append((node, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        return [node for node, _ in scored[:limit]]

    def _score_node(
        self,
        node: ConceptNode,
        query_embedding: list[float] | None,
        now: float,
    ) -> float:
        """Compute the retrieval score for a single memory node."""
        recency = self._recency_score(node, now)
        relevance = self._relevance_score(node, query_embedding)
        importance = node.importance

        score = (
            self.recency_w * recency
            + self.relevance_w * relevance
            + self.importance_w * importance
        )
        return score

    def _recency_score(self, node: ConceptNode, now: float) -> float:
        """Exponential decay based on time since creation."""
        created_ts = node.created.timestamp()
        minutes_ago = (now - created_ts) / 60.0
        return self.recency_decay ** minutes_ago

    def _relevance_score(
        self,
        node: ConceptNode,
        query_embedding: list[float] | None,
    ) -> float:
        """Cosine similarity between node embedding and query embedding."""
        if query_embedding is None or node.embedding is None:
            return 0.0
        try:
            a = np.array(query_embedding)
            b = np.array(node.embedding)
            dot = np.dot(a, b)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return float(dot / (norm_a * norm_b))
        except Exception:
            return 0.0

    def _now_timestamp(self) -> float:
        """Get current time as timestamp."""
        import datetime
        return datetime.datetime.now().timestamp()
