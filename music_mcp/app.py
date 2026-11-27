"""MCP 工具装配：搜索、播放、音源管理。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from music_mcp.cache import cache_store
from music_mcp.config import settings
from music_mcp.local_player import player
from music_mcp.mcp_server import MCPServer, MCPError
from music_mcp.models import PlayResult, Song
from music_mcp.ranking import filter_and_rank_songs
from music_mcp.resource_proxy import proxy
from music_mcp.sources import manager
from music_mcp.utils import compact_json, truncate_text

logger = logging.getLogger("music_mcp.app")

SOURCE_CHOICES = ["auto", *manager.names]


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _resource_link(song: Song, url: str, mime_type: str) -> dict:
    return {
        "type": "resource_link",
        "uri": proxy.URI,
        "name": f"{song.title} - {song.artist}" if song.artist else song.title,
        "description": url,
        "mimeType": mime_type,
    }


def _song_public(song: Song) -> dict:
    return song.to_dict()


MIN_AUDIO_BYTES = 400 * 1024  # 400 KB 以下多为试听片段或「请使用客户端」提示音


def _fetch_total_bytes(url: str) -> Optional[int]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": settings.user_agent,
            "Range": "bytes=0-1",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=min(4, settings.source_timeout)) as resp:
        content_range = resp.headers.get("Content-Range", "")
        if "/" in content_range:
            total_part = content_range.rsplit("/", 1)[-1].strip()
            if total_part.isdigit():
                return int(total_part)
        content_length = resp.headers.get("Content-Length", "").strip()
        if resp.status == 200 and content_length.isdigit():
            return int(content_length)
    return None


async def _verify_url(url: str) -> bool:
    """快速验证播放地址可用（Range 0-1），并过滤小于 400 KB 的提示音/试听文件。"""
    try:
        ok = await asyncio.wait_for(proxy.verify_url(url), timeout=4.0)
        if not ok:
            return False
        try:
            total_bytes = await asyncio.wait_for(asyncio.to_thread(_fetch_total_bytes, url), timeout=4.0)
        except Exception:
            # 大小探测超时时放行，避免卡住整个 MCP 响应
            return True
        if total_bytes is not None and 0 < total_bytes < MIN_AUDIO_BYTES:
            logger.info("跳过疑似提示音/试听小文件 (%d bytes < %d): %s", total_bytes, MIN_AUDIO_BYTES, url)
            return False
        return True
    except Exception as exc:
        logger.debug("播放地址验证失败 %s: %s", url, exc)
        return False


def _fmt_song(song: Song) -> str:
    """单行描述一首歌，用于日志。"""
    dur = f"{song.duration // 60}:{song.duration % 60:02d}" if song.duration else "--:--"
    return (
        f"{song.title} - {song.artist or '未知歌手'}"
        f" | 专辑={song.album or '-'} | 时长={dur} | {song.source}:{song.song_id}"
    )


def _log_candidates(tool: str, query: str, candidates: List[Song], tries: int) -> None:
    """打印排序后的候选歌曲列表，便于查看会按什么顺序尝试。"""
    shown = candidates[:tries]
    lines = [f"[{tool}] 查询「{query}」共 {len(candidates)} 个候选，将依次尝试前 {len(shown)} 个："]
    for i, song in enumerate(shown, 1):
        lines.append(f"  #{i} {_fmt_song(song)}")
    if len(candidates) > len(shown):
        lines.append(f"  ... 另有 {len(candidates) - len(shown)} 个候选不会尝试")
    logger.info("\n".join(lines))


def _log_selected(tool: str, rank: int, total: int, song: Song, url: str) -> None:
    logger.info("[%s] ✔ 选中 #%d/%d：%s\n  url=%s", tool, rank, total, _fmt_song(song), truncate_text(url, 120))


def _tool_result(
    texts: List[str],
    structured: Optional[dict] = None,
    is_error: bool = False,
    resource_link: Optional[dict] = None,
) -> dict:
    content = [_text(t) for t in texts]
    if resource_link:
        content.append(resource_link)
    result: Dict[str, Any] = {"content": content, "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


async def _cached_search(query: str, limit: int, source: Optional[str]) -> List[Song]:
    cached = await asyncio.to_thread(cache_store.get_search, query, source, limit)
    if cached is not None:
        logger.debug("命中搜索缓存: %s (%d 首)", query, len(cached))
        return cached
    await cache_store.wait_rate_limit("search")
    songs = await manager.search(query, limit=limit, source=source)
    if songs:
        await asyncio.to_thread(cache_store.set_search, query, source, limit, songs)
    return songs


async def _cached_resolve(song: Song, force_refresh: bool = False) -> Tuple[PlayResult, bool]:
    if not force_refresh and song.song_id:
        cached = await asyncio.to_thread(cache_store.get_play_url, song.source, song.song_id)
        if cached is not None:
            url, mime_type = cached
            logger.debug("命中直链缓存: %s - %s", song.title, song.source)
            return PlayResult(url=url, mime_type=mime_type), True
    await cache_store.wait_rate_limit(song.source)
    play = await manager.resolve(song)
    if play.url and song.song_id:
        await asyncio.to_thread(cache_store.set_play_url, song.source, song.song_id, play.url, play.mime_type)
    return play, False


async def _search_song(args: dict) -> dict:
    query = str(args.get("query", "") or "").strip()
    limit = int(args.get("limit", 8) or 8)
    limit = max(1, min(limit, 20))
    source_raw = str(args.get("source", "auto") or "auto").strip()
    source = source_raw if source_raw in manager.names else None
    if not query:
        return _tool_result(["请提供搜索关键词 query"], {"songs": []}, is_error=True)
    fetch_limit = min(20, max(limit * 2, 16)) if source is None else limit
    raw_songs = await _cached_search(query, limit=fetch_limit, source=source)
    songs = filter_and_rank_songs(raw_songs, query)[:limit]
    logger.info("[search_song] 原始 %d 条 → 过滤排序后 %d 条", len(raw_songs), len(songs))
    _log_candidates("search_song", query, songs, limit)
    payload = [_song_public(s) for s in songs]
    if not payload:
        return _tool_result(
            [f"没有搜索到与「{query}」相关的歌曲，请换个关键词试试。"],
            {"songs": []},
        )
    text = compact_json(payload)
    summary = f"共找到 {len(payload)} 首与「{query}」相关的歌曲："
    for i, s in enumerate(payload[:6], 1):
        summary += f"\n{i}. {s['title']} - {s['artist']}（{s['source_name']}）"
    return _tool_result([summary, text], {"songs": payload})


_TIER2_KEYWORDS = ("live", "现场", "演唱会", "合唱", "原声", "ost", "片尾曲", "主题曲", "插曲")
_TIER3_KEYWORDS = (
    "翻唱", "cover", "dj", "慢摇", "remix", "电音", "舞曲",
    "女声版", "男声版", "女生版", "男生版", "抖音", "网红", "热播",
    "加速", "降调", "升调", "0.8x", "1.2x", "改编", "戏腔版", "粤语版",
)
_TIER4_KEYWORDS = (
    "伴奏", "消音", "卡拉ok", "ktv", "纯音乐", "乐器", "曲子",
    "钢琴", "琵琶", "古筝", "二胡", "笛子", "箫", "吉他", "小提琴",
    "萨克斯", "尤克里里", "八音盒", "铃声", "高潮版", "片段", "试听",
)
_ALL_VERSION_KEYWORDS = _TIER2_KEYWORDS + _TIER3_KEYWORDS + _TIER4_KEYWORDS


_QUERY_FILLER_WORDS = {
    "原版", "原唱", "完整版", "完整", "高清", "无损", "正版", "歌曲", "音乐", "一首",
}


def _strip_brackets(text: str) -> str:
    return re.sub(r"[（(【\[][^）)】\]]*[）)】\]]", "", text).strip()


def _filter_relevant(query: str, songs: List[Song]) -> List[Song]:
    """过滤明显跑题的搜索结果（如搜「周杰伦 晴天」时返回「布拉格广场 - 周杰伦」）。"""
    q = (query or "").strip()
    if not q or not songs:
        return songs

    raw_parts = [part.strip().lower() for part in re.split(r"[\s\-—·/、,，]+", q) if part.strip()]
    core_parts = [part for part in raw_parts if part not in _QUERY_FILLER_WORDS] or raw_parts

    matched: List[Song] = []
    for song in songs:
        title_lower = (song.title or "").strip().lower()
        artist_lower = (song.artist or "").strip().lower()
        combined = f"{title_lower} {artist_lower}"

        if not all(part in combined for part in core_parts):
            continue

        if len(core_parts) >= 2:
            non_artist_parts = [
                part for part in core_parts
                if part not in artist_lower and part not in _ALL_VERSION_KEYWORDS
            ]
            if non_artist_parts and not all(part in title_lower for part in non_artist_parts):
                continue

        matched.append(song)
    return matched


def _rank_candidates(query: str, songs: List[Song]) -> List[Song]:
    """未指定版本时优先原版，原版不可用时自动降级到 Live/翻唱/DJ/器乐。"""
    if len(songs) <= 1:
        return songs

    q_lower = query.strip().lower()
    q_parts = [p for p in re.split(r"\s+", q_lower) if p]
    requested_versions = [kw for kw in _ALL_VERSION_KEYWORDS if kw in q_lower]

    def score_song(indexed_song: Tuple[int, Song]) -> Tuple[int, int]:
        idx, song = indexed_song
        title_lower = (song.title or "").strip().lower()
        artist_lower = (song.artist or "").strip().lower()
        combined = f"{title_lower} {artist_lower}"
        clean_title = _strip_brackets(title_lower)

        # 如果用户明确指定了版本词（例如 "青花瓷 钢琴" 或 "dj"）
        if requested_versions:
            matched_req = any(kw in combined for kw in requested_versions)
            tier = 0 if matched_req else 3
            return (tier, idx)

        # 判断非原版梯队
        if any(kw in combined for kw in _TIER4_KEYWORDS):
            tier = 40
        elif any(kw in combined for kw in _TIER3_KEYWORDS):
            tier = 30
        elif any(kw in combined for kw in _TIER2_KEYWORDS):
            tier = 20
        else:
            tier = 0

        # 过短音频（疑似铃声/片段）降权
        duration = getattr(song, "duration", 0) or 0
        if 0 < duration < 60:
            tier += 35

        # 标题干净度与匹配度微调（仅在同一大梯队内比较）
        clean_q = q_lower
        for part in q_parts:
            if part and part in artist_lower and len(q_parts) > 1:
                clean_q = clean_q.replace(part, "").strip()
        if clean_title == clean_q or title_lower == clean_q:
            tier -= 4
        elif clean_q and clean_q in clean_title:
            tier -= 2

        # 如果 query 中带了歌手名且命中歌手，优先度提升
        if len(q_parts) >= 2 and any(p in artist_lower for p in q_parts):
            tier -= 3

        # 标题包含括号修饰（如 "(xxx版)"）轻微后置
        if title_lower != clean_title:
            tier += 1

        return (tier, idx)

    return [s for _, s in sorted(enumerate(songs), key=score_song)]


async def _resolve_candidates(query: str, song_id: str, source: Optional[str]) -> List[Song]:
    """根据参数构造候选歌曲列表（支持原版优先排序）。"""
    if song_id and source:
        src = manager.get(source)
        return [
            Song(
                source=source,
                source_name=src.display_name if src else source,
                song_id=song_id,
                title=query or song_id,
                artist="",
            )
        ]
    songs = await _cached_search(query, limit=20, source=source)
    if song_id:
        matched = [s for s in songs if s.song_id == song_id]
        if matched:
            return matched
    return filter_and_rank_songs(songs, query)


async def _play_song(args: dict) -> dict:
    query = str(args.get("query", "") or "").strip()
    song_id = str(args.get("song_id", "") or "").strip()
    source_raw = str(args.get("source", "auto") or "auto").strip()
    source = source_raw if source_raw in manager.names else None

    if not query and not song_id:
        return _tool_result(
            ["请提供歌曲名称 query（例如：播放青花瓷），或提供 song_id。"],
            {"playing": False},
            is_error=True,
        )

    try:
        candidates = await _resolve_candidates(query, song_id, source)
    except Exception as exc:
        logger.exception("搜索歌曲失败")
        return _tool_result([f"搜索失败: {exc}"], {"playing": False}, is_error=True)

    if not candidates:
        return _tool_result(
            [f"没有找到「{query or song_id}」对应的歌曲，请换一个说法或歌名。"],
            {"playing": False},
        )

    verify = True  # 本地/设备端播放前同样验证链接可用性
    last_error = "未知错误"
    deadline = time.monotonic() + 12.0  # 防止多候选重试超时导致服务端断连
    tries = candidates[: settings.max_resolve_tries]
    _log_candidates("play_song", query or song_id, candidates, settings.max_resolve_tries)
    for rank, song in enumerate(tries, 1):
        if time.monotonic() >= deadline:
            last_error = "解析超时，已停止继续尝试"
            logger.info("[play_song] 解析超时，停止尝试（已试到 #%d）", rank - 1)
            break
        try:
            play, from_cache = await _cached_resolve(song)
            if not play.url:
                logger.info("[play_song] ✘ 跳过 #%d/%d：未解析到播放地址 | %s", rank, len(tries), _fmt_song(song))
                continue
            if verify and not await _verify_url(play.url):
                if from_cache and song.song_id:
                    await asyncio.to_thread(cache_store.invalidate_play_url, song.source, song.song_id)
                    play, _ = await _cached_resolve(song, force_refresh=True)
                    if not play.url or not await _verify_url(play.url):
                        last_error = f"「{song.title}」无完整可播放直链（可能已下架、VIP限制或仅返回客户端提示音）"
                        logger.info("[play_song] ✘ 跳过 #%d/%d：直链验证失败(缓存已刷新) | %s", rank, len(tries), _fmt_song(song))
                        continue
                else:
                    last_error = f"「{song.title}」无完整可播放直链（可能已下架、VIP限制或仅返回客户端提示音）"
                    logger.info("[play_song] ✘ 跳过 #%d/%d：直链验证失败 | %s", rank, len(tries), _fmt_song(song))
                    continue
            _log_selected("play_song", rank, len(tries), song, play.url)
            song_label = f"{song.title} - {song.artist}" if song.artist else song.title
            if settings.auto_play_local and player.available:
                msg = player.play(play.url, song_label)
                structured = {
                    "playing": True,
                    "song": _song_public(song),
                    "play_url": play.url,
                    "mime_type": play.mime_type,
                    "source": song.source_name,
                    "local_player": msg,
                }
                texts = [
                    compact_json(
                        {
                            "playing": True,
                            "title": song.title,
                            "artist": song.artist,
                            "source": song.source_name,
                            "message": f"正在播放：{song_label}",
                        }
                    ),
                    msg,
                ]
            else:
                structured = {
                    "playing": False,
                    "resolved": True,
                    "song": _song_public(song),
                    "play_url": play.url,
                    "mime_type": play.mime_type,
                    "source": song.source_name,
                }
                texts = [
                    compact_json(
                        {
                            "playing": False,
                            "resolved": True,
                            "title": song.title,
                            "artist": song.artist,
                            "play_url": play.url,
                            "next_action": "请立即调用设备端工具 self.music.play_url(url=play_url, title=title) 开始播放",
                        }
                    )
                ]
            return _tool_result(
                texts,
                structured,
                resource_link=_resource_link(song, play.url, play.mime_type),
            )
        except Exception as exc:
            last_error = str(exc)
            logger.warning("解析 %s 播放地址失败: %s", song.title, exc)

    return _tool_result(
        [
            f"无法播放「{query or song_id}」：{last_error}。"
            "已尝试多个音源仍失败，可能是版权限制，请换一首歌试试。"
        ],
        {"playing": False, "reason": last_error},
        is_error=True,
    )


async def _get_play_url(args: dict) -> dict:
    query = str(args.get("query", "") or "").strip()
    song_id = str(args.get("song_id", "") or "").strip()
    source_raw = str(args.get("source", "auto") or "auto").strip()
    source = source_raw if source_raw in manager.names else None
    if not query and not song_id:
        return _tool_result(["需要 query 或 song_id"], {"play_url": None}, is_error=True)
    candidates = await _resolve_candidates(query, song_id, source)
    if not candidates:
        return _tool_result(["未找到歌曲"], {"play_url": None})
    tries = candidates[: settings.max_resolve_tries]
    _log_candidates("get_play_url", query or song_id, candidates, settings.max_resolve_tries)
    for rank, song in enumerate(tries, 1):
        try:
            play, _ = await _cached_resolve(song)
            if play.url:
                _log_selected("get_play_url", rank, len(tries), song, play.url)
                return _tool_result(
                    [compact_json({"title": song.title, "artist": song.artist, "play_url": play.url})],
                    {"song": _song_public(song), "play_url": play.url},
                )
        except Exception as exc:
            logger.info("[get_play_url] ✘ 跳过 #%d/%d：%s | %s", rank, len(tries), exc, _fmt_song(song))
    return _tool_result(["未能解析到播放地址"], {"play_url": None}, is_error=True)


async def _list_sources(args: dict) -> dict:
    health = await manager.health()
    payload = [
        {
            "name": name,
            "display_name": info["name"],
            "ok": info["ok"],
        }
        for name, info in health.items()
    ]
    text = compact_json(payload)
    return _tool_result([f"可用音源 {len(payload)} 个：\n" + text], {"sources": payload})


async def _local_player_control(args: dict) -> dict:
    action = str(args.get("action", "status") or "status").strip()
    if action == "play":
        url = str(args.get("url", "") or "").strip()
        if not url:
            return _tool_result(["需要 url 参数"], {"local_player": player.status()}, is_error=True)
        msg = player.play(url)
        return _tool_result([msg], {"local_player": player.status()})
    if action == "stop":
        msg = player.stop()
        return _tool_result([msg], {"local_player": player.status()})
    if action == "status":
        return _tool_result([compact_json(player.status())], {"local_player": player.status()})
    return _tool_result(
        [f"不支持的 action: {action}（支持 play/stop/status）"],
        {"local_player": player.status()},
        is_error=True,
    )


def build_server() -> MCPServer:
    server = MCPServer(server_name="xiaozhi-music-mcp", version="0.1.0")

    server.add_tool(
        "search_song",
        (
            "在免费音乐源中搜索歌曲。当用户要求找歌、点歌、搜索歌曲或询问某首歌是否存在时使用。"
            "支持按歌名、歌手名或“歌名 歌手”搜索，返回歌曲列表（含音源、歌名、歌手、专辑、时长）。"
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，例如：青花瓷 或 周杰伦 或 周杰伦 青花瓷"},
                "source": {
                    "type": "string",
                    "enum": SOURCE_CHOICES,
                    "description": "指定音源；auto 表示在所有可用音源中搜索",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回结果数量，默认 8"},
            },
            "required": ["query"],
        },
        _search_song,
    )

    server.add_tool(
        "play_song",
        (
            "播放歌曲。用户说“播放xxx”“来一首xxx”“放点xxx”时使用。"
            "传入歌名/歌手后会自动搜索并解析可播放地址；也可传入 search_song 返回的 song_id 和 source 精确播放。"
            "返回内容包含文本信息和 ResourceLink 音频资源（可通过 resource://read_from_http 分页读取后播放）。"
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "歌曲名，或 歌手 + 歌名，例如：青花瓷"},
                "song_id": {"type": "string", "description": "search_song 返回的歌曲 ID（可选）"},
                "source": {
                    "type": "string",
                    "enum": SOURCE_CHOICES,
                    "description": "指定音源；auto 表示自动选择",
                },
                "prefer_quality": {
                    "type": "string",
                    "enum": ["standard", "high"],
                    "description": "音质偏好，默认 standard",
                },
            },
        },
        _play_song,
    )

    server.add_tool(
        "get_play_url",
        (
            "解析歌曲的直链播放地址。当其他工具需要具体音频 URL 时使用。"
            "参数与 play_song 相同，只返回地址不播放。"
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "歌曲名或歌手+歌名"},
                "song_id": {"type": "string", "description": "歌曲 ID（可选）"},
                "source": {
                    "type": "string",
                    "enum": SOURCE_CHOICES,
                    "description": "指定音源",
                },
            },
        },
        _get_play_url,
    )

    server.add_tool(
        "list_sources",
        "列出当前可用的免费音乐源及健康状态。",
        {"type": "object", "properties": {}},
        _list_sources,
    )

    server.add_tool(
        "local_player_control",
        (
            "控制本机媒体播放器（mpv/vlc/mplayer）播放/停止，仅当 LOCAL_PLAYER 配置启用时有效。"
            "用于电脑端本地播放场景；小智硬件播放走 play_song 的音频资源，不需要此工具。"
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "stop", "status"],
                    "description": "操作类型",
                },
                "url": {"type": "string", "description": "action=play 时必填的音频地址"},
            },
            "required": ["action"],
        },
        _local_player_control,
    )

    async def _read_audio_resource(uri: str, arguments: Optional[dict]) -> list:
        blob = await proxy.read_chunk(arguments)
        return [
            {
                "uri": uri,
                "mimeType": "audio/mpeg",
                "blob": blob,
            }
        ]

    server.add_resource(
        proxy.URI,
        "音频流代理",
        _read_audio_resource,
        mime_type="audio/mpeg",
        description=(
            "通过 HTTP Range 分页读取远程音频。arguments: "
            '{"url": "音频地址", "start": 0, "end": 102400}；'
            "到达末尾返回 base64 编码的 [DONE]。"
        ),
    )
    return server