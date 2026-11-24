"""可选的本地播放器控制（mpv / vlc / mplayer），支持 Windows / Linux / macOS，默认关闭。"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from music_mcp.config import settings

logger = logging.getLogger("music_mcp.local_player")

SUPPORTED_PLAYERS = ("mpv", "vlc", "mplayer")


class LocalPlayer:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._current: str = ""
        self._err_log_path = Path(tempfile.gettempdir()) / "xiaozhi_local_player.log"

    @property
    def _player_name(self) -> str:
        return (settings.local_player or "").strip().lower()

    @property
    def available(self) -> bool:
        return self._player_name in SUPPORTED_PLAYERS

    @property
    def player_path(self) -> Optional[str]:
        name = self._player_name
        if name not in SUPPORTED_PLAYERS:
            return None
        path = shutil.which(name)
        if not path and sys.platform.startswith("win"):
            path = shutil.which(f"{name}.exe")
        return path

    def _build_command(self, exe: str, url: str) -> list[str]:
        name = self._player_name
        if name == "mpv":
            return [exe, "--no-video", "--really-quiet", "--force-window=no", url]
        if name == "mplayer":
            return [exe, "-novideo", "-noconsolecontrols", "-really-quiet", url]
        return [exe, "--intf", "dummy", "--no-video", url]

    def play(self, url: str, title: str = "") -> str:
        if not self.available:
            return "本地播放器未启用（LOCAL_PLAYER 配置为 none）"
        exe = self.player_path
        if not exe:
            return f"未找到 {self._player_name} 可执行文件，请安装并加入 PATH"
        self.stop()
        cmd = self._build_command(exe, url)
        err_file = None
        try:
            err_file = open(self._err_log_path, "w", encoding="utf-8", errors="replace")
            popen_kwargs: dict = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": err_file,
            }
            if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                popen_kwargs["start_new_session"] = True

            proc = subprocess.Popen(cmd, **popen_kwargs)
            err_file.close()
            err_file = None

            time.sleep(0.3)
            ret = proc.poll()
            if ret is not None:
                err_out = self._err_log_path.read_text(encoding="utf-8", errors="replace").strip()
                err_tail = err_out.splitlines()[-1] if err_out else f"exit code {ret}"
                logger.warning("本地播放器启动后立即退出 (%s): %s", self._player_name, err_tail)
                return f"本地播放失败（{self._player_name} 退出: {err_tail}）"

            self._proc = proc
            self._current = url
            logger.info("本地播放开始: %s (%s)", title or url, self._player_name)
            return f"已通过 {self._player_name} 开始播放 {title or url}"
        except Exception as exc:
            if err_file is not None:
                err_file.close()
            logger.warning("本地播放失败: %s", exc)
            return f"本地播放失败: {exc}"

    def stop(self) -> str:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._current = ""
        return "已停止本地播放"

    def status(self) -> dict:
        running = bool(self._proc and self._proc.poll() is None)
        return {
            "player": self._player_name,
            "available": self.available,
            "running": running,
            "current_url": self._current,
        }


player = LocalPlayer()