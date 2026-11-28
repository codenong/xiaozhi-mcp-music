from __future__ import annotations

import re
from typing import Iterable

from music_mcp.models import Song

HARD_REJECT_TAGS: tuple[str, ...] = (
    "伴奏",
    "纯伴奏",
    "和声伴奏",
    "消音",
    "卡拉ok",
    "ktv",
    "铃声",
    "高潮版",
    "片段",
    "试听",
    "dj",
    "remix",
    "变速",
    "加快",
    "0.8x",
    "0.9x",
    "1.2x",
    "慢摇",
    "电音版",
    "夜店版",
    "广场舞",
    "喊麦",
    "ai翻唱",
    "鬼畜",
    "直播",
    "弹唱",
    "播客",
    "电台",
    "翻唱主播",
)

SOFT_PENALTY_TAGS: tuple[str, ...] = (
    "live",
    "现场",
    "演唱会",
    "翻唱",
    "cover",
    "钢琴版",
    "吉他版",
    "纯音乐",
    "二胡",
    "古筝",
    "琵琶",
    "笛子",
    "箫",
    "小提琴",
    "萨克斯",
    "尤克里里",
    "钢琴",
    "吉他",
    "器乐",
    "乐器",
    "轻音乐",
    "八音盒",
    "串烧",
    "合集",
    "女声版",
    "男声版",
    "戏腔",
    "影视版",
    "广播剧",
    "朗诵",
)

# 同义标签组：用户请求其中任意一个，整组都视为已请求
TAG_GROUPS: tuple[tuple[str, ...], ...] = (
    ("live", "现场", "演唱会"),
    ("翻唱", "cover"),
    ("钢琴版", "钢琴", "吉他版", "吉他"),
    ("纯音乐", "器乐", "乐器", "轻音乐"),
    ("女声版", "男声版"),
)


# 具体乐器词；用户请求其中任意一个时，专辑名里的泛器乐标签（如"轻音乐"）也不应扣分
INSTRUMENT_TAGS: tuple[str, ...] = (
    "二胡", "古筝", "琵琶", "笛子", "箫", "小提琴", "萨克斯", "尤克里里",
    "钢琴", "吉他", "钢琴版", "吉他版",
)
GENERIC_INSTRUMENTAL_TAGS: tuple[str, ...] = ("纯音乐", "器乐", "乐器", "轻音乐", "八音盒")


def _expand_groups(requested: set[str]) -> set[str]:
    expanded = set(requested)
    if requested.intersection(INSTRUMENT_TAGS):
        expanded.update(GENERIC_INSTRUMENTAL_TAGS)
    for group in TAG_GROUPS:
        if requested.intersection(group):
            expanded.update(group)
    return expanded


SOURCE_WEIGHTS: dict[str, float] = {
    "kuwo": 20.0,
    "netease": 18.0,
    "migu": 5.0,
    "kugou": 5.0,
}


def _extract_core_title(title: str) -> str:
    cleaned = re.sub(r"[\(\[（【].*?[\)\]）】]", "", title)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _requested_tags(query_lower: str, tags: Iterable[str]) -> set[str]:
    return {tag for tag in tags if tag in query_lower}


def _artist_parts(artist: str) -> list[str]:
    cleaned = re.sub(r"[\(\[（【].*?[\)\]）】]", "", artist or "")
    return [p.strip().lower() for p in re.split(r"[/、,&]+", cleaned) if p.strip()]


def song_is_exact_artist(song: Song, target_artist: str) -> bool:
    """判断歌曲歌手是否为纯粹的目标原唱（非主播拼盘/合唱蹭名）。"""
    t = (target_artist or "").strip().lower()
    if not t:
        return False
    parts = _artist_parts(song.artist or "")
    return len(parts) == 1 and (parts[0] == t or t in parts[0] or parts[0] in t)


def song_matches_artist(song: Song, target_artist: str) -> bool:
    """判断歌曲歌手是否命中目标歌手（过滤含直播/弹唱等杂质歌手名）。"""
    t = (target_artist or "").strip().lower()
    a = (song.artist or "").strip().lower()
    if not t or not a:
        return False
    if any(bad in a for bad in ("直播", "弹唱", "翻唱", "cover", "dj", "播客", "电台")):
        return False
    parts = _artist_parts(song.artist or "")
    if any(p == t or t in p or p in t for p in parts):
        return True
    t_parts = [p for p in re.split(r"[\s/、,&，]+", t) if len(p) >= 2]
    return bool(t_parts and any(p in a for p in t_parts))


