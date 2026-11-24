import json
from types import SimpleNamespace as NS

import httpx

from gabe.providers.anthropic_provider import AnthropicProvider
from gabe.providers.base import ToolResult
from gabe.providers.openai_compatible import OpenAICompatibleProvider
from gabe.tools.registry import Tool, schema

TOOL = Tool("echo", "Echo text", schema({"text": {"type": "string"}}), lambda text: text)


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def test_anthropic_request_shape_and_parsing():
    sent = {}
    msg = NS(
        content=[NS(type="thinking", thinking=""), NS(type="tool_use", id="t1", name="echo", input={"text": "hi"})],
        stop_reason="tool_use",
        usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0),
    )

    def stream(**kw):
        sent.update(kw)
        return FakeStream(msg)

    client = NS(beta=NS(messages=NS(stream=stream)))
    conv = AnthropicProvider(client=client).start("sys", [TOOL])
    conv.add_user("hello")
    r = conv.step()
    assert r.stop == "tool_use" and r.tool_calls[0].input == {"text": "hi"}
    assert sent["model"] == "claude-opus-5-5"
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["tools"][0]["eager_input_streaming"] is True
    assert "thinking" not in sent and "temperature" not in sent
    # assistant content (incl. thinking block) is stored unchanged
    assert conv.messages[-1]["content"] is msg.content
    conv.add_tool_results([ToolResult("t1", "hi"), ToolResult("t2", "bad", True)])
    blocks = conv.messages[-1]["content"]
    assert len(blocks) == 2 and blocks[1]["is_error"] is True


def test_openai_compatible_tool_call_roundtrip():
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{\"text\": \"yo\"}"}}],
            }}]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "yo"}}]})

    p = OpenAICompatibleProvider(base_url="http://local/v1", model="glm-5.3", transport=httpx.MockTransport(handler))
    conv = p.start("sys", [TOOL])
    conv.add_user("say yo")
    r = conv.step()
    assert r.stop == "tool_use" and r.tool_calls[0].input == {"text": "yo"}
    conv.add_tool_results([ToolResult("c1", "yo")])
    r2 = conv.step()
    assert r2.stop == "done" and r2.text == "yo"
    assert seen[0]["tools"][0]["function"]["name"] == "echo"
    assert seen[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "yo"}
