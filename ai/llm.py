"""Day 6: the two things the assistant needs from a model, behind one small interface.

    embed(texts)                    -> vectors, from Ollama's nomic-embed-text (local, free)
    chat_client().start/step/...    -> a tool-using chat, from Ollama (default) or Claude

Settings (environment variables):
    OLLAMA_URL         http://localhost:11434
    AGRI_EMBED_MODEL   nomic-embed-text      (768 dimensions, matches doc_chunks)
    AGRI_CHAT_MODEL    llama3.2:3b
    AGRI_LLM           ollama | claude       (claude needs ANTHROPIC_API_KEY, costs money)
    ANTHROPIC_MODEL    claude-haiku-4-5-20251001

Each provider formats tool calls differently, so each has an adapter with the same
three methods. ai/ask.py runs one agent loop on top and never sees the difference.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from ingest.retry import retry

# nomic-embed-text was trained with these task prefixes. Using them puts questions
# and passages in the right "regions" of the vector space, which improves retrieval.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


def ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://localhost:11434")


def embed_model() -> str:
    return os.environ.get("AGRI_EMBED_MODEL", "nomic-embed-text")


def chat_model() -> str:
    return os.environ.get("AGRI_CHAT_MODEL", "llama3.2:3b")


def embed(texts: list[str], *, batch_size: int = 32) -> list[list[float]]:
    """Embed texts with Ollama, in batches. Callers add DOC_PREFIX or QUERY_PREFIX."""
    vectors: list[list[float]] = []
    with httpx.Client(base_url=ollama_url(), timeout=300) as client:
        for start in range(0, len(texts), batch_size):
            resp = client.post(
                "/api/embed",
                json={"model": embed_model(), "input": texts[start : start + batch_size]},
            )
            resp.raise_for_status()
            vectors += resp.json()["embeddings"]
    return vectors


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call: ToolCall
    content: str  # JSON text
    is_error: bool = False


@dataclass
class Turn:
    """One model response: some text, and the tools it wants called (maybe none)."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelServerError(Exception):
    """The local model server answered with a 5xx status."""


@retry(times=3, base_delay=2.0, retry_on=(httpx.ConnectError, ModelServerError))
def _ollama_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """POST /api/chat and return the reply message.

    A 500 from Ollama usually means its model worker died. On an 8 GB machine that is
    most often memory: the worker keeps a cache of past prompts in RAM, and during a
    long eval run that cache can outgrow what is free. Ollama starts a fresh worker on
    the next request, so trying again normally works, and with temperature 0 the retry
    gives the answer the first try would have. If it keeps happening, restart Ollama
    with LLAMA_ARG_CACHE_RAM=1024 to cap that cache (see the README).
    """
    resp = httpx.post(f"{ollama_url()}/api/chat", json=payload, timeout=600)
    if resp.status_code >= 500:
        raise ModelServerError(f"{resp.status_code} from {resp.url}: {resp.text[:200]}")
    resp.raise_for_status()
    return resp.json()["message"]


class OllamaChat:
    """Local model through Ollama's /api/chat. Free, private, slower on a laptop CPU."""

    def __init__(self, model: str | None = None, num_ctx: int = 8192) -> None:
        self.model = model or chat_model()
        self.num_ctx = num_ctx  # room for 5 retrieved chunks, the tool schema and the answer

    def start(self, system: str, user: str) -> list[dict[str, Any]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def step(self, conversation: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Turn:
        message = _ollama_chat(
            {
                "model": self.model,
                "messages": conversation,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["input_schema"],
                        },
                    }
                    for t in tools
                ],
                "stream": False,
                # Temperature 0 and a fixed seed: the same question gives the same
                # answer, so an eval score changes only when the system changes.
                "options": {"temperature": 0, "seed": 7, "num_ctx": self.num_ctx},
            }
        )
        conversation.append(message)
        calls = [
            ToolCall(f"call_{i}", c["function"]["name"], _as_dict(c["function"].get("arguments")))
            for i, c in enumerate(message.get("tool_calls") or [])
        ]
        return Turn(message.get("content") or "", calls)

    def add_tool_results(
        self, conversation: list[dict[str, Any]], results: list[ToolResult]
    ) -> None:
        for r in results:
            conversation.append({"role": "tool", "tool_name": r.call.name, "content": r.content})


class ClaudeChat:
    """Claude through the Anthropic Messages API. Optional: needs ANTHROPIC_API_KEY."""

    def __init__(
        self, model: str | None = None, client: Any = None, max_tokens: int = 1024
    ) -> None:
        if client is None:
            import anthropic  # imported here so the default Ollama path does not need it

            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.client = client
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        self.max_tokens = max_tokens

    def start(self, system: str, user: str) -> dict[str, Any]:
        return {"system": system, "messages": [{"role": "user", "content": user}]}

    def step(self, conversation: dict[str, Any], tools: list[dict[str, Any]]) -> Turn:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=conversation["system"],
            messages=conversation["messages"],
            tools=tools,  # already in Anthropic's shape: name, description, input_schema
        )
        conversation["messages"].append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [
            ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"
        ]
        return Turn(text, calls)

    def add_tool_results(self, conversation: dict[str, Any], results: list[ToolResult]) -> None:
        conversation["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call.id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )


def chat_client() -> OllamaChat | ClaudeChat:
    return ClaudeChat() if os.environ.get("AGRI_LLM", "ollama") == "claude" else OllamaChat()


def _as_dict(arguments: Any) -> dict[str, Any]:
    """Ollama usually sends arguments as an object, some models send a JSON string."""
    if isinstance(arguments, dict):
        return arguments
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {"_unparsed": arguments}