def filter_and_rank_songs(
    songs: list[Song],
    query: str,
    allow_fallback: bool = True,
    target_artist: str = "",
    target_version: str = "",
) -> list[Song]:
    """根据查询意图过滤低质量/非原版结果并跨音源重排。"""
    if not songs:
        return []

    effective_q = f"{query} {target_artist} {target_version}".strip()
    q_lower = effective_q.lower()
    if not q_lower:
        return list(songs)
    core_query_lower = (query or "").lower().strip()
    target_version_l = (target_version or "").strip().lower()

    tokens = [t for t in re.split(r"[\s\-—/]+", q_lower) if t]
    requested_hard = _requested_tags(q_lower, HARD_REJECT_TAGS)
    requested_soft = _expand_groups(_requested_tags(q_lower, SOFT_PENALTY_TAGS))

    requested_all = requested_hard | requested_soft
    # 去掉版本词后剩下的才是"关键词"（歌名/歌手），如「沧海一声笑 二胡」→ 沧海一声笑
    keyword_text = q_lower
    for tag in sorted(requested_all, key=len, reverse=True):
        keyword_text = keyword_text.replace(tag, " ")
    keyword_tokens = [t for t in re.split(r"[\s\-—/]+", keyword_text) if t]

    scored: list[tuple[float, Song]] = []
    fallback_pool: list[tuple[float, Song]] = []

    for song in songs:
        title_l = (song.title or "").lower()
        artist_l = (song.artist or "").lower()
        album_l = (song.album or "").lower()
        meta_text = f"{title_l} {album_l} {artist_l}"

        if any(tag in meta_text and tag not in requested_hard for tag in HARD_REJECT_TAGS):
            continue

        score = SOURCE_WEIGHTS.get(song.source, 0.0)

        core_title = _extract_core_title(song.title or "")
        if core_title:
            if core_title == core_query_lower or core_title == q_lower:
                score += 55.0
            elif core_title in q_lower or (core_query_lower and core_query_lower in core_title):
                score += 38.0
            elif any(tok in core_title for tok in tokens):
                score += 15.0

        # 标题包含括号修饰但用户未要求特殊版本时轻微扣分
        if title_l != core_title and not (requested_hard or requested_soft or target_version_l):
            score -= 6.0

        # 明确目标歌手强锁定（用户指定或 MCP 静默查到的原唱）
        if target_artist:
            if song_is_exact_artist(song, target_artist):
                score += 80.0
            elif song_matches_artist(song, target_artist):
                score += 20.0
            else:
                score -= 60.0
        else:
            artist_tokens = [tok for tok in tokens if tok not in core_title]
            if artist_tokens and any(tok in artist_l for tok in artist_tokens):
                score += 30.0

        # 用户指定版本词额外加分（如 "钢琴演奏" / "Live" / "DJ"）
        if target_version_l and (target_version_l in meta_text or target_version_l in artist_l):
            score += 45.0

        # 正式专辑加分
        if (song.album or "").strip():
            score += 5.0

        # 时长合理性校验
        if song.duration is not None:
            if 90 <= song.duration <= 420:
                score += 10.0
            elif 0 < song.duration < 60 or song.duration > 600:
                score -= 35.0

        # 用户明确要求某种版本（Live/伴奏等）时，命中该版本的歌曲优先；
        # 但标题和歌手都不含任何查询关键词的歌曲（如只是专辑名带"二胡"）不吃这个加分
        if requested_all and any(tag in meta_text for tag in requested_all):
            if keyword_tokens and not any(tok in title_l or tok in artist_l for tok in keyword_tokens):
                pass
            else:
                score += 40.0

        hit_soft = any(tag in meta_text and tag not in requested_soft for tag in SOFT_PENALTY_TAGS)
        if hit_soft:
            fallback_pool.append((score - 50.0, song))
        else:
            scored.append((score, song))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored or not allow_fallback:
        return [song for _, song in scored]

    fallback_pool.sort(key=lambda item: item[0], reverse=True)
    return [song for _, song in fallback_pool]
