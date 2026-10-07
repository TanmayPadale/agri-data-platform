"""Slow checks against the real local stack (Ollama + ingested documents).

Marked slow, so `make test` and CI skip them. Run with: uv run pytest -m slow
"""

import json
from pathlib import Path

import httpx
import pytest

from ai.llm import ollama_url

pytestmark = pytest.mark.slow

GOLDEN = json.loads((Path(__file__).parent.parent / "ai/evals/golden.json").read_text())[
    "questions"
]


def _ollama_up() -> bool:
    try:
        return httpx.get(f"{ollama_url()}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


needs_ollama = pytest.mark.skipif(not _ollama_up(), reason="Ollama is not running")


@needs_ollama
def test_retrieval_finds_the_right_document_for_most_questions():
    from ai.rag import retrieve

    items = [q for q in GOLDEN if q["kind"] in ("docs", "injection")]
    hits = sum(
        bool({p.source for p in retrieve(q["question"])} & set(q["expected_sources"]))
        for q in items
    )
    assert hits >= 8, f"hit@5 {hits}/{len(items)}"


@needs_ollama
def test_unanswerable_questions_are_refused():
    from ai.evals.run_evals import evaluate
    from ai.llm import chat_client

    item = next(q for q in GOLDEN if q["id"] == "r1")
    assert evaluate(item, chat_client())["refused"]
