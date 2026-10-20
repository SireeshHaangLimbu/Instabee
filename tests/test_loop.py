import json
from types import SimpleNamespace as NS

from agent.loop import LockerAgent


def _usage(i=100, o=20):
    return NS(input_tokens=i, output_tokens=o, cache_read_input_tokens=0, cache_creation_input_tokens=0)


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # the SDK serialises at call time
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.beta = NS(messages=FakeMessages(responses))


def test_tool_call_then_answer(mini_db, tmp_path):
    responses = [
        NS(stop_reason="tool_use", usage=_usage(), content=[
            NS(type="thinking", thinking="Check coverage first."),
            NS(type="tool_use", id="tu_1", name="coverage", input={"area": "Testby", "network": "Instabee", "radius_m": 500}),
        ]),
        NS(stop_reason="end_turn", usage=_usage(200, 40), content=[NS(type="text", text="14.3% of Testby is covered.")]),
    ]
    client = FakeClient(responses)
    log = tmp_path / "runs.jsonl"
    agent = LockerAgent(client=client, con=mini_db, log_path=log)
    result = agent.ask("How covered is Testby?")

    assert result.answer == "14.3% of Testby is covered."
    assert [c["name"] for c in result.tool_calls] == ["coverage"]
    assert "pct_covered: 14.3" in result.tool_calls[0]["output"]
    assert result.usage["input_tokens"] == 300 and result.usage["output_tokens"] == 60

    first, second = client.beta.messages.calls
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in first["betas"]
    assert first["thinking"]["type"] == "adaptive"
    assert first["output_config"]["effort"] == "medium"
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_1"
    assert second["messages"][1]["role"] == "assistant"  # assistant content appended unchanged

    logged = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert logged["question"] == "How covered is Testby?" and logged["tool_calls"][0]["name"] == "coverage"


def test_refusal_is_reported_not_read_as_content(mini_db, tmp_path):
    responses = [NS(stop_reason="refusal", usage=_usage(), content=[],
                    stop_details=NS(category="cyber", explanation="declined"))]
    agent = LockerAgent(client=FakeClient(responses), con=mini_db, log_path=tmp_path / "r.jsonl")
    result = agent.ask("anything")
    assert result.stop_reason == "refusal"
    assert "declined" in result.answer


def test_tool_errors_are_flagged(mini_db, tmp_path):
    responses = [
        NS(stop_reason="tool_use", usage=_usage(), content=[
            NS(type="tool_use", id="tu_1", name="coverage", input={"area": "Atlantis"})]),
        NS(stop_reason="end_turn", usage=_usage(), content=[NS(type="text", text="Unknown area.")]),
    ]
    client = FakeClient(responses)
    result = LockerAgent(client=client, con=mini_db, log_path=tmp_path / "r.jsonl").ask("Atlantis?")
    sent = client.beta.messages.calls[1]["messages"][-1]["content"][0]
    assert sent["is_error"] is True
    assert result.tool_calls[0]["is_error"] is True


def test_stops_after_max_turns(mini_db, tmp_path):
    looping = NS(stop_reason="tool_use", usage=_usage(), content=[
        NS(type="tool_use", id="tu_x", name="list_tables", input={})])
    agent = LockerAgent(client=FakeClient([looping] * 3), con=mini_db, log_path=tmp_path / "r.jsonl", max_turns=3)
    result = agent.ask("loop forever")
    assert result.stop_reason == "max_turns"
