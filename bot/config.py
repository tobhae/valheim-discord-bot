"""Bot configuration from two places.

.env (environment variables): secrets and where things are, which differ between
machines (see .env.example). config.toml: what the bot shows and does; this module
reads its [display] and [timing] sections, messages.py the switches and texts.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    pass


def _get(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value:
        if required:
            raise ConfigError(f"Missing required environment variable {name}")
        return default
    return value


def _int(name: str, default: int | None = None, *, required: bool = False) -> int | None:
    value = _get(name, required=required)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from None


def _choice(name: str, default: str, choices: set[str]) -> str:
    value = _get(name, default).lower()
    if value not in choices:
        raise ConfigError(f"{name} must be one of {sorted(choices)}, got {value!r}")
    return value


def read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Config file {path} not found (set CONFIG_FILE)") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None


def toml_section(data: dict[str, Any], name: str, path: Path) -> dict[str, Any]:
    """A [section] of config.toml, empty if it's missing."""
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: {name} must be a section ([{name}] on its own line), got {value!r}")
    return value


# config.toml section -> setting -> (type, default). A setting missing from the file uses its default.
SETTINGS: dict[str, dict[str, tuple[type, Any]]] = {
    "display": {
        "server_name": (str, "Valheim"),
        "max_players": (int, 0),  # 0: not shown, the count appears without "/max"
        "category_name": (str, "📊 Server Status"),
    },
    "timing": {
        "status_poll_seconds": (int, 30),
        "event_poll_seconds": (int, 15),
        # Discord allows 2 renames per channel per 10 minutes; stay under it.
        "channel_rename_seconds": (int, 300),
    },
}


def read_settings(path: Path) -> dict[str, Any]:
    """The [display] and [timing] values from config.toml, checked and with defaults filled in."""
    data = read_toml(path)
    values: dict[str, Any] = {}
    for section, settings in SETTINGS.items():
        given = toml_section(data, section, path)
        if unknown := set(given) - set(settings):
            raise ConfigError(
                f"{path}: unknown setting(s) {', '.join(sorted(unknown))} under [{section}]. "
                f"Known: {', '.join(settings)}"
            )
        for name, (kind, default) in settings.items():
            value = given.get(name, default)
            # bool is a subclass of int, but `max_players = true` is a mistake.
            if not isinstance(value, kind) or isinstance(value, bool):
                expected = "a whole number" if kind is int else "text in quotes"
                raise ConfigError(f"{path}: [{section}] {name} must be {expected}, got {value!r}")
            if kind is str and not value.strip():
                raise ConfigError(f"{path}: [{section}] {name} can't be empty")
            if kind is int and value < 0:
                raise ConfigError(f"{path}: [{section}] {name} can't be negative")
            values[name] = value
    if len(values["category_name"]) > 100:
        raise ConfigError(f"{path}: [display] category_name can be at most 100 characters (Discord's limit)")
    if values["channel_rename_seconds"] < 300:
        raise ConfigError(f"{path}: [timing] channel_rename_seconds below 300 will hit Discord's rename rate limit")
    for name in ("status_poll_seconds", "event_poll_seconds"):
        if values[name] < 1:
            raise ConfigError(f"{path}: [timing] {name} must be at least 1")
    return values


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int

    server_name: str
    max_players: int | None

    status_source: str  # "dathost" | "a2s"
    event_source: str  # "dathost" | "docker" | "file" | "none"

    dathost_email: str | None
    dathost_password: str | None
    dathost_server_id: str | None

    a2s_host: str
    a2s_port: int

    event_log_file: Path | None

    docker_container: str | None
    docker_socket: str

    status_category_name: str
    status_channel_id: int | None
    players_channel_id: int | None
    events_channel_id: int | None

    status_poll_seconds: int
    event_poll_seconds: int
    channel_rename_seconds: int

    state_file: Path
    config_file: Path

    @classmethod
    def load(cls) -> Config:
        config_file = Path(_get("CONFIG_FILE", "config.toml"))
        settings = read_settings(config_file)
        status_source = _choice("STATUS_SOURCE", "dathost", {"dathost", "a2s"})
        event_source = _choice("EVENT_SOURCE", "none", {"dathost", "docker", "file", "none"})

        needs_dathost = "dathost" in (status_source, event_source)
        return cls(
            discord_token=_get("DISCORD_TOKEN", required=True),
            guild_id=_int("DISCORD_GUILD_ID", required=True),
            server_name=settings["server_name"],
            max_players=settings["max_players"] or None,
            status_source=status_source,
            event_source=event_source,
            dathost_email=_get("DATHOST_EMAIL", required=needs_dathost),
            dathost_password=_get("DATHOST_PASSWORD", required=needs_dathost),
            dathost_server_id=_get("DATHOST_SERVER_ID", required=needs_dathost),
            a2s_host=_get("A2S_HOST", "127.0.0.1"),
            # Valheim answers Steam queries on game port + 1.
            a2s_port=_int("A2S_PORT", 2457),
            event_log_file=Path(p) if (p := _get("EVENT_LOG_FILE", required=event_source == "file")) else None,
            docker_container=_get("DOCKER_CONTAINER", required=event_source == "docker"),
            docker_socket=_get("DOCKER_SOCKET", "/var/run/docker.sock"),
            status_category_name=settings["category_name"],
            status_channel_id=_int("STATUS_CHANNEL_ID"),
            players_channel_id=_int("PLAYERS_CHANNEL_ID"),
            events_channel_id=_int("EVENTS_CHANNEL_ID"),
            status_poll_seconds=settings["status_poll_seconds"],
            event_poll_seconds=settings["event_poll_seconds"],
            channel_rename_seconds=settings["channel_rename_seconds"],
            state_file=Path(_get("STATE_FILE", "data/state.json")),
            config_file=config_file,
        )
