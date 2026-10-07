"""Day 6: retrieval, the R in RAG.

The question is embedded with the same model as the chunks (otherwise the vectors
are not comparable), then pgvector returns the 5 chunks closest to it by cosine
distance. Each chunk keeps its source and page, which become numbered citations.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai import llm
from ai.ingest_docs import to_vector
from ingest.db import connect


@dataclass
class Passage:
    source: str
    page: int
    chunk_index: int
    text: str
    distance: float  # cosine distance: 0 means same direction, smaller is more similar

    @property
    def label(self) -> str:
        return f"{self.source}, p.{self.page}"


def retrieve(question: str, k: int = 5, dsn: str | None = None) -> list[Passage]:
    vector = to_vector(llm.embed([llm.QUERY_PREFIX + question])[0])
    with connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT source, page, chunk_index, chunk, embedding <=> %s::vector AS distance
            FROM public.doc_chunks
            ORDER BY embedding <=> %s::vector  -- the HNSW index serves this ORDER BY ... LIMIT
            LIMIT %s
            """,
            (vector, vector, k),
        ).fetchall()
    return [Passage(*row) for row in rows]


def format_sources(passages: list[Passage]) -> str:
    return "\n\n".join(f"[{i}] ({p.label})\n{p.text}" for i, p in enumerate(passages, start=1))
