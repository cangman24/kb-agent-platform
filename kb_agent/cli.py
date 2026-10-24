# -*- coding: utf-8 -*-
"""kb_agent/cli.py —— 命令行入口（不需要 Web 框架）。

    python -m kb_agent.cli "查一下工单复核流程"         # 真机（需 DEEPSEEK_API_KEY）
    python -m kb_agent.cli --offline "查一下复核流程"    # 离线演示（无需 Key）
    python -m kb_agent.cli --stream "查一下复核流程"     # 看每一步工具调用
    python -m kb_agent.cli --trace <thread_id>          # 回放某次执行的轨迹
    python -m kb_agent.cli --tools                      # 打印工具清单与权限白名单

★ 为什么要有 --offline：Agent 的**编排逻辑**（白名单拦截、熔断、Checkpoint
  续跑、上下文压缩）是纯工程问题，不该被"有没有 API Key"卡住。离线模式下
  决策器是一段脚本化队列，引擎与工具仍是真的 —— 所以你能零成本验证
  "越权动作根本发不出去"这类安全性质。
"""
from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):  # 允许 `python kb_agent/cli.py` 直接跑
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mcp import ToolRegistry  # noqa: E402
from core.service import AgentService  # noqa: E402
from core.sse import parse_sse  # noqa: E402


# --------------------------------------------------------------------- 离线引擎
def build_offline_service() -> AgentService:
    """脚本化决策的演示引擎：工具是真的，模型是假的。"""
    from kb_agent.app import (build_default_checkpointer, build_engine,
                              rule_based_plan)

    index = _demo_index()

    reg = ToolRegistry()

    @reg.tool(description="检索知识库")
    def kb_search(query: str):
        """检索

        query: 检索词
        """
        hits = index.bm25.search(query, top_k=3)
        return {"hits": [{"id": cid, "text": index.doc_text(cid)[:200],
                          "score": round(score, 4)} for cid, score in hits]}

    @reg.tool(description="发邮件", sensitive=True)
    def send_mail(to: str):
        """发邮件

        to: 收件人
        """
        return {"sent": True, "to": to}

    # 决策队列：先检索，再回答。想演示"越权被拦"就把第一个决策换成 send_mail。
    decisions = [
        {"tool": "kb_search", "args": {"query": "__QUERY__"}},
        {"final": "（离线演示）已依据知识库检索结果汇总回答。", "citations": []},
    ]
    cursor = {"i": 0}

    def act(state, allowed):
        d = decisions[min(cursor["i"], len(decisions) - 1)]
        cursor["i"] += 1
        if "args" in d and d["args"].get("query") == "__QUERY__":
            d = dict(d, args={"query": state.query})
        return d

    engine = build_engine(
        registry=reg,
        decider_factory=lambda r, m: act,
        # ★ 离线模式也落盘：这样 --trace 能在**另一个进程**里回放刚才的执行，
        #   内存态 checkpointer 做不到（进程一退就没了）。
        checkpointer=build_default_checkpointer(),
        route_table={"kb": ["kb_search"], "general": []},
        intent_fn=lambda q: "kb",
        plan_fn=rule_based_plan,
        max_rounds=6,
        max_tool_calls=12,
    )
    return AgentService(engine)


def _demo_index():
    """优先用内置语料库；语料缺失时退化成两条内置文档，保证 CLI 总能跑起来。"""
    from kb_agent.app import build_kb_index

    try:
        return build_kb_index()
    except (FileNotFoundError, ValueError):
        from kb_agent.app import InMemoryKBIndex, KBChunk
        chunks = [KBChunk("demo#c0", "工单复核由复核岗执行，需在 24 小时内完成。"),
                  KBChunk("demo#c1", "敏感操作需二次审批，并留痕到审计日志。")]
        return InMemoryKBIndex(chunks)


