"""配置加载：环境变量 + .env 文件。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 优先加载运行目录下的 .env（pip 安装后从任意目录运行也生效），
# 再加载项目根目录的 .env 作为兜底。
load_dotenv(Path.cwd() / ".env", override=False)
load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


@dataclass
class Settings:
    enabled_sources: List[str] = field(
        default_factory=lambda: [s.strip() for s in os.getenv("MUSIC_SOURCES", "kuwo,netease,migu,kugou").split(",") if s.strip()]
    )
    source_timeout: int = _int("SOURCE_TIMEOUT", 15)
    http_proxy: Optional[str] = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    user_agent: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    )
    # 播放地址解析时的候选尝试次数（含失败自动换下一首）
    max_resolve_tries: int = _int("MAX_RESOLVE_TRIES", 8)
    # resource/read 分页大小上限（字节）
    max_page_size: int = _int("MAX_PAGE_SIZE", 1024 * 1024)
    # 本地播放器：none / mpv / vlc
    local_player: str = os.getenv("LOCAL_PLAYER", "none").strip().lower()
    auto_play_local: bool = _bool("AUTO_PLAY_LOCAL", False)
    # 自定义聚合音乐 API（如自建的 lx-music-api-server），留空则禁用
    custom_api_base: Optional[str] = os.getenv("CUSTOM_API_BASE") or None
    cache_dir: Path = Path(os.getenv("CACHE_DIR", str(PROJECT_ROOT / "cache")))
    # SQLite 双层缓存与限流配置：搜索默认缓存 2 天，播放直链默认缓存 15 分钟
    search_cache_ttl: int = _int("SEARCH_CACHE_TTL", 2 * 24 * 3600)
    play_url_cache_ttl: int = _int("PLAY_URL_CACHE_TTL", 15 * 60)
    source_min_interval_ms: int = _int("SOURCE_MIN_INTERVAL_MS", 400)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()


settings = Settings()