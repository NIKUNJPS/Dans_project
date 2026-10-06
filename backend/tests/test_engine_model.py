"""Which Claude model the engine calls, and with what request shape (no network)."""
import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import pytest  # noqa: E402

import claude_engine as ce  # noqa: E402

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("ANTHROPIC_MODEL") or os.environ.get("ANTHROPIC_FALLBACK_MODEL")),
    reason="model overridden by the environment",
)


class _FakeClient:
    """Records every stream request; can fail the first N calls."""

    def __init__(self, fail_first: int = 0):
        self.calls: list[dict] = []
        self.fail_first = fail_first
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        if len(self.calls) <= self.fail_first:
            raise RuntimeError("overloaded")
        msg = SimpleNamespace(model=kw["model"], stop_reason="end_turn",
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


def _run(client):
    return asyncio.run(ce.generate(system="s", content=[{"type": "text", "text": "hi"}], label="t", client=client))


def test_every_call_uses_sonnet_5_5_only():
    assert ce.MODEL_CHAIN == ["claude-sonnet-5-5"]
    client = _FakeClient()
    res = _run(client)
    assert res.model == "claude-sonnet-5-5" and ce.engine_label(res.model) == "STRUCTMIND CORE · PRO"
    kw = client.calls[0]
    assert kw["model"] == "claude-sonnet-5-5"
    assert kw["thinking"] == {"type": "adaptive"} and kw["output_config"] == {"effort": "high"}
    assert kw["max_tokens"] == 128_000 and kw["fallbacks"] == "default"


def test_a_failing_sonnet_call_never_switches_to_opus():
    client = _FakeClient(fail_first=1)
    with pytest.raises(ce.EngineError):
        _run(client)
    assert [c["model"] for c in client.calls] == ["claude-sonnet-5-5"]
