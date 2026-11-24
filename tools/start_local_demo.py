"""Start the two independently installed repositories on loopback."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from serve_ui import main as serve_ui


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"


def wait_for_health(process: subprocess.Popen, url: str, log: Path) -> None:
    deadline = time.monotonic() + 15
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.2)
    raise RuntimeError(f"服务未就绪：{url}。请查看 {log}")


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 RAG 和 KB Agent 的本机离线体验")
    parser.add_argument("--rag-root", type=Path, default=ROOT.parent / "domain-rag-qa")
    parser.add_argument("--ui-port", type=int, default=18080)
    parser.add_argument("--agent-port", type=int, default=18100)
    parser.add_argument("--rag-port", type=int, default=18000)
    args = parser.parse_args()
    ports = {args.ui_port, args.agent_port, args.rag_port}
    if len(ports) != 3:
        parser.error("三个端口必须不同")
    for port in ports:
        try:
            with socket.socket() as sock:
                sock.bind((HOST, port))
        except (OSError, OverflowError):
            parser.error(f"端口 {port} 不可用，请选择其他端口")

    rag = args.rag_root.resolve()
    rag_python = rag / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not rag_python.is_file() or not (rag / "demos/serve_offline.py").is_file():
        parser.error(f"请按 RAG README 创建独立虚拟环境，或用 --rag-root 指定正确目录：{rag}")

    logs = ROOT / "data/checkpoints/local-demo"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    services = [
        (rag, [str(rag_python), "-u", "demos/serve_offline.py", "--port", str(args.rag_port)],
         f"http://{HOST}:{args.rag_port}/health", logs / "rag.log"),
        (ROOT, [sys.executable, "-u", "-m", "uvicorn", "kb_agent.agent_api:app_factory",
                "--factory", "--host", HOST, "--port", str(args.agent_port)],
         f"http://{HOST}:{args.agent_port}/agent/health", logs / "agent.log"),
    ]
    processes = []
    try:
        for cwd, command, health, log in services:
            with log.open("wb") as output:
                process = subprocess.Popen(
                    command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            processes.append(process)
            wait_for_health(process, health, log)

        return serve_ui(["--port", str(args.ui_port), "--backend-port", str(args.rag_port),
                         "--agent-port", str(args.agent_port)])
    except (OSError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            if process.poll() is not None:
                continue
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            else:
                process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
