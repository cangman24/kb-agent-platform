# -*- coding: utf-8 -*-
"""kb_agent/api_fastapi.py —— FastAPI 接线层（薄适配）。

★ 这一层**只做传输**，不含任何业务逻辑：
      HTTP 请求 -> 拆成 dict -> core.service.AgentService -> JSON / SSE

  所以它只有一百来行，且不改动任何已验证的核心代码。业务逻辑（白名单、
  熔断、Checkpoint、上下文压缩）全部在 core/ 里，已被零依赖单测覆盖。

★ 重要：本文件**需要安装 fastapi 才能导入**。
  核心逻辑已在 core/service.py 覆盖（本仓库 342 项零依赖单测），
  不依赖本文件验证。没装 fastapi 时请直接用 core.service.AgentService
  —— 它是框架无关的，收 dict、回 dict。

启动方式（真机）：
    pip install fastapi uvicorn
    python -m kb_agent.server
或：
    uvicorn kb_agent.api_fastapi:app_factory --factory --port 8000

接口：
    GET  /health             健康检查（工具数、checkpointer、熔断上限）
    GET  /tools              工具清单与路由白名单
    POST /agent/run          一次性执行    {"query": "...", "thread_id": "..."}
    POST /agent/stream       SSE 流式执行（看得到工具调用的每一步）
    GET  /agent/stream       SSE 流式（方便 EventSource 联调）
    POST /agent/resume       从 Checkpoint 续跑
    GET  /agent/trace        取某次执行的完整轨迹（审计用）
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, Optional

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - 取决于环境
    raise ImportError(
        "本文件需要 fastapi / pydantic：\n"
        "    pip install fastapi uvicorn\n"
        "若暂时不装，请直接使用框架无关的 core.service.AgentService。\n"
        f"原始错误: {exc}"
    ) from exc

from core.service import AgentService, ServiceConfig, new_request_id
from core.sse import SSE_MEDIA_TYPE

# SSE 响应头：关掉中间层缓冲，否则流式会被攒着一起发
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


# --------------------------------------------------------------------- 契约
class RunRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="用户指令")
    thread_id: Optional[str] = Field(
        None, description="会话 id；不传则自动生成，用于 Checkpoint 续跑")


class ResumeRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, description="要续跑的会话 id")


class RunResponse(BaseModel):
    thread_id: str
    query: str
    answer: str
    finished: bool
    tool_calls: int
    citations: list = Field(default_factory=list)
    stop_reason: Optional[str] = None
    error: Optional[str] = None


# --------------------------------------------------------------------- 装配
def build_app(service: AgentService, title: str = "KB Agent Platform") -> "FastAPI":
    """把 AgentService 挂到 FastAPI 上。

    ★ 用**依赖注入**而不是模块级全局单例：
      同一份代码可以在测试里注入假引擎、在生产里注入真引擎，
      也支持一个进程托管多套工具集与权限配置。
    """
    config = service.config or ServiceConfig()
    app = FastAPI(title=title, version=config.version)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return service.health()

    @app.get("/tools")
    def tools() -> Dict[str, Any]:
        """暴露工具清单与路由白名单 —— 权限配置错了这里一眼能看出来。"""
        return service.tools()

    @app.post("/agent/run", response_model=RunResponse)
    def agent_run(req: RunRequest) -> Dict[str, Any]:
        return service.run(req.query, req.thread_id)

    @app.post("/agent/stream")
    def agent_stream(req: RunRequest) -> "StreamingResponse":
        def gen() -> Iterator[str]:
            yield from service.run_stream(req.query, req.thread_id)

        return StreamingResponse(gen(), media_type=SSE_MEDIA_TYPE,
                                 headers=SSE_HEADERS)

    @app.get("/agent/stream")
    def agent_stream_get(q: str = Query(..., min_length=1),
                         thread_id: Optional[str] = None) -> "StreamingResponse":
        """GET 版：浏览器 `new EventSource('/agent/stream?q=...')` 直接联调。"""
        def gen() -> Iterator[str]:
            yield from service.run_stream(q, thread_id or new_request_id())

        return StreamingResponse(gen(), media_type=SSE_MEDIA_TYPE,
                                 headers=SSE_HEADERS)

    @app.post("/agent/resume", response_model=RunResponse)
    def agent_resume(req: ResumeRequest) -> Dict[str, Any]:
        """从 Checkpoint 续跑。

        ★ 熔断/中断后不用重问一遍 —— thread_id 带上就能接着跑，
          intent_fn 不会重复调用（已由单测验证）。
        """
        return service.resume(req.thread_id)

    @app.get("/agent/trace")
    def agent_trace(thread_id: str = Query(..., min_length=1)) -> Dict[str, Any]:
        """取执行轨迹。这是**审计留痕**的接口：谁在什么时候调了什么工具。"""
        trace = service.trace(thread_id)
        if not trace.get("found"):
            raise HTTPException(status_code=404,
                                detail=f"未找到会话 {thread_id} 的轨迹")
        return trace

    return app


def app_factory() -> "FastAPI":
    """uvicorn --factory 入口。

    真正的装配逻辑放在 `kb_agent/app.py::build_production_service`
    （内置知识库 + 真 LLM + 受控引擎）。缺 DEEPSEEK_API_KEY 时会给出
    可读错误，而不是静默降级成一个"看起来在跑"的服务。
    """
    from kb_agent.app import build_production_service
    return build_app(build_production_service())
