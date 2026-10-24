# -*- coding: utf-8 -*-
"""tests/test_service.py —— Agent 服务层单测。全部离线，零第三方依赖。

★ 这一层是"框架无关"的：业务逻辑收 dict、回 dict，所以即使环境里没有
  fastapi，也能全量验证。HTTP 接线在 kb_agent/api_fastapi.py，只是薄薄一层。

  本仓库只覆盖 Agent 侧（run / resume / trace / run_stream / tools / health）
  与 dispatch 的 Agent 分支；RAG 问答侧的测试属于 domain-rag-qa 仓库。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import AgentEngine, InMemoryCheckpointer  # noqa: E402
from core.mcp import ToolRegistry  # noqa: E402
from core.service import AgentService, dispatch  # noqa: E402
from core.sse import parse_sse  # noqa: E402


def make_agent_service(**kw):
    """装配一个可控的 AgentService：工具固定、决策序列可编程。"""
    reg = ToolRegistry()

    @reg.tool(description="检索知识库")
    def kb_search(query: str):
        """检索

        query: 检索词
        """
        return {"hits": [{"id": "c1", "text": "父块一"}]}

    @reg.tool(description="发邮件", sensitive=True)
    def send_mail(to: str):
        """发邮件

        to: 收件人
        """
        return {"sent": True}

    decisions = kw.pop("decisions", [
        {"tool": "kb_search", "args": {"query": "x"}},
        {"final": "答案 [1]", "citations": ["c1"]},
    ])
    cursor = {"i": 0}

    def act(state, allowed):
        d = decisions[min(cursor["i"], len(decisions) - 1)]
        cursor["i"] += 1
        return d

    engine = AgentEngine(
        registry=reg, intent_fn=kw.pop("intent_fn", lambda q: "kb"),
        plan_fn=lambda q, t: ["检索", "汇总"], act_fn=act,
        route_table={"kb": ["kb_search"], "general": []},
        checkpointer=kw.pop("checkpointer", InMemoryCheckpointer()),
        max_rounds=kw.pop("max_rounds", 6),
    )
    return AgentService(engine, kw.pop("config", None)), engine, reg


class TestAgentService(unittest.TestCase):
    def test_run_shape(self):
        svc, _, _ = make_agent_service()
        out = svc.run("查制度", thread_id="T1")
        self.assertEqual(out["thread_id"], "T1")
        self.assertEqual(out["answer"], "答案 [1]")
        self.assertTrue(out["finished"])
        self.assertEqual(out["tool_calls"], 1)
        self.assertIsNone(out["error"])

    def test_run_generates_thread_id(self):
        svc, _, _ = make_agent_service()
        self.assertTrue(svc.run("查制度")["thread_id"])

    def test_whitelist_blocks_sensitive_tool(self):
        """★ 敏感工具不在白名单里就直接拦掉 —— 越权动作一次都发不出去。"""
        svc, _, _ = make_agent_service(decisions=[
            {"tool": "send_mail", "args": {"to": "a@b.com"}},
            {"final": "无权限"},
        ])
        out = svc.run("查制度")
        self.assertTrue(out["finished"])
        self.assertEqual(out["tool_calls"], 0)

    def test_trace_after_run(self):
        svc, _, _ = make_agent_service()
        svc.run("查制度", thread_id="T2")
        tr = svc.trace("T2")
        self.assertTrue(tr["found"])
        self.assertEqual(tr["plan"], ["检索", "汇总"])
        self.assertTrue(tr["events"])
        self.assertTrue(tr["scratchpad"])

    def test_trace_unknown_thread(self):
        svc, _, _ = make_agent_service()
        tr = svc.trace("nope")
        self.assertFalse(tr["found"])
        self.assertEqual(tr["events"], [])

    def test_trace_marks_memory_source(self):
        """同进程内查轨迹：来源必须是 memory，且带完整事件流。"""
        svc, _, _ = make_agent_service()
        svc.run("查制度", thread_id="T5")
        tr = svc.trace("T5")
        self.assertEqual(tr["source"], "memory")
        self.assertTrue(tr["events"])

    def test_trace_falls_back_to_checkpoint(self):
        """★ 换一个服务实例（模拟进程重启）仍要能查到这次执行的状态。"""
        cp = InMemoryCheckpointer()
        svc1, _, _ = make_agent_service(checkpointer=cp)
        svc1.run("查制度", thread_id="T6")

        svc2, _, _ = make_agent_service(checkpointer=cp)
        tr = svc2.trace("T6")
        self.assertTrue(tr["found"])
        self.assertEqual(tr["source"], "checkpoint")
        self.assertEqual(tr["events"], [], "事件明细不进 Checkpoint")

    def test_trace_absent_without_checkpointer(self):
        svc, _, _ = make_agent_service()
        self.assertFalse(svc.trace("ghost")["found"])

    def test_resume_continues(self):
        """★ Checkpoint 续跑：熔断中断后换一个引擎实例接着跑。"""
        cp = InMemoryCheckpointer()
        svc1, _, _ = make_agent_service(checkpointer=cp, max_rounds=1,
                                        decisions=[{"tool": "kb_search",
                                                    "args": {"query": "x"}}])
        out1 = svc1.run("查制度", thread_id="T3")
        self.assertFalse(out1["finished"])

        svc2, _, _ = make_agent_service(checkpointer=cp, max_rounds=5,
                                        decisions=[{"final": "续跑答案"}])
        out2 = svc2.resume("T3")
        self.assertTrue(out2["finished"])
        self.assertEqual(out2["answer"], "续跑答案")

    def test_run_stream_events(self):
        svc, _, _ = make_agent_service()
        frames = "".join(svc.run_stream("查制度", thread_id="T4"))
        events = [e for e in parse_sse(frames) if "event" in e]
        types = [e["event"] for e in events]
        for expected in ("start", "intent", "plan", "tool_call",
                         "tool_result", "answer", "done"):
            self.assertIn(expected, types)

    def test_tools_listing(self):
        svc, _, _ = make_agent_service()
        info = svc.tools()
        names = sorted(t["name"] for t in info["tools"])
        self.assertEqual(names, ["kb_search", "send_mail"])
        self.assertIn("kb", info["routes"])

    def test_health_shape(self):
        svc, _, _ = make_agent_service()
        h = svc.health()
        self.assertEqual(h["tool_count"], 2)
        self.assertEqual(h["checkpointer"], "InMemoryCheckpointer")
        self.assertEqual(h["max_rounds"], 6)

    def test_health_without_registry(self):
        class Bare:
            pass
        self.assertEqual(AgentService(Bare()).health()["tool_count"], 0)

    def test_tools_without_registry(self):
        class Bare:
            pass
        self.assertEqual(AgentService(Bare()).tools(), {"tools": [], "routes": {}})


class TestDispatch(unittest.TestCase):
    def test_agent_run_and_trace(self):
        svc, _, _ = make_agent_service()
        out = dispatch(svc, "run", {"query": "查制度", "thread_id": "D1"})
        self.assertTrue(out["finished"])
        self.assertTrue(dispatch(svc, "trace", {"thread_id": "D1"})["found"])

    def test_agent_tools_and_health(self):
        svc, _, _ = make_agent_service()
        self.assertTrue(dispatch(svc, "tools")["tools"])
        self.assertEqual(dispatch(svc, "health")["status"], "ok")

    def test_agent_resume(self):
        cp = InMemoryCheckpointer()
        svc1, _, _ = make_agent_service(checkpointer=cp, max_rounds=1,
                                        decisions=[{"tool": "kb_search",
                                                    "args": {"query": "x"}}])
        dispatch(svc1, "run", {"query": "查制度", "thread_id": "D2"})
        svc2, _, _ = make_agent_service(checkpointer=cp, max_rounds=5,
                                        decisions=[{"final": "续跑"}])
        self.assertTrue(dispatch(svc2, "resume", {"thread_id": "D2"})["finished"])

    def test_unknown_service(self):
        self.assertIn("unknown_service", dispatch(object(), "run")["error"])


if __name__ == "__main__":
    unittest.main()
