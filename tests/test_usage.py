import json
from types import SimpleNamespace as NS

import httpx

from gabe.app import build_gabe
from gabe.cli import main
from gabe.config import load_config
from gabe.providers.anthropic_provider import AnthropicProvider
from gabe.providers.base import StepResult, ToolCall
from gabe.providers.openai_compatible import OpenAICompatibleProvider
from gabe.usage import cost, read_events, summarize
from tests.conftest import ScriptedProvider

PRICING = {"claude-opus-5-5": {"input": 4.0, "cache_read": 0.2, "cache_write": 5.0, "output": 20.0}}


def step(usage, *calls, text=""):
    tool_calls = [ToolCall(f"id{i}", n, a) for i, (n, a) in enumerate(calls)]
    return StepResult(text=text, tool_calls=tool_calls, stop="tool_use" if calls else "done", usage=usage)


def u(model, inp, out, read=0, write=0):
    return {"model": model, "input_tokens": inp, "output_tokens": out,
            "cache_read_tokens": read, "cache_write_tokens": write}


def test_every_step_is_logged_with_user_task_and_delegate(config_file):
    cfg = load_config(config_file)
    p = ScriptedProvider([
        step(u("claude-opus-5-5", 100, 10), ("delegate", {"specialist": "scheduler", "task": "list tasks"})),
        step(u("claude-opus-5-5", 50, 5, read=40), text="none"),   # the specialist
        step(u("claude-opus-5-5", 120, 8, write=30), text="All clear."),
    ])
    assert build_gabe(cfg, provider=p, user="sam", task="chat").send("status?") == "All clear."

    events = read_events(cfg.data_dir / "usage.jsonl")
    assert [(e["user"], e["task"], e["agent"]) for e in events] == [
        ("sam", "chat", "Gabe"), ("sam", "chat", "scheduler"), ("sam", "chat", "Gabe")]
    assert events[1]["cache_read_tokens"] == 40 and events[2]["cache_write_tokens"] == 30


def test_steps_without_usage_are_not_logged(config_file):
    cfg = load_config(config_file)
    build_gabe(cfg, provider=ScriptedProvider([step({}, text="hi")]), user="sam").send("hi")
    assert read_events(cfg.data_dir / "usage.jsonl") == []


def test_cost_and_summary():
    e = {"model": "claude-opus-5-5", **{k: v for k, v in u("x", 1_000_000, 100_000, 2_000_000, 0).items() if k != "model"}}
    assert cost(e, PRICING) == 4.0 + 0.4 + 2.0
    assert cost({"model": "glm-5.3", "input_tokens": 5}, PRICING) is None
    # cache prices fall back to the input price when not configured
    assert cost({"model": "m", "cache_read_tokens": 1_000_000}, {"m": {"input": 1.5, "output": 2}}) == 1.5

    rows = summarize([
        {"user": "sam", "model": "claude-opus-5-5", "input_tokens": 1_000_000},
        {"user": "sam", "model": "glm-5.3", "input_tokens": 10},
        {"user": "alex", "model": "claude-opus-5-5", "output_tokens": 1_000_000},
    ], ["user"], PRICING)
    assert [(r.key, r.calls, r.cost, r.unpriced) for r in rows] == [
        (("alex",), 1, 20.0, False), (("sam",), 2, 4.0, True)]


def test_anthropic_usage_is_normalised_and_reports_serving_model():
    msg = NS(content=[NS(type="text", text="hi")], stop_reason="end_turn", model="claude-opus-5",
             usage=NS(input_tokens=7, output_tokens=3, cache_read_input_tokens=90, cache_creation_input_tokens=20))

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return msg

    client = NS(beta=NS(messages=NS(stream=lambda **kw: Stream())))
    conv = AnthropicProvider(client=client).start("sys", [])
    conv.add_user("hi")
    assert conv.step().usage == u("claude-opus-5", 7, 3, read=90, write=20)


def test_openai_usage_splits_cached_prompt_tokens():
    def handler(request):
        return httpx.Response(200, json={
            "model": "glm-5.3",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": 800}},
        })

    p = OpenAICompatibleProvider(base_url="http://local/v1", model="glm", transport=httpx.MockTransport(handler))
    conv = p.start("sys", [])
    conv.add_user("hi")
    assert conv.step().usage == u("glm-5.3", 200, 40, read=800)


def test_usage_command_prints_table(config_file, capsys):
    cfg = load_config(config_file)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "usage.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"ts": "2026-11-20T09:00:00+00:00", "user": "sam", "task": "chat", "agent": "Gabe", "model": "glm-5.3",
         "input_tokens": 1000, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 100},
        {"ts": "2026-11-24T09:00:00+00:00", "user": "alex", "task": "brief", "agent": "Gabe", "model": "glm-5.3",
         "input_tokens": 2000, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 50},
    ]) + "\n")
    assert main(["-c", str(config_file), "usage", "--since", "2026-11-21", "--by", "user"]) == 0
    out = capsys.readouterr().out
    assert "alex" in out and "sam" not in out and "1 model calls" in out
    assert "no entry under `pricing:`" in out  # the test config has no prices
