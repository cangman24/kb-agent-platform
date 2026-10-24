# -*- coding: utf-8 -*-
"""kb_agent/server.py —— HTTP 服务启动入口。

    python -m kb_agent.server [--host 0.0.0.0] [--port 8000] [--reload]

★ 与 api_fastapi.py 的分工：
      server.py      —— 进程启动、日志、端口（运维关注）
      api_fastapi.py —— 路由与传输（接口关注）
      core/service.py—— 业务逻辑（已零依赖单测覆盖）
  这样即使不装 uvicorn，core/service.py 依然可用，测试也不依赖 Web 框架。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="kb_agent.server",
        description="启动 KB Agent 的 HTTP 服务（FastAPI + uvicorn）")
    p.add_argument("--host", default=os.environ.get("KB_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("KB_PORT", "8000")))
    p.add_argument("--reload", action="store_true", help="开发模式：代码改动自动重启")
    p.add_argument("--workers", type=int, default=1)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        import uvicorn
    except ImportError:
        print("需要 uvicorn 才能启动 HTTP 服务：pip install fastapi uvicorn\n"
              "不装也能用：python -m kb_agent.cli（命令行交互，无需 Web 框架）",
              file=sys.stderr)
        return 2

    if args.reload and args.workers > 1:
        print("--reload 与多 worker 不能同时使用", file=sys.stderr)
        return 2

    print(f"启动 KB Agent 服务： http://{args.host}:{args.port}")
    print("  GET  /health         健康检查")
    print("  GET  /tools          工具清单与权限白名单")
    print("  POST /agent/run      执行（一次性返回）")
    print("  POST /agent/stream   执行（SSE 流式，看得到每一步工具调用）")
    print("  GET  /agent/trace    执行轨迹（审计）")
    uvicorn.run("kb_agent.api_fastapi:app_factory", factory=True,
                host=args.host, port=args.port,
                reload=args.reload, workers=args.workers if not args.reload else 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
