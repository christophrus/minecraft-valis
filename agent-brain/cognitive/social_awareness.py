"""
Social Awareness — tracks relationships and sentiments between agents.

Based on the PIANO architecture (Project Sid, Altera.AL 2024):
Maintains a directed sentiment graph between agents. When agents interact,
sentiment is extracted via LLM and stored. This influences future social
behavior, role specialization, and cultural transmission.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import ValisAgent

logger = logging.getLogger("valis.cognitive.social_awareness")


@dataclass
class Relationship:
    """A directed relationship from one agent to another."""
    target_name: str
    sentiment: float = 5.0  # 0=hate, 5=neutral, 10=love
    trust: float = 5.0  # 0=no trust, 10=complete trust
    interaction_count: int = 0
    last_interaction: float = 0.0
    notes: str = ""


class SocialAwareness:
    """
    Maintains the agent's social awareness: relationships with other agents,
    sentiment tracking, and social context for decision-making.

    Key features from Project Sid:
    - Directed sentiment graph (A might like B but B doesn't like A)
    - Sentiment updates from conversational analysis
    - Social context influences action selection
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.relationships: dict[str, Relationship] = {}
        self.observed_agents: set[str] = set()

    def update_relationship(
        self,
        target: str,
        sentiment_delta: float = 0.0,
        trust_delta: float = 0.0,
        notes: str = "",
    ):
        """Update or create a relationship with another agent."""
        import time

        if target not in self.relationships:
            self.relationships[target] = Relationship(target_name=target)

        rel = self.relationships[target]
        rel.sentiment = max(0.0, min(10.0, rel.sentiment + sentiment_delta))
        rel.trust = max(0.0, min(10.0, rel.trust + trust_delta))
        rel.interaction_count += 1
        rel.last_interaction = time.time()
        if notes:
            rel.notes = notes

    async def analyze_interaction(
        self,
        agent: "ValisAgent",
        target_name: str,
        conversation: str,
    ):
        """Analyze a conversation to extract sentiment changes."""
        prompt = f"""Analyze this conversation from the perspective of {agent.name}.

{agent.name} is talking to {target_name}:

{conversation}

How does {agent.name} feel about {target_name} after this conversation?
Output a JSON:
- "sentiment_change": a number from -3 to +3 (-3 = much worse, 0 = unchanged, +3 = much better)
- "trust_change": a number from -3 to +3
- "notes": one sentence about the relationship

Output ONLY the JSON."""

        try:
            response = await agent.llm.chat([
                {"role": "system", "content": "You analyze social interactions. Output only JSON."},
                {"role": "user", "content": prompt},
            ], max_tokens=200)

            import json
            json_str = response.strip()
            if json_str.startswith("```"):
                json_str = json_str.split("\n", 1)[1]
                if json_str.endswith("```"):
                    json_str = json_str[:-3]
            data = json.loads(json_str)

            self.update_relationship(
                target=target_name,
                sentiment_delta=float(data.get("sentiment_change", 0)),
                trust_delta=float(data.get("trust_change", 0)),
                notes=data.get("notes", ""),
            )
        except Exception as e:
            logger.warning(f"Failed to analyze interaction: {e}")
            # Default: neutral update
            self.update_relationship(target=target_name)

    def get_social_context(self) -> str:
        """Build a social context summary for the cognitive controller."""
        if not self.relationships:
            return "No social relationships yet."

        lines = ["Relationships with other agents:"]
        for name, rel in self.relationships.items():
            sentiment_label = self._sentiment_label(rel.sentiment)
            trust_label = self._trust_label(rel.trust)
            lines.append(
                f"  - {name}: sentiment={sentiment_label} ({rel.sentiment:.1f}/10), "
                f"trust={trust_label} ({rel.trust:.1f}/10), "
                f"interactions={rel.interaction_count}"
            )
        return "\n".join(lines)

    def get_allies(self, threshold: float = 6.0) -> list[str]:
        """Get agents with positive sentiment above threshold."""
        return [
            name for name, rel in self.relationships.items()
            if rel.sentiment >= threshold
        ]

    def get_enemies(self, threshold: float = 4.0) -> list[str]:
        """Get agents with negative sentiment below threshold."""
        return [
            name for name, rel in self.relationships.items()
            if rel.sentiment <= threshold
        ]

    def _sentiment_label(self, score: float) -> str:
        if score >= 8: return "friendly"
        if score >= 6: return "positive"
        if score >= 4: return "neutral"
        if score >= 2: return "negative"
        return "hostile"

    def _trust_label(self, score: float) -> str:
        if score >= 8: return "high"
        if score >= 5: return "moderate"
        return "low"

    def get_relationship_graph_data(self) -> dict:
        """Export relationship data for visualization."""
        return {
            "agent": self.agent_name,
            "relationships": {
                name: {
                    "sentiment": rel.sentiment,
                    "trust": rel.trust,
                    "interactions": rel.interaction_count,
                    "notes": rel.notes,
                }
                for name, rel in self.relationships.items()
            },
        }
