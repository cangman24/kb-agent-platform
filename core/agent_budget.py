from concurrent.futures import Future, wait
from contextvars import copy_context
from copy import deepcopy
from threading import Thread
from time import monotonic
from typing import Any, Callable, Dict, List, Optional, TypeVar

from .mcp import ToolRegistry


T = TypeVar("T")


class DeadlineExceeded(Exception):
    def __init__(self, reason: str, *, started: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.started = started


class RunBudget:
    """用单调时钟限制等待，并保留注入时钟与 checkpoint 的墙钟语义。"""

    def __init__(self, total_s: float, started_at: float,
                 clock: Callable[[], float], deadline_at: Optional[float] = None) -> None:
        self.total_s = total_s
        self.started_at = started_at
        self.clock = clock
        self.deadline_at = (deadline_at if deadline_at is not None else
                            monotonic() + self._wall_remaining())

    def _wall_remaining(self) -> float:
        return self.total_s - max(0.0, self.clock() - self.started_at)

    def remaining(self) -> float:
        return min(self.deadline_at - monotonic(), self._wall_remaining())

    def check(self) -> None:
        if self.remaining() <= 0:
            raise DeadlineExceeded("total_timeout")

    def call(self, fn: Callable[..., T], *args: Any,
             tool_timeout: Optional[float] = None) -> T:
        remaining = self.remaining()
        if remaining <= 0:
            raise DeadlineExceeded("total_timeout")

        # 同时到期时，任务总超时优先。
        reason = "total_timeout"
        if tool_timeout is not None and tool_timeout < remaining:
            remaining, reason = tool_timeout, "tool_timeout"
        if remaining <= 0:
            raise DeadlineExceeded(reason)

        until = monotonic() + remaining
        future: Future[T] = Future()
        context = copy_context()

        def work() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                if monotonic() >= until:
                    raise DeadlineExceeded(reason)
                future.set_result(context.run(fn, *args))
            except BaseException as exc:
                future.set_exception(exc)

        Thread(target=work, name="agent-stage", daemon=True).start()
        done, _ = wait((future,), timeout=max(0.0, until - monotonic()))
        if not done:
            raise DeadlineExceeded(reason, started=not future.cancel())

        result = future.result()
        if self.remaining() <= 0:
            raise DeadlineExceeded("total_timeout", started=True)
        if reason == "tool_timeout" and monotonic() >= until:
            raise DeadlineExceeded(reason, started=True)
        return result

    def call_tool(self, registry: ToolRegistry, name: str, args: Dict[str, Any],
                  allowed: List[str], timeout: float) -> Any:
        self.check()
        if name not in allowed:
            return registry.call(name, args, allowed)
        limit = min(timeout, registry.get(name).timeout_s)
        return self.call(registry.call, name, deepcopy(args), list(allowed),
                         tool_timeout=limit)
