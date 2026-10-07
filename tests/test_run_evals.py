"""The eval harness itself, with a scripted judge: no Ollama, no database."""

from ai import llm
from ai.evals.run_evals import CALIBRATION, calibrate_judge, judge_faithful, summarize, verdict


class ScriptedJudge:
    def __init__(self, *replies):
        self.replies = list(replies)

    def start(self, system, user):
        return []

    def step(self, conversation, tools):
        return llm.Turn(self.replies.pop(0))


def test_verdict_reads_the_last_verdict_line():
    assert verdict("CLAIM: Kc is 1.05\nEVIDENCE: Sweet Peppers | 1.05\nVERDICT: YES")
    assert verdict("VERDICT: **yes**")
    assert not verdict("CLAIM: Kc is 2.40\nEVIDENCE: NONE\nVERDICT: NO")
    assert not verdict("Yes, looks fine to me")  # no VERDICT line: not trusted


def test_a_refusal_is_faithful_without_asking_the_judge():
    judge = ScriptedJudge()  # no replies: the judge must not be called
    assert judge_faithful("I don't know based on the available sources.", "sources", judge)


def test_calibration_needs_both_answers_right():
    assert calibrate_judge(ScriptedJudge("VERDICT: YES", "VERDICT: NO"), sources="s")
    assert not calibrate_judge(ScriptedJudge("VERDICT: NO", "VERDICT: NO"), sources="s")
    assert not calibrate_judge(ScriptedJudge("VERDICT: YES", "VERDICT: YES"), sources="s")
    assert [ok for _, ok in CALIBRATION] == [True, False]


def test_summary_counts_only_rows_that_have_the_metric():
    rows = [{"hit_at_5": True, "faithful": True}, {"hit_at_5": False}, {"refused": True}]
    summary = summarize(rows)
    assert summary["hit@5"] == "1/2"
    assert summary["faithfulness"] == "1/1"
    assert summary["refusals"] == "1/1"
    assert summary["tool use"] == "n/a"
    assert summary["tool answers supported"] == "n/a"
