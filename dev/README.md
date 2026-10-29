# Development

How to run and test the bot on your own machine, including a local Valheim server.

***Note**: The commands below assume Linux. On Windows, see [On Windows](#on-windows) at the end.*

Everything below runs from the repository root. The local-testing files live in this folder:

| File | What it is |
|---|---|
| `dev/compose.valheim.yml` | A local Valheim dedicated server ([lloesche/valheim-server](https://github.com/lloesche/valheim-docker)) |
| `dev/compose.dev.yml` | Extra settings to run the bot in Docker against that server |
| `dev/devserver/` | The test server's world and config (created on first start, not committed) |
| `dev/devlogs/` | Log files for `EVENT_SOURCE=file` (not committed) |

***Tip**: Use a private test Discord server, so your real one stays clean.*

## Run the bot directly (Python 3.11+)

```bash
cp .env.example .env # DISCORD_TOKEN, DISCORD_GUILD_ID, EVENTS_CHANNEL_ID
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest # parsing, templates, state (short output, use -v for one line per test)
.venv/bin/python -m bot
```

You should see `Synced 3 application commands` (`/status`, `/announce`, *Edit announcement*) and
`Logged in as ...`. Within one poll, the status category appears at the top of your channel list.
The bot reads `.env` itself, so no `source .env` is needed.

After changing code, `.env` or `messages.toml`: Ctrl+C and start it again. Set `LOG_LEVEL=DEBUG` when
something silently doesn't happen; the sources log their failures at debug level.

## A local Valheim server

```bash
sudo systemctl start docker
docker compose -f dev/compose.valheim.yml up -d           # first boot: several minutes, ~2 GB download
docker compose -f dev/compose.valheim.yml logs -f valheim # wait for "Game server connected"
```

Point the bot at it in `.env`:

```env
STATUS_SOURCE=a2s       # Steam query on 127.0.0.1:2457
EVENT_SOURCE=docker     # reads the container's log straight from Docker
```

At startup the bot reads the recent log without posting, so it already knows who is online. Look for
`Read 1000 earlier log lines; online now: ...`.

**Joining:** Join game → Add server → `127.0.0.1:2456`, password `devpass123`. You'll get a join post
within ~15 s, and the sidebar updates within 5 minutes.

Things that tripped us up:
- **The server runs as public** (`SERVER_PUBLIC=true`). A private Valheim server ignores Steam status
  queries, which leaves `STATUS_SOURCE=a2s` showing "offline" forever. It's still password protected.
- **Join with a vanilla client:** start Valheim from Steam, not from a mod manager. Mods that check the server
  (e.g. anything using ConditionalConfigSync or ServerSync) disconnect you right after joining, because
  the test server doesn't run them.
- **No player names over A2S:** Valheim doesn't report names that way. The bot takes them from the log
  instead, as long as it has seen everyone join.

Stop the server with `docker compose -f dev/compose.valheim.yml down`. If Docker ever says
`network ... not found`, add `--force-recreate` to the `up` command. The world in `dev/devserver/` is kept.

### Admin commands and raids

Admins are listed by Steam ID in `dev/devserver/config/adminlist.txt`. Your ID appears in the server log
as `Got connection SteamID <id>` when you join. The file belongs to root (Docker created it), so add
yourself from inside the container:

```bash
docker compose -f dev/compose.valheim.yml exec valheim sh -c 'echo <your steam id> >> /config/adminlist.txt'
```

Relog, open the console with F5 (add `-console` to Valheim's Steam launch options if needed), and
run `devcommands`. On a dedicated server, only server-side commands work, e.g. `event army_eikthyr` to
start a raid and `stopevent` to end it. `god` and `debugmode` don't.

To see what a raid looks like in the log without any of that, play single player with `devcommands`;
the game writes the same server lines to `~/.config/unity3d/IronGate/Valheim/Player.log`.

## Fake events without a Valheim server

Set `EVENT_SOURCE=file` (it tails `dev/devlogs/valheim.log`), start the bot, then append log lines:

```bash
mkdir -p dev/devlogs
echo "10/27/2026 21:14:03: Got character ZDOID from Ragnar : 0:0" >> dev/devlogs/valheim.log   # death
echo "10/28/2026 17:47:52: Random event set:army_eikthyr" >> dev/devlogs/valheim.log           # raid
```

## Run the bot in Docker locally

```bash
getent group docker            # note the number (third field), put it in .env as DOCKER_GID=...
mkdir -p data                  # create it as your user, otherwise Docker creates it root-owned
docker compose -f compose.yml -f dev/compose.dev.yml up -d --build
docker compose logs -f bot
```

`dev/compose.dev.yml` switches the bot to `a2s` + `docker`, and gives the container Docker's socket
(to read the Valheim log) and `host.docker.internal` (to reach the server's query port). Access to the
socket is root-equivalent, which is fine for testing but why it isn't part of `compose.yml`.

What needs what after a change:

| You changed | Running directly | Running in Docker |
|---|---|---|
| `.env` | restart the bot | `docker compose ... up -d` (a plain `restart` keeps the old settings) |
| `messages.toml` | restart the bot | `docker compose restart bot` |
| Python code | restart the bot | `docker compose ... up -d --build` |

## On Windows

Not tested, but these are the known differences:

- **Docker:** start Docker Desktop instead of `sudo systemctl start docker`. The Valheim test server
  (`dev/compose.valheim.yml`) runs as a Linux container and should work as is.
- **Python:** use `.venv\Scripts\python` and `.venv\Scripts\pip` instead of `.venv/bin/...`.
- **`EVENT_SOURCE=docker` with the bot running directly doesn't work.** Docker on Windows uses a named pipe,
  and the bot only supports Docker's Unix socket. Run the bot in Docker instead
  (`dev/compose.dev.yml`), or use `EVENT_SOURCE=file` to fake events.
- **`dev/compose.dev.yml`:** `getent group docker` and `DOCKER_GID` are Linux-only. Docker Desktop
  handles access to the socket differently, so the `group_add` part may need changing.
- **Fake events:** the `echo ... >> dev/devlogs/valheim.log` lines work in Git Bash. In PowerShell, use
  `Add-Content dev/devlogs/valheim.log "<line>"` so the file stays UTF-8.
- **Single-player log** (for checking raid lines):
  `%USERPROFILE%\AppData\LocalLow\IronGate\Valheim\Player.log`.


## Discord limits to know about

- **Renames:** 2 per channel per 10 minutes. The bot renames at most every 5 minutes and remembers
  the last rename across restarts.
- **Creating channels:** Discord limits how many channels a server can create in a while, shared by
  everyone who changes the server. After lots of deleting and recreating, it can refuse for hours.
  The bot then logs when it will try again and waits. To get a row back sooner, create a voice
  channel yourself and put its ID in `STATUS_CHANNEL_ID` / `PLAYERS_CHANNEL_ID`. Avoid flipping
  the row switches back and forth: each flip deletes or creates a channel.
