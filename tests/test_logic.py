import asyncio
import json
from pathlib import Path

import pytest

from bot.cogs.status import format_uptime, players_channel_name, status_channel_name, template_values
from bot.config import ConfigError
from bot.events import EventParser, PlayerDied, PlayerJoined, PlayerLeft, RaidStarted
from bot.messages import Messages
from bot.models import ServerState, ServerStatus
from bot.sources.dathost import new_lines_since
from bot.sources.logs import FileLogSource

# Your real messages.toml must always load; the other tests use their own
# templates below so editing your texts never breaks them.
REAL_MESSAGES = Path(__file__).parent.parent / "messages.toml"

TEST_TEMPLATES = {
    "status_channel": {
        "online": "🟢 {server}: {players}/{max_players} online · up {uptime}",
        "starting": "🟡 {server}", "offline": "🔴 {server}: offline", "unknown": "⚪ {server}",
    },
    "players_channel": {"names": "👥 {names}", "count": "👥 {players} online", "empty": "👥 nobody"},
    "presence": {"online": "{players} online", "offline": "{state}"},
    "events": {
        name: "x" for name in
        ("server_online", "server_offline", "player_joined", "player_left", "death", "raid", "raid_unknown")
    },
}


def _toml(templates, switches=""):
    lines = [f"[switches]\n{switches}"]
    for section, entries in templates.items():
        lines.append(f"[{section}]")
        lines += [f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in entries.items()]
    return "\n".join(lines) + "\n"


def _load(tmp_path, switches="", **overrides):
    """Load the test templates; overrides look like events={"raid": ""}."""
    templates = {section: {**entries, **overrides.get(section, {})} for section, entries in TEST_TEMPLATES.items()}
    path = tmp_path / "messages.toml"
    path.write_text(_toml(templates, switches), encoding="utf-8")
    return Messages.load(path)


def test_real_messages_file_loads():
    Messages.load(REAL_MESSAGES)


def test_deaths_and_duplicates():
    parser = EventParser()
    line = "10/27/2026 21:14:03: Got character ZDOID from Ragnar the Red : 0:0"
    assert parser.parse(line, now=0) == PlayerDied("Ragnar the Red")  # names may contain spaces
    assert parser.parse(line, now=1) is None  # Valheim sometimes logs a death twice
    assert parser.parse(line, now=60) == PlayerDied("Ragnar the Red")


def test_raids_and_duplicates():
    # Real line from a single-player test session.
    parser = EventParser()
    line = "10/28/2026 17:47:52: Random event set:army_eikthyr"
    assert parser.parse(line, now=0) == RaidStarted("army_eikthyr")
    assert parser.parse(line, now=30) is None  # the same raid
    assert parser.parse("Random event set:wolves", now=30) == RaidStarted("wolves")
    assert parser.parse(line, now=3000) == RaidStarted("army_eikthyr")
    assert parser.parse("10/28/2026 17:47:52: Spawned Neck x 1", now=3001) is None  # raid spawns aren't events


def test_join_respawn_and_leave():
    # Real lines from the local test server (date prefixes shortened).
    parser = EventParser()
    lines = [
        ("17:30:20: Got character ZDOID from Test : 1078420627:1", PlayerJoined("Test")),
        ("17:30:40: Got character ZDOID from Test : 0:0", PlayerDied("Test")),
        ("17:30:41: Got character ZDOID from Test : 1078420627:146", None),  # respawn
        ("17:30:59: RPC_Disconnect", None),
        ("17:30:59: Destroying abandoned non persistent zdo 1078420627:36 owner 1078420627", PlayerLeft("Test")),
        ("17:30:59: Destroying abandoned non persistent zdo 1078420627:1 owner 1078420627", None),
        # New session; session ids can also be negative.
        ("17:40:00: Got character ZDOID from Test : -1234567:1", PlayerJoined("Test")),
        ("17:50:00: Destroying abandoned non persistent zdo -1234567:1 owner -1234567", PlayerLeft("Test")),
    ]
    for t, (line, expected) in enumerate(lines):
        assert parser.parse(line, now=t * 10) == expected, line


def test_respawn_of_player_online_before_bot_started():
    parser = EventParser()
    assert parser.parse("Got character ZDOID from Test : 0:0", now=0) == PlayerDied("Test")
    assert parser.parse("Got character ZDOID from Test : 555:81", now=2) is None
    # ...but the bot now knows the session, so the leave is still reported.
    assert parser.parse("Destroying abandoned non persistent zdo 555:1 owner 555", now=60) == PlayerLeft("Test")


def test_server_shutdown_forgets_players():
    parser = EventParser()
    assert parser.parse("16:29:49: Got character ZDOID from Test : 2828846559:1", now=0) == PlayerJoined("Test")
    assert parser.parse("16:41:00: ZNet Shutdown", now=10) is None
    # No leave line comes after a shutdown; the next session is a fresh join.
    assert parser.parse("16:42:02: Got character ZDOID from Test : 2606718819:1", now=200) == PlayerJoined("Test")
    assert parser.parse("Destroying abandoned non persistent zdo 2828846559:1 owner 2828846559", now=210) is None


