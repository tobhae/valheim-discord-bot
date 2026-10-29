# Valheim Discord Bot

A Discord bot for one Valheim server, built for servers hosted at [DatHost](https://dathost.com):

- **Sidebar status:** a category with voice channels nobody can join, renamed to show the server state,
  e.g. `🟢 My Server · up 3h 42m` and `👥 Ragnar, Lagertha`. The bot's presence shows the player count.
- **Event feed:** messages for deaths, players joining and leaving, raids starting, and the server
  going up or down.
- **`/announce`:** admins fill in a form and the bot posts it as itself, as a plain message or as an embed
  card. Right-click → **Apps → Edit announcement** edits it afterwards.
- **`/status`:** state, players, uptime, world and address, visible only to you.
- **Configurable:** every text, plus an on/off switch for each feature, in [messages.toml](messages.toml).

## How it works

```
             STATUS_SOURCE                           EVENT_SOURCE
  ┌───────────────────────────────┐    ┌─────────────────────────────────────┐
  │ dathost: GET /game-servers/id │    │ dathost: GET .../console (polled)   │
  │          GET .../metrics      │    │ docker:  a container's log (local)  │
  │ a2s:     Steam query (local)  │    │ file:    tail a log file (testing)  │
  └───────────────┬───────────────┘    └──────────────────┬──────────────────┘
                  ▼                                       ▼
   cogs/status.py: sidebar rows, presence,   cogs/events.py: parse log lines →
   uptime, server up/down posts              deaths, joins/leaves, raids
```

| File | What it does |
|---|---|
| `bot/main.py` | Bot setup: picks the sources from config, loads the features, syncs slash commands |
| `bot/config.py` | Every `.env` setting in one place |
| `bot/messages.py` + `messages.toml` | All texts and on/off switches, checked at startup |
| `bot/events.py` | Log line → event (death, join, leave, raid) |
| `bot/state.py` | `data/state.json`: what the bot remembers across restarts |
| `bot/sources/` | DatHost API, Steam query, Docker log, log file |
| `bot/cogs/` | The Discord features: status, events, announce |

Running and testing it locally, including a local Valheim server: [dev/README.md](dev/README.md).

## 1. Create the Discord application

1. Go to <https://discord.com/developers/applications>, click **New Application** and name it.
2. **Bot** tab: click **Reset Token** and copy it. This is `DISCORD_TOKEN`, so treat it like a password.
   Turn **Public Bot** off if only you should be able to invite it. No privileged intents are needed.
3. **Installation** tab:
   - **Installation Contexts:** enable **Guild Install** (User Install isn't needed).
   - **Guild Install scopes:** `bot` and `applications.commands`.
   - **Permissions:** View Channels, Manage Channels, Connect, Send Messages, Embed Links, Mention Everyone.
4. Invite it with this URL, using the Application ID from the General Information tab:
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=1199120&integration_type=0
   ```
   Pick your server and authorize. The bot then shows up in the member list.
5. In Discord, turn on **User Settings → Advanced → Developer Mode**, then:
   - Right-click your server icon → **Copy Server ID**. This is `DISCORD_GUILD_ID`.
   - Right-click the text channel for events → **Copy Channel ID**. This is `EVENTS_CHANNEL_ID`.

## 2. Configure

```bash
cp .env.example .env
```

Fill in the Discord values from above, and your DatHost login:

```env
STATUS_SOURCE=dathost
EVENT_SOURCE=dathost
DATHOST_EMAIL=you@example.com
DATHOST_PASSWORD=...
DATHOST_SERVER_ID=...   # the ID in the control panel URL: dathost.com/control-panel/game-servers/<ID>
```

The DatHost API uses your account email and password. If you sign in with Google, set a password first.
Consider a dedicated DatHost sub-account with access to only this server. The bot only reads
`/game-servers/{id}` (state, player count, address), `/metrics` (player names) and `/console` (the last
200 log lines). It never sends commands to the server.

Every other setting is explained in [.env.example](.env.example).

## 3. Run it with Docker

```bash
mkdir -p data                  # create it as your user, otherwise Docker creates it root-owned
docker compose up -d --build   # build the image and start the bot in the background
```

`--build` builds the image from the `Dockerfile`, which is needed the first time and after updating the
code. `-d` runs the bot in the background, so it keeps running when you close the terminal.

To check that it started, watch its output with `docker compose logs -f bot` and look for
`Synced 3 application commands` and `Logged in as ...`. Ctrl+C there only stops watching, not the bot.

Within 30 seconds the status category appears at the top of your channel list. `restart: unless-stopped`
brings the bot back after reboots. Only one instance may run per bot token.

Without Docker, it runs on Python 3.11+: `pip install -r requirements.txt`, then `python -m bot`.
See [dev/README.md](dev/README.md).

### Platform

The bot is developed and tested on Linux. With Docker it should also run on Windows and macOS,
since the bot runs in a Linux container under Docker Desktop, but that hasn't been tested.

Differences you may run into on Windows:

- Create the data folder with `mkdir data` (`-p` isn't needed).
- Running without Docker, the virtual environment's Python is `.venv\Scripts\python`
  instead of `.venv/bin/python`.

Local testing has a few more differences, listed in [dev/README.md](dev/README.md#on-windows).


## Usage

- **Texts and switches:** change any text in [messages.toml](messages.toml), or turn features off under
  `[switches]`, then restart (`docker compose restart bot`). A typo in a `{placeholder}` or switch
  stops the bot at startup with an error naming the line.
  - **Rows:** switching a row off deletes the channel the bot created for it; switching it back on
    recreates it.
  - **Your own channels:** set their IDs in `STATUS_CHANNEL_ID` / `PLAYERS_CHANNEL_ID` and the bot
    only renames them, never deletes them.
- **`/announce [channel] [mention] [style]`:** opens a form for an optional title and the message
  (Markdown works).
  - **Pings:** only the role you pick under `mention` gets pinged; @mentions inside the text never ping.
  - **Who can use it:** by default only members with *Manage Server* see the command. Change that under
    **Server Settings → Integrations → your bot**.
- **Edit an announcement:** right-click the bot's message → **Apps → Edit announcement**.
- **`/status`:** a status card only you can see.

**Sidebar delay:** Discord allows only 2 renames per channel every 10 minutes, so the sidebar can lag by
up to 5 minutes. The presence, `/status` and the event feed update on every poll. The bot remembers its
last rename in `data/state.json`, so restarts don't run into the limit.
