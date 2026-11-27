from music_mcp.models import Song
from music_mcp.ranking import filter_and_rank_songs


def _make_song(
    title: str,
    artist: str = "周杰伦",
    album: str = "叶惠美",
    duration: int = 269,
    source: str = "kuwo",
    song_id: str = "1",
) -> Song:
    return Song(
        source=source,
        source_name=source,
        song_id=song_id,
        title=title,
        artist=artist,
        album=album,
        duration=duration,
    )


def test_prefers_original_and_rejects_dj_or_instrumental():
    songs = [
        _make_song("晴天 (DJ加快版)", artist="DJ小鱼", album="", duration=210, song_id="dj"),
        _make_song("晴天 (伴奏)", artist="周杰伦", album="", duration=269, song_id="inst"),
        _make_song("晴天 (Live)", artist="周杰伦", album="演唱会", duration=280, song_id="live"),
        _make_song("晴天", artist="周杰伦", album="叶惠美", duration=269, song_id="orig"),
    ]
    ranked = filter_and_rank_songs(songs, "周杰伦 晴天")
    ids = [s.song_id for s in ranked]
    assert ids == ["orig"]


def test_respects_explicit_live_request():
    songs = [
        _make_song("晴天", artist="周杰伦", album="叶惠美", duration=269, song_id="orig"),
        _make_song("晴天 (Live)", artist="周杰伦", album="演唱会", duration=280, song_id="live"),
    ]
    ranked = filter_and_rank_songs(songs, "晴天 Live")
    assert [s.song_id for s in ranked] == ["live", "orig"]


def test_respects_explicit_instrumental_request():
    songs = [
        _make_song("晴天 (伴奏)", artist="周杰伦", album="", duration=269, song_id="inst"),
        _make_song("晴天", artist="周杰伦", album="叶惠美", duration=269, song_id="orig"),
    ]
    ranked = filter_and_rank_songs(songs, "晴天 伴奏")
    assert any(s.song_id == "inst" for s in ranked)


def test_falls_back_to_live_when_no_studio_version():
    songs = [
        _make_song("某老歌 (DJ版)", duration=200, song_id="dj"),
        _make_song("某老歌 (Live)", duration=240, song_id="live"),
    ]
    ranked = filter_and_rank_songs(songs, "某老歌")
    assert [s.song_id for s in ranked] == ["live"]


def test_instrument_in_query_prefers_instrument_version():
    songs = [
        _make_song("沧海一声笑", artist="许冠杰", album="沧海一声笑", duration=175, source="netease", song_id="orig"),
        _make_song("沧海一声笑（二胡超酷版）", artist="叶小丹", album="超好听的二胡版名曲", duration=245, source="kuwo", song_id="erhu"),
        _make_song("沧海一声笑 (古筝版)", artist="沐九儿", album="古筝曲", duration=321, source="kuwo", song_id="guzheng"),
    ]
    ranked = filter_and_rank_songs(songs, "沧海一声笑 二胡")
    ids = [s.song_id for s in ranked]
    assert ids[0] == "erhu"
    assert "guzheng" not in ids  # 用户要二胡，不应推荐古筝版


def test_instrument_version_not_preferred_without_request():
    songs = [
        _make_song("沧海一声笑（二胡超酷版）", artist="叶小丹", album="二胡名曲", duration=245, source="kuwo", song_id="erhu"),
        _make_song("沧海一声笑", artist="许冠杰", album="沧海一声笑", duration=175, source="netease", song_id="orig"),
    ]
    ranked = filter_and_rank_songs(songs, "沧海一声笑")
    assert ranked[0].song_id == "orig"


def test_instrument_request_tolerates_generic_album_tag():
    songs = [
        _make_song("沧海一声笑 (二胡)", artist="胡贰弦", album="二胡之魂|解压轻音乐", duration=257, source="netease", song_id="erhu2"),
        _make_song("沧海一声笑", artist="许冠杰", album="沧海一声笑", duration=175, source="netease", song_id="orig"),
    ]
    ranked = filter_and_rank_songs(songs, "沧海一声笑 二胡")
    assert [s.song_id for s in ranked][0] == "erhu2"
