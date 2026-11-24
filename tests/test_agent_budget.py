import unittest
from threading import Event

from core.agent import AgentEngine, AgentState, EventType, InMemoryCheckpointer
from core.agent_langgraph import LangGraphAgentEngine, langgraph_available
from core.mcp import ToolRegistry
from test_agent_langgraph import fake_graph_factory


class TestExecutionLimits(unittest.TestCase):
    def engines(self):
        yield AgentEngine, {}
        yield LangGraphAgentEngine, {"graph_factory": fake_graph_factory}
        if langgraph_available():
            yield LangGraphAgentEngine, {}

    def make_engine(self, engine, options, registry, act, **kwargs):
        config = {
            "registry": registry,
            "act_fn": act,
            "intent_fn": lambda _: "kb",
            "plan_fn": lambda *_: [],
            "route_table": {"kb": ["search"]},
            "checkpointer": InMemoryCheckpointer(),
        }
        config.update(options, **kwargs)
        return engine(**config)

    def test_tool_wait_is_bounded_and_timeout_checkpoint_is_terminal(self):
        cases = (
            (3, 0.2, 3, "tool_timeout"),
            (3, 3, 0.2, "tool_timeout"),
            (0.3, 3, 3, "total_timeout"),
        )
        for engine, options in self.engines():
            for total_s, tool_s, spec_s, reason in cases:
                with self.subTest(engine=engine, options=options, reason=reason,
                                  tool_s=tool_s, spec_s=spec_s):
                    self.assert_tool_timeout(engine, options, total_s, tool_s, spec_s, reason)

    def assert_tool_timeout(self, engine, options, total_s, tool_s, spec_s, reason):
        started, release, ended = Event(), Event(), Event()
        registry = ToolRegistry()
        decisions = []

        @registry.tool()
        def search(query: str):
            started.set()
            release.wait(5)
            ended.set()
            return "late result"

        registry.get("search").timeout_s = spec_s

        def act(*_):
            decisions.append(len(decisions))
            return {"tool": "search", "args": {"query": str(len(decisions))}}

        eng = self.make_engine(engine, options, registry, act,
                               total_timeout_s=total_s, tool_timeout_s=tool_s)
        try:
            run = eng.run("query", thread_id="T-timeout")
            self.assertTrue(started.is_set())
            self.assertFalse(ended.is_set())
            self.assertEqual(run.state.stop_reason, reason)
            self.assertEqual(run.state.tool_calls, 1)
            self.assertIsNone(run.state.last_observation)
            self.assertTrue(run.state.finished)
            self.assertEqual([e["data"]["reason"] for e in run.events
                              if e["type"] == EventType.LIMIT], [reason])
            self.assertIn(reason, [s["error"] for s in eng.last_spans
                                   if s["name"] == "tool:search"])
            self.assertEqual(eng.resume("T-timeout").state.stop_reason, reason)
            self.assertEqual(decisions, [0])
        finally:
            release.set()
            if started.is_set():
                self.assertTrue(ended.wait(2))

    def test_total_timeout_interrupts_decision_and_discards_late_result(self):
        for engine, options in self.engines():
            with self.subTest(engine=engine, options=options):
                self.assert_decision_timeout(engine, options)

    def assert_decision_timeout(self, engine, options):
        release = Event()
        decision_started, decision_ended = Event(), Event()

        def slow_act(state, allowed):
            decision_started.set()
            release.wait(5)
            state.scratchpad.append({"type": "late"})
            decision_ended.set()
            return {"tool": "search", "args": {"query": "late"}}

        registry = ToolRegistry()
        eng = self.make_engine(engine, options, registry, slow_act,
                               total_timeout_s=0.3)
        try:
            events = list(eng.run_stream("query", thread_id="T-slow-act"))
            self.assertEqual(eng.last_state.stop_reason, "total_timeout")
            self.assertTrue(decision_started.is_set())
            self.assertFalse(decision_ended.is_set())
            self.assertEqual(events[-1]["type"], EventType.DONE)
            self.assertNotIn(EventType.TOOL_CALL, [e["type"] for e in events])
        finally:
            release.set()
            if decision_started.is_set():
                self.assertTrue(decision_ended.wait(2))
        self.assertEqual(eng.last_state.scratchpad, [])

    def test_graph_timeout_checkpoint_survives_stream_disconnect(self):
        for engine, options in self.engines():
            if engine is LangGraphAgentEngine:
                with self.subTest(options=options):
                    self.assert_disconnect_after_timeout(engine, options)

    def assert_disconnect_after_timeout(self, engine, options):
        release = Event()
        registry = ToolRegistry()

        @registry.tool()
        def search(query: str):
            release.wait(5)

        eng = self.make_engine(engine, options, registry, lambda *_: {
            "tool": "search", "args": {"query": "slow"},
        }, tool_timeout_s=0.2)
        stream = eng.run_stream("query", thread_id="T-disconnect")
        try:
            limit = next(event for event in stream if event["type"] == EventType.LIMIT)
            self.assertEqual(limit["data"]["reason"], "tool_timeout")
        finally:
            stream.close()
            release.set()

        saved = eng.checkpointer.load("T-disconnect")
        self.assertTrue(saved.finished)
        resumed = eng.resume("T-disconnect")
        self.assertEqual(resumed.state.stop_reason, "tool_timeout")
        self.assertNotIn(EventType.TOOL_CALL, [e["type"] for e in resumed.events])

    def test_loop_comparison_and_resume_with_changing_arguments(self):
        for engine, options in self.engines():
            with self.subTest(engine=engine, options=options):
                registry = ToolRegistry()

                @registry.tool()
                def search(query: str):
                    return query

                first = self.make_engine(engine, options, registry, lambda *_: {
                    "tool": "search", "args": {"query": "same"},
                }, max_rounds=5)
                run = first.run("query")
                self.assertEqual(run.state.stop_reason, "loop_detected")
                self.assertEqual(run.state.tool_calls, 3)

                next_steps = iter([
                    {"tool": "search", "args": {"query": "third"}},
                    {"tool": "search", "args": {"query": "fourth"}},
                    {"final": "done"},
                ])
                cp = InMemoryCheckpointer()
                original = AgentState(query="query", thread_id="T-resume", task_type="kb",
                                      allowed_tools=["search"], planned=True, rounds=2,
                                      tool_calls=2, scratchpad=[
                                          {"type": "tool_call", "tool": "search",
                                           "args": {"query": query}}
                                          for query in ("first", "second")
                                      ])
                cp.save(original.thread_id, original)
                resumed = self.make_engine(engine, options, registry,
                                           lambda *_, steps=next_steps: next(steps), checkpointer=cp)
                result = resumed.resume(original.thread_id)
                self.assertEqual(result.state.answer, "done")
                self.assertEqual(result.state.tool_calls, 4)
                self.assertNotIn(EventType.INTENT, [e["type"] for e in result.events])
