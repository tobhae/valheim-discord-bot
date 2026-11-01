"""Sources of Valheim log lines: what every source has (LogSource), a local file, or none.

The other two live next to their APIs: DatHostConsoleSource in dathost.py and
DockerLogSource in docker_logs.py.
"""

from __future__ import annotations

import codecs
import logging
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

FILE_BACKLOG_BYTES = 256 * 1024


class LogSource(Protocol):
    """Where the server log comes from (EVENT_SOURCE)."""

    async def read_backlog(self) -> list[str]:
        """Called once at startup: recent lines from before the bot started. The bot reads
        them without posting, only to learn who is already online, so a restart never
        re-announces anything."""
        ...

    async def read_new_lines(self) -> list[str]:
        """Lines added since the last call."""
        ...

    async def close(self) -> None:
        """Called on shutdown. Nothing to do unless a source overrides it."""


class LineSplitter:
    """Turns bytes arriving in chunks into text lines.

    A chunk can end in the middle of a line or of a multi-byte character (å, ø...);
    both are kept until the next chunk completes them.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._partial = ""

    def feed(self, chunk: bytes) -> list[str]:
        """The lines completed by this chunk."""
        *lines, self._partial = (self._partial + self._decoder.decode(chunk)).split("\n")
        return [line.rstrip("\r") for line in lines]

    def flush(self) -> str | None:
        """The unterminated last line once the input has ended, if any."""
        rest = self._partial + self._decoder.decode(b"", final=True)
        self.reset()
        return rest.rstrip("\r") or None

    def reset(self) -> None:
        self._decoder.reset()
        self._partial = ""


class FileLogSource(LogSource):
    """Tails a log file, like `tail -F`. Handles truncation and recreation."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._position = 0  # set to the end of the file by read_backlog()
        self._inode: int | None = None
        # Keeps an unterminated last line until the writer finishes it.
        self._lines = LineSplitter()

    async def read_backlog(self) -> list[str]:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            self._position, self._inode = 0, None
            return []
        self._position, self._inode = stat.st_size, stat.st_ino
        start = max(0, stat.st_size - FILE_BACKLOG_BYTES)
        with self._path.open("rb") as f:
            f.seek(start)
            lines = f.read(stat.st_size - start).decode("utf-8", errors="replace").split("\n")
        if start > 0:
            lines = lines[1:]  # probably cut in the middle
        return [line.rstrip("\r") for line in lines if line]

    async def read_new_lines(self) -> list[str]:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return []
        if stat.st_ino != self._inode or stat.st_size < self._position:
            log.info("%s was created, rotated or truncated, reading from start", self._path)
            self._position, self._inode = 0, stat.st_ino
            self._lines.reset()
        if stat.st_size == self._position:
            return []

        with self._path.open("rb") as f:
            f.seek(self._position)
            chunk = f.read()
            self._position = f.tell()
        return self._lines.feed(chunk)


class NullLogSource(LogSource):
    async def read_backlog(self) -> list[str]:
        return []

    async def read_new_lines(self) -> list[str]:
        return []