def test_console_window_overlap():
    assert new_lines_since(["a", "b", "c"], ["b", "c", "d", "e"]) == ["d", "e"]
    assert new_lines_since(["a", "b"], ["a", "b"]) == []
    assert new_lines_since(["a", "b"], ["x", "y"]) == ["x", "y"]
    assert new_lines_since([], ["x"]) == ["x"]


def test_file_source(tmp_path):
    path = tmp_path / "valheim.log"
    path.write_text("Got character ZDOID from Test : 5:1\nsecond line\n")
    source = FileLogSource(path)
    assert asyncio.run(source.read_backlog()) == ["Got character ZDOID from Test : 5:1", "second line"]
    assert asyncio.run(source.read_new_lines()) == []  # the backlog isn't repeated as new
    with path.open("a") as f:
        f.write("new 1\nnew 2\npartial")
    assert asyncio.run(source.read_new_lines()) == ["new 1", "new 2"]
    with path.open("a") as f:
        f.write(" done\n")
    assert asyncio.run(source.read_new_lines()) == ["partial done"]  # waits for the line to finish


def test_channel_names(tmp_path):
    messages = _load(tmp_path)

    def names(status, uptime=None):
        values = template_values("My Server", status, uptime)
        return status_channel_name(messages, values, status), players_channel_name(messages, values, status)

    online = ServerStatus(ServerState.ONLINE, players=2, max_players=6, player_names=("Ragnar", "Lagertha"))
    assert names(online, uptime=3 * 3600 + 42 * 60) == ("🟢 My Server: 2/6 online · up 3h 42m", "👥 Ragnar, Lagertha")
    offline = ServerStatus(ServerState.OFFLINE, max_players=6)
    assert names(offline) == ("🔴 My Server: offline", "👥 nobody")
    unnamed = ServerStatus(ServerState.ONLINE, players=1, max_players=6)
    assert names(unnamed)[1] == "👥 1 online"


def test_format_uptime():
    assert format_uptime(59) == "0m"
    assert format_uptime(42 * 60) == "42m"
    assert format_uptime(3 * 3600 + 42 * 60 + 30) == "3h 42m"
    assert format_uptime(23 * 3600 + 59 * 60) == "23h 59m"
    assert format_uptime(26 * 3600 + 5 * 60) == "1d 2h"


def test_unknown_placeholder_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"\{plyers\}"):
        _load(tmp_path, presence={"online": "{plyers} online"})


def test_switches(tmp_path):
    messages = _load(tmp_path, switches="death = false")
    assert not messages.enabled("death")
    assert messages.enabled("raid")  # missing switches count as on


def test_switch_typo_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown switch 'deth'"):
        _load(tmp_path, switches="deth = false")


def test_switch_must_be_true_or_false(tmp_path):
    with pytest.raises(ConfigError, match="true or false"):
        _load(tmp_path, switches='death = "no"')


def test_empty_text_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="can't be empty"):
        _load(tmp_path, events={"raid": ""})


def test_player_row_count_instead_of_names(tmp_path):
    messages = _load(tmp_path)
    status = ServerStatus(ServerState.ONLINE, players=2, max_players=6, player_names=("Ragnar", "Lagertha"))
    values = template_values("My Server", status, None)
    assert players_channel_name(messages, values, status, show_names=True) == "👥 Ragnar, Lagertha"
    assert players_channel_name(messages, values, status, show_names=False) == "👥 2 online"


def test_backlog_replay_knows_who_is_online():
    parser = EventParser()
    for line in [
        "16:23:15: Valheim version: l-1.0.21 (network version 41)",
        "16:29:49: Got character ZDOID from Ragnar : 2828846559:1",
        "16:41:23: Valheim version: l-1.0.21 (network version 41)",  # restart, Ragnar gone
        "17:42:48: Got character ZDOID from astrid : 3135434672:1",
        "17:43:00: Got character ZDOID from Bjorn : 1078420627:1",
        "17:44:00: Got character ZDOID from Test : 555:1",
        "17:45:00: Destroying abandoned non persistent zdo 555:1 owner 555",  # Test left
    ]:
        parser.parse(line, now=0)
    parser.forget_timings()
    assert parser.online_names() == ("astrid", "Bjorn")  # sorted, ignoring case


def test_docker_timestamps():
    from bot.sources.docker_logs import parse_timestamp, split_timestamp
    a = parse_timestamp("2026-10-28T17:42:48.12345Z")  # Docker drops trailing zeros
    b = parse_timestamp("2026-10-28T17:42:48.123450001Z")
    assert a == 1793209368_123450000 and b == a + 1
    assert split_timestamp("2026-10-28T17:42:48Z hello world") == (1793209368_000000000, "hello world")
    assert split_timestamp("no timestamp here") is None


def test_state_skips_unchanged_writes(tmp_path):
    from bot.state import State
    path = tmp_path / "state.json"
    state = State(path)
    state.set("status_category_id", None)
    path.write_text('{"status_category_id": null, "marker": 1}')  # a real write would drop the marker
    state.set("status_category_id", None)
    assert "marker" in path.read_text()  # unchanged value: not written again
    state.set("rename_after", {"1": 5.0})
    assert State(path).get("rename_after") == {"1": 5.0}
