"""Which Claude model the engine calls, and with what request shape (no network)."""
import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import pytest  # noqa: E402

import claude_engine as ce  # noqa: E402

import anthropic  # noqa: E402
import httpx  # noqa: E402

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("ANTHROPIC_MODEL") or os.environ.get("ANTHROPIC_EFFORT")),
    reason="model overridden by the environment",
)


class _FakeClient:
    """Records every stream request; can fail the first N calls or stop at max_tokens."""

    def __init__(self, fail_first: int = 0, error=None, stop_reason: str = "end_turn"):
        self.calls: list[dict] = []
        self.fail_first = fail_first
        self.error = error or RuntimeError("boom")
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        if len(self.calls) <= self.fail_first:
            raise self.error
        msg = SimpleNamespace(model=kw["model"], stop_reason=self.stop_reason,
                              content=[SimpleNamespace(type="text", text="ok")],
                              usage=SimpleNamespace(input_tokens=10, output_tokens=5))

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return msg

        return _Stream()


def _run(client, **kw):
    return asyncio.run(ce.generate(system="s", content=[{"type": "text", "text": "hi"}], label="t", client=client, **kw))


def _connection_error():
    return anthropic.APIConnectionError(message="overloaded", request=httpx.Request("POST", "https://api.anthropic.com"))


def test_every_call_uses_opus_5_5_only():
    assert ce.MODEL == "claude-opus-5-5" and ce.MODEL_CHAIN == ["claude-opus-5-5"]
    client = _FakeClient()
    res = _run(client)
    assert res.model == "claude-opus-5-5" and ce.engine_label(res.model) == "STRUCTMIND CORE · PRO"
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"} and kw["output_config"] == {"effort": "high"}
    assert kw["max_tokens"] == 128_000
    # No model fallback of any kind — neither a second model nor the server-side fallback.
    assert "fallbacks" not in kw and "betas" not in kw


def test_takeoff_and_estimate_run_at_max_effort():
    assert ce.effort_for("takeoff") == "max" and ce.effort_for("estimate") == "max"
    assert ce.effort_for("narrative") == "high"
    client = _FakeClient()
    _run(client, effort=ce.effort_for("takeoff"))
    assert client.calls[0]["output_config"] == {"effort": "max"}


def test_a_non_capacity_failure_never_switches_model():
    client = _FakeClient(fail_first=1)
    with pytest.raises(ce.EngineError):
        _run(client)
    assert [c["model"] for c in client.calls] == ["claude-opus-5-5"]


def test_capacity_errors_wait_and_retry_on_the_same_model(monkeypatch):
    monkeypatch.setattr(ce, "CAPACITY_BACKOFF_START", 0.01)
    waits = []

    async def hook(label):
        waits.append(label)

    async def go():
        token = ce.set_wait_hook(hook)
        try:
            return await ce.generate(system="s", content=[{"type": "text", "text": "hi"}], label="t", client=client)
        finally:
            ce.reset_wait_hook(token)

    client = _FakeClient(fail_first=3, error=_connection_error())
    res = asyncio.run(go())
    assert res.text == "ok"
    assert [c["model"] for c in client.calls] == ["claude-opus-5-5"] * 4
    assert len(waits) == 3


def test_capacity_exhausted_raises_for_requeue(monkeypatch):
    monkeypatch.setattr(ce, "CAPACITY_BACKOFF_START", 0.01)
    monkeypatch.setattr(ce.settings, "anthropic_capacity_wait_minutes", 0)
    client = _FakeClient(fail_first=99, error=_connection_error())
    with pytest.raises(ce.CapacityUnavailable):
        _run(client)
    assert {c["model"] for c in client.calls} == {"claude-opus-5-5"}


def test_output_never_silently_truncated(monkeypatch):
    monkeypatch.setattr(ce, "MAX_CONTINUATIONS", 1)
    client = _FakeClient(stop_reason="max_tokens")
    with pytest.raises(ce.OutputTruncated):
        _run(client)
    assert len(client.calls) == 2
