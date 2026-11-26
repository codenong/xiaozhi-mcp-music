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
    ("钢琴版", "吉他版", "纯音乐"),
    ("女声版", "男声版"),
)


def _expand_groups(requested: set[str]) -> set[str]:
    expanded = set(requested)
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


def filter_and_rank_songs(
    songs: list[Song],
    query: str,
    allow_fallback: bool = True,
) -> list[Song]:
    """根据查询意图过滤低质量/非原版结果并跨音源重排。"""
    if not songs:
        return []

    q_lower = query.lower().strip()
    if not q_lower:
        return list(songs)

    tokens = [t for t in re.split(r"[\s\-—/]+", q_lower) if t]
    requested_hard = _requested_tags(q_lower, HARD_REJECT_TAGS)
    requested_soft = _expand_groups(_requested_tags(q_lower, SOFT_PENALTY_TAGS))

    scored: list[tuple[float, Song]] = []
    fallback_pool: list[tuple[float, Song]] = []

    for song in songs:
        title_l = (song.title or "").lower()
        artist_l = (song.artist or "").lower()
        album_l = (song.album or "").lower()
        meta_text = f"{title_l} {album_l}"

        if any(tag in meta_text and tag not in requested_hard for tag in HARD_REJECT_TAGS):
            continue

        score = SOURCE_WEIGHTS.get(song.source, 0.0)

        core_title = _extract_core_title(song.title or "")
        if core_title:
            if core_title == q_lower:
                score += 55.0
            elif core_title in q_lower or q_lower in core_title:
                score += 38.0
            elif any(tok in core_title for tok in tokens):
                score += 15.0

        # 标题包含括号修饰但用户未要求特殊版本时轻微扣分
        if title_l != core_title and not (requested_hard or requested_soft):
            score -= 6.0

        # 歌手匹配加分（如 "周杰伦 晴天"）
        artist_tokens = [tok for tok in tokens if tok not in core_title]
        if artist_tokens and any(tok in artist_l for tok in artist_tokens):
            score += 30.0

        # 正式专辑加分
        if (song.album or "").strip():
            score += 5.0

        # 时长合理性校验
        if song.duration is not None:
            if 90 <= song.duration <= 420:
                score += 10.0
            elif 0 < song.duration < 60 or song.duration > 600:
                score -= 35.0

        # 用户明确要求某种版本（Live/伴奏等）时，命中该版本的歌曲优先
        requested_all = requested_hard | requested_soft
        if requested_all and any(tag in meta_text for tag in requested_all):
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
