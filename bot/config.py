"""All configuration comes from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    messages_file: Path

    @classmethod
    def from_env(cls) -> Config:
        status_source = _choice("STATUS_SOURCE", "dathost", {"dathost", "a2s"})
        event_source = _choice("EVENT_SOURCE", "none", {"dathost", "docker", "file", "none"})

        needs_dathost = "dathost" in (status_source, event_source)
        config = cls(
            discord_token=_get("DISCORD_TOKEN", required=True),
            guild_id=_int("DISCORD_GUILD_ID", required=True),
            server_name=_get("SERVER_NAME", "Valheim"),
            max_players=_int("MAX_PLAYERS"),
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
            status_category_name=_get("STATUS_CATEGORY_NAME", "📊 Server Status"),
            status_channel_id=_int("STATUS_CHANNEL_ID"),
            players_channel_id=_int("PLAYERS_CHANNEL_ID"),
            events_channel_id=_int("EVENTS_CHANNEL_ID"),
            status_poll_seconds=_int("STATUS_POLL_SECONDS", 30),
            event_poll_seconds=_int("EVENT_POLL_SECONDS", 15),
            # Discord allows 2 renames per channel per 10 minutes; stay under it.
            channel_rename_seconds=_int("CHANNEL_RENAME_SECONDS", 300),
            state_file=Path(_get("STATE_FILE", "data/state.json")),
            messages_file=Path(_get("MESSAGES_FILE", "messages.toml")),
        )
        if config.channel_rename_seconds < 300:
            raise ConfigError("CHANNEL_RENAME_SECONDS below 300 will hit Discord's rename rate limit")
        return config
