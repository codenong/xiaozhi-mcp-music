"""小智 MCP 桥接入口。

读取 .env 中的 MCP_ENDPOINT（小智控制台生成的 WebSocket 接入点），
使用官方 mcp_pipe.py 的逻辑连接小智服务端，并自动启动本项目的音乐 MCP 服务。

用法:
    python bridge.py [server_name]
默认使用 mcp_config.json 中配置的全部服务；也可指定单个服务名。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 确保以项目根目录为工作目录，mcp_config.json / .venv 相对路径始终有效
os.chdir(Path(__file__).resolve().parent)

load_dotenv()

# 在导入 mcp_pipe 前配置根日志，使 DEBUG 日志（含 JSON-RPC 帧）可输出；
# mcp_pipe 内部的 basicConfig 因根 logger 已有 handler 而不会覆盖。
_log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("websockets.client").setLevel(logging.WARNING)
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logger = logging.getLogger("bridge")


def _load_endpoints(mcp_pipe) -> list[dict[str, str]]:
    cfg = mcp_pipe.load_config()
    raw = cfg.get("mcpEndpoints") or cfg.get("mcp_endpoints") or []
    endpoints: list[dict[str, str]] = []
    if isinstance(raw, list):
        for idx, item in enumerate(raw, 1):
            if isinstance(item, str) and item.strip():
                endpoints.append({"name": f"agent{idx}", "url": item.strip()})
            elif isinstance(item, dict):
                url = str(item.get("url", "") or "").strip()
                if url and not item.get("disabled"):
                    name = str(item.get("name", "") or f"agent{idx}").strip()
                    endpoints.append({"name": name, "url": url})
    if not endpoints:
        env_ep = (os.environ.get("MCP_ENDPOINT") or "").strip()
        if env_ep:
            endpoints.append({"name": "", "url": env_ep})
    return endpoints


def main() -> int:
    import mcp_pipe

    endpoints = _load_endpoints(mcp_pipe)
    if not endpoints:
        print(
            "未配置 MCP 接入点。\n"
            "请在 mcp_config.json 中配置 mcpEndpoints 数组（推荐多智能体）：\n"
            '  "mcpEndpoints": ["wss://api.xiaozhi.me/mcp/?token=xxx1", "wss://api.xiaozhi.me/mcp/?token=xxx2"]\n'
            "或在 .env 中设置：MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=xxx",
            file=sys.stderr,
        )
        return 1

    logger.info("已加载 %d 个智能体接入点", len(endpoints))
    target = sys.argv[1] if len(sys.argv) >= 2 else None
    signal_handler = mcp_pipe.signal_handler
    import signal

    signal.signal(signal.SIGINT, signal_handler)
    try:
        asyncio.run(mcp_pipe.run_pipes(endpoints, target))
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as exc:
        logger.error(f"Program execution error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())