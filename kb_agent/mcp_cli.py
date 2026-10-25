# -*- coding: utf-8 -*-
"""kb_agent/mcp_cli.py —— 用**内置知识库**起标准 MCP server。

与 core/mcp_server.py 的分工（接缝设计）：
    core/mcp_server.py   只管协议（JSON-RPC 2.0），零依赖，可离线单测
    本文件               只管装配：内置语料 -> 索引 -> ToolRegistry -> McpServer

用法：
    python -m kb_agent.mcp_cli --transport stdio     # 本地子进程传输
    python -m kb_agent.mcp_cli --transport http --port 8765

★ 任何 MCP host（Claude Desktop / Cursor / 其他 Agent）都能连上这个
  server 拿到 kb_search / ticket_query / metric_query 三个工具——
  这就是"按 MCP 规范封装工具"与"进程内私有注册表"的差别。
  演示：python demos/demo_mcp.py（spawn 本模块走完整会话）。
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from core.mcp import ToolRegistry
from core.mcp_server import McpServer, serve_http, serve_stdio


def build_server(allowed: Optional[List[str]] = None) -> McpServer:
    """装配：内置知识库索引 -> 业务工具 registry -> MCP server。"""
    from kb_agent.app import build_kb_index, build_registry_from_index

    index = build_kb_index()          # data/kb/ 语料 + BM25，零 ML 依赖
    registry: ToolRegistry = build_registry_from_index(index)
    return McpServer(registry, allowed=allowed)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="内置知识库的 MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    server = build_server()
    if args.transport == "http":
        serve_http(server, args.host, args.port)
        return 0
    return serve_stdio(server)


if __name__ == "__main__":
    raise SystemExit(main())