# --------------------------------------------------------------------- 渲染
def render_trace(trace: dict) -> None:
    print(f"\n=== 轨迹 {trace.get('thread_id')} ===")
    print(f"意图: {trace.get('intent')}    计划: {trace.get('plan')}")
    for ev in trace.get("events", []):
        etype = ev.get("type", "")
        data = ev.get("data", {})
        if etype == "tool_call":
            print(f"  → 调用 {data.get('tool')}({data.get('args')})")
        elif etype == "tool_result":
            print(f"  ← 结果 {str(data.get('result'))[:120]}")
        elif etype == "answer":
            print(f"  ■ 回答 {str(data.get('text'))[:200]}")
        else:
            print(f"  · {etype}")
    if trace.get("stop_reason"):
        print(f"终止原因: {trace['stop_reason']}")


def render_stream(frames: str) -> None:
    for ev in parse_sse(frames):
        if "comment" in ev:
            continue
        etype = ev.get("event", "")
        try:
            import json
            data = json.loads(ev.get("data", "{}"))
        except (ValueError, TypeError):
            data = ev.get("data")
        if etype == "tool_call":
            print(f"  → 调用 {data.get('tool')}({data.get('args')})")
        elif etype == "tool_result":
            print(f"  ← 结果 {str(data.get('result'))[:120]}")
        elif etype == "answer":
            print(f"\n{data.get('text')}")
        elif etype == "done":
            print(f"\n[完成] 工具调用 {data.get('tool_calls')} 次")
        elif etype in ("start", "intent", "plan"):
            print(f"  · {etype}: {data}")


# --------------------------------------------------------------------- 入口
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="kb_agent.cli",
        description="KB Agent 命令行（离线可跑，无需 API Key）")
    p.add_argument("query", nargs="?", help="要执行的指令")
    p.add_argument("--offline", action="store_true",
                   help="离线模式：脚本化决策，不需要 DEEPSEEK_API_KEY")
    p.add_argument("--stream", action="store_true", help="流式输出每一步")
    p.add_argument("--thread-id", help="指定会话 id（用于续跑/轨迹回放）")
    p.add_argument("--resume", metavar="THREAD_ID", help="从 Checkpoint 续跑")
    p.add_argument("--trace", metavar="THREAD_ID", help="回放某次执行的轨迹")
    p.add_argument("--tools", action="store_true", help="打印工具清单与权限白名单")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.offline:
        service = build_offline_service()
    else:
        from kb_agent.app import build_production_service
        try:
            service = build_production_service()
        except RuntimeError as exc:
            print(f"{exc}\n\n提示：加 --offline 可以先看编排行为（无需 API Key）。",
                  file=sys.stderr)
            return 2

    if args.tools:
        info = service.tools()
        # ★ sensitive 是内部权限标记，不属于 MCP 协议字段，所以单独从
        #   ToolSpec 上取 —— 运维一眼能看出哪些工具是"审批后才能调"的。
        registry = getattr(service.engine, "registry", None)
        known = set(registry.names()) if registry is not None else set()
        print("可用工具：")
        for t in info["tools"]:
            flag = ""
            if t["name"] in known:
                flag = " [敏感·需审批]" if registry.get(t["name"]).sensitive else ""
            print(f"  - {t['name']}{flag}: {t.get('description', '')}")
        print("\n路由白名单（不在名单里的工具不会被调用）：")
        for route, tools in info["routes"].items():
            print(f"  {route}: {tools}")
        return 0

    if args.trace:
        trace = service.trace(args.trace)
        if not trace.get("found"):
            print(f"未找到会话 {args.trace} 的轨迹", file=sys.stderr)
            return 1
        render_trace(trace)
        return 0

    if args.resume:
        out = service.resume(args.resume)
        print(f"\n{out.get('answer')}")
        print(f"[thread_id={out.get('thread_id')} 完成={out.get('finished')}]")
        return 0

    if not args.query:
        print("请提供要执行的指令，或用 --tools / --trace 查看信息。", file=sys.stderr)
        return 2

    if args.stream:
        render_stream("".join(service.run_stream(args.query, args.thread_id)))
        return 0

    out = service.run(args.query, args.thread_id)
    print(f"\n{out.get('answer')}")
    detail = (f"[thread_id={out.get('thread_id')} 工具调用={out.get('tool_calls')} "
              f"完成={out.get('finished')}")
    if out.get("stop_reason"):
        detail += f" 终止原因={out['stop_reason']}"
    if out.get("error"):
        detail += f" 错误={out['error']}"
    print(detail + "]")
    return 0 if not out.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
