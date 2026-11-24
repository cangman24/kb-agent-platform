"""HTTP 汇总帧与轨迹事件的计数保持一致。"""
import json
import time
from types import SimpleNamespace
import unittest

try:
    from kb_agent.agent_api import _summary_frame
except ImportError as exc:
    raise unittest.SkipTest("HTTP 依赖可选") from exc

from core.sse import parse_sse


class TestStreamSummary(unittest.TestCase):
    def test_stream_summary_counts_events_and_limits(self):
        events = [
            {"type": "start", "ts": 10.0},
            {"type": "tool_call", "ts": 10.1},
            {"type": "tool_error", "ts": 10.2},
            {"type": "limit", "ts": 10.2},
            {"type": "done", "ts": 10.3},
        ]
        state = SimpleNamespace(thread_id="T", rounds=1, tool_calls=1,
                                answer="", stop_reason="tool_timeout")
        frame = _summary_frame(state, time.perf_counter(), events)
        payload = json.loads(next(e["data"] for e in parse_sse(frame) if e.get("event") == "summary"))
        self.assertEqual(payload["event_count"], 5)
        self.assertEqual(payload["error_count"], 2)
        self.assertEqual(payload["error_types"], ["limit", "tool_error"])
        self.assertEqual(payload["stop_reason"], "tool_timeout")
