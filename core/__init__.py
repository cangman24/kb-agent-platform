# -*- coding: utf-8 -*-
"""core —— 受控 Agent 平台的共享基建。

本包 12 个模块中 **11 个只用标准库**，唯一的第三方依赖（openai SDK）
在 core.llm 里延迟导入——因此整个包**不依赖 torch / transformers /
chromadb / langgraph**，全部单测在裸 Python 上全绿（约 1 秒）。

    import core.agent     # 受控状态机：Checkpoint 续跑 / 三道熔断 / 事件流
    import core.mcp       # 工具注册中心 + Schema 自动生成 + 白名单过滤
    import core.memory    # 三层记忆 + 三级上下文压缩
    import core.service   # 框架无关服务层（AgentService：执行 / 轨迹 / 工具清单）
    import core.sse       # SSE 协议
    import core.bm25      # BM25 自实现（内置知识库检索用）
    import core.llm       # LLM 客户端（重试 / 限流 / 磁盘缓存）
"""

__all__ = [
    "agent",
    "agent_eval",
    "agent_langgraph",
    "bm25",
    "llm",
    "mcp",
    "memory",
    "metrics",
    "retrieval",
    "service",
    "sse",
    "text",
]
