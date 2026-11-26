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
