"""静默原唱识别：在未指定歌手/版本时，内部快速检索原唱歌手（带内存缓存）。"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("music_mcp.original_artist")

_CACHE: dict[str, Optional[str]] = {}
_MAX_CACHE_SIZE = 512

_NON_ORIGINAL_HINTS = (
    "live", "现场", "演唱会", "翻唱", "cover", "dj", "remix", "伴奏",
    "纯音乐", "钢琴", "吉他", "二胡", "古筝", "铃声", "片段", "试听",
)


def _norm(text: str) -> str:
    cleaned = re.sub(r"[\(\[（【].*?[\)\]）】]", "", text or "")
    return re.sub(r"\s+", "", cleaned).strip().lower()


async def lookup_original_artist(song_title: str) -> Optional[str]:
    """根据纯歌名静默查询原唱歌手，失败或超时返回 None（不阻塞主流程）。"""
    key = (song_title or "").strip()
    if not key:
        return None
    if key in _CACHE:
        return _CACHE[key]

    artist: Optional[str] = None
    norm_key = _norm(key)

    try:
        async with httpx.AsyncClient(timeout=1.5, follow_redirects=True) as client:
            # 1. iTunes Search (CN)：元数据规范且带 releaseDate，按发行时间最早筛原唱
            try:
                resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={
                        "term": key,
                        "country": "CN",
                        "entity": "song",
                        "limit": 8,
                    },
                )
                if resp.status_code == 200:
                    results = resp.json().get("results") or []
                    exact_matches = []
                    for item in results:
                        track = str(item.get("trackName") or "")
                        t_lower = track.lower()
                        if any(h in t_lower for h in _NON_ORIGINAL_HINTS):
                            continue
                        if _norm(track) == norm_key and item.get("artistName"):
                            release = str(item.get("releaseDate") or "9999")
                            exact_matches.append((release, str(item["artistName"]).strip()))
                    if exact_matches:
                        exact_matches.sort(key=lambda x: x[0])
                        artist = exact_matches[0][1]
            except Exception as exc:
                logger.debug("iTunes 原唱查询跳过 (%s): %s", key, exc)

            # 2. 兜底：网易云公开搜索接口（国内网络直连更稳）
            if not artist:
                try:
                    resp = await client.get(
                        "https://music.163.com/api/search/get/web",
                        params={"s": key, "type": 1, "limit": 8},
                        headers={"Referer": "https://music.163.com/"},
                    )
                    if resp.status_code == 200:
                        songs = (resp.json().get("result") or {}).get("songs") or []
                        for item in songs:
                            name = str(item.get("name") or "")
                            if any(h in name.lower() for h in _NON_ORIGINAL_HINTS):
                                continue
                            if _norm(name) == norm_key:
                                artists = item.get("artists") or []
                                if artists and artists[0].get("name"):
                                    artist = str(artists[0]["name"]).strip()
                                    break
                except Exception as exc:
                    logger.debug("网易云原唱查询跳过 (%s): %s", key, exc)
    except Exception:
        pass

    if len(_CACHE) >= _MAX_CACHE_SIZE:
        _CACHE.clear()
    _CACHE[key] = artist
    if artist:
        logger.info("[original_artist] 「%s」识别原唱: %s", key, artist)
    return artist
