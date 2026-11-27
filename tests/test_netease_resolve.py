import asyncio

import pytest

from music_mcp.models import Song
from music_mcp.sources.base import SourceError
from music_mcp.sources.netease import NeteaseSource


class _FakeResp:
    def __init__(self, status_code, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def get(self, url, **kwargs):
        return self._resp


def _song():
    return Song(source="netease", source_name="网易云音乐", song_id="170749", title="x", artist="y")


def _source(resp):
    src = NeteaseSource()
    src._client = _FakeClient(resp)
    return src


def test_redirect_to_404_is_unavailable():
    src = _source(_FakeResp(302, {"location": "http://music.163.com/404"}))
    with pytest.raises(SourceError):
        asyncio.run(src.get_play_url(_song()))


def test_redirect_to_real_audio_is_ok():
    url = "http://m701.music.126.net/2026/abc/xyz.mp3"
    src = _source(_FakeResp(302, {"location": url}))
    play = asyncio.run(src.get_play_url(_song()))
    assert play.url == url
