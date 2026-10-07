"""The agent loop, with a scripted fake model: no Ollama, no API key, no database."""

from types import SimpleNamespace

from ai import llm
from ai.ask import REFUSAL, answer, build_user_message, tools_for
from ai.rag import Passage
from tests.test_tools import DAY, FIELD, FakeQuery

PASSAGES = [
    Passage("fao56_ch6_crop_coefficient.html", 7, 0, "Sweet Peppers (bell) | - | 1.05 | 0.90", 0.2),
    Passage("usda_nrcs_soil_moisture_feel.pdf", 2, 1, "Squeeze a handful of soil...", 0.3),
]


class ScriptedChat:
    """Plays back a list of Turns and records what the loop sent it."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.tools_offered = []
        self.tool_results = []

    def start(self, system, user):
        self.system, self.user = system, user
        return []

    def step(self, conversation, tools):
        self.tools_offered.append(tools)
        return self.turns.pop(0)

    def add_tool_results(self, conversation, results):
        self.tool_results += results


def test_sources_are_numbered_for_citation():
    message = build_user_message("Kc for peppers?", PASSAGES)
    assert "[1] (fao56_ch6_crop_coefficient.html, p.7)" in message
    assert "[2] (usda_nrcs_soil_moisture_feel.pdf, p.2)" in message
    assert message.endswith("Question: Kc for peppers?")


def test_tool_is_offered_only_for_field_questions():
    assert tools_for("Should field F-03 be irrigated?")
    assert tools_for("Which fields need water?")
    assert tools_for("What is the Kc of sweet peppers?") == []


def test_tool_call_then_answer():
    chat = ScriptedChat(
        llm.Turn("", [llm.ToolCall("call_0", "get_field_conditions", {"field_id": "F-03"})]),
        llm.Turn("Yes, irrigate F-03: 23.2% is below 25% (field data)."),
    )
    result = answer(
        "Should field F-03 be irrigated?",
        chat=chat,
        passages=PASSAGES,
        query=FakeQuery([FIELD], [DAY]),
    )
    assert result.text.startswith("Yes, irrigate F-03")
    assert result.tool_calls == [
        {"name": "get_field_conditions", "arguments": {"field_id": "F-03"}, "error": False}
    ]
    assert '"moisture_3d_avg_pct":23.2' in chat.tool_results[0].content


def test_bad_tool_arguments_go_back_to_the_model_as_an_error():
    chat = ScriptedChat(
        llm.Turn("", [llm.ToolCall("call_0", "get_field_conditions", {"field_id": "3"})]),
        llm.Turn(REFUSAL),
    )
    result = answer("How is field 3?", chat=chat, passages=PASSAGES, query=FakeQuery())
    assert chat.tool_results[0].is_error
    assert result.tool_calls[0]["error"] is True


def test_the_loop_stops_after_max_rounds():
    looping = llm.Turn("", [llm.ToolCall("c", "get_field_conditions", {"field_id": "F-03"})])
    chat = ScriptedChat(*[looping] * 5)
    query = FakeQuery(*([[FIELD], [DAY]] * 5))
    result = answer("field F-03?", chat=chat, passages=PASSAGES, query=query, max_rounds=3)
    assert "could not finish" in result.text
    assert len(result.tool_calls) == 3


def test_claude_adapter_speaks_the_messages_api_format():
    """The optional Claude path, checked against a fake client (no key, no cost)."""
    tool_use = SimpleNamespace(
        type="tool_use",
        id="toolu_1",
        name="get_field_conditions",
        input={"field_id": "F-03", "days": 7},
    )
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=[tool_use], stop_reason="tool_use")

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    chat = llm.ClaudeChat(model="claude-haiku-4-5-20251001", client=client)
    conversation = chat.start("system text", "user text")
    turn = chat.step(
        conversation,
        [{"name": "get_field_conditions", "description": "d", "input_schema": {"type": "object"}}],
    )
    assert turn.tool_calls[0].id == "toolu_1"
    assert calls[0]["system"] == "system text" and calls[0]["temperature"] == 0
    chat.add_tool_results(conversation, [llm.ToolResult(turn.tool_calls[0], "{}", False)])
    result_block = conversation["messages"][-1]["content"][0]
    assert result_block == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "{}",
        "is_error": False,
    }


def test_ollama_tries_again_after_its_worker_dies(monkeypatch):
    # The first call gets the 500 Ollama returns when its model worker was killed
    # (out of memory on 8 GB); the second reaches the fresh worker and succeeds.
    replies = [
        SimpleNamespace(status_code=500, url="http://ollama/api/chat", text="EOF"),
        SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"message": {"role": "assistant", "content": "1.05"}},
        ),
    ]
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: replies.pop(0))
    monkeypatch.setattr("ingest.retry.time.sleep", lambda seconds: None)

    chat = llm.OllamaChat(model="llama3.2:3b")
    turn = chat.step(chat.start("system", "Kc mid for sweet peppers?"), [])
    assert turn.text == "1.05"
    assert replies == []  # both replies were used: one failure, one retry
