# Valheim Discord Bot

A Discord bot for a Valheim server, built for servers hosted at [DatHost](https://dathost.com):

- **Sidebar status:** a category with voice channels nobody can join, renamed to show the server state,
  e.g. `🟢・Online: 3h 42m` and `👥・Ragnar, Lagertha`. The bot's presence shows the player count.
- **Event feed:** posts for deaths, players joining and leaving, raids starting, and the server going
  up or down.
- **`/announce`:** admins fill in a form and the bot posts it as itself, as a plain message or an embed card.
- **`/status`:** state, players, uptime, world and address, visible only to you.
- **`/start`, `/stop`, `/restart`:** admins control the server from Discord.
- **Configurable:** the server name, every text, and an on/off switch for each feature.

The bot polls the DatHost API for the server's status and reads its console log for events.

## 1. Create the Discord application

1. Go to <https://discord.com/developers/applications>, click **New Application** and name it.
2. **Bot** tab: click **Reset Token** and copy it. This is `DISCORD_TOKEN`, so treat it like a password.
   Turn **Public Bot** off if only you should be able to invite it. No privileged intents are needed.
3. **Installation** tab:
   - **Installation Contexts:** enable **Guild Install** (User Install isn't needed).
   - **Guild Install scopes:** `bot` and `applications.commands`.
   - **Permissions:** View Channels, Manage Channels, Connect, Send Messages, Embed Links, Attach Files,
     Mention Everyone.
4. Invite it with this URL, using the Application ID from the General Information tab:
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=1231888&integration_type=0
   ```
5. In Discord, turn on **User Settings → Advanced → Developer Mode**, then:
   - Right-click your server icon → **Copy Server ID**. This is `DISCORD_GUILD_ID`.
   - Right-click the text channel for events → **Copy Channel ID**. This is `EVENTS_CHANNEL_ID`.

## 2. Configure

```bash
cp .env.example .env
cp config.example.toml config.toml
```

- **`.env`** holds secrets and what differs per machine. Every setting is explained in
  [.env.example](.env.example).
- **`config.toml`** holds what the bot shows and does: display settings, timing, switches and texts.

Neither is committed, so pulling updates never overwrites them. If an update adds a new text or setting,
the bot names it at startup, and you copy it over from [config.example.toml](config.example.toml).

Fill in the Discord values from above and your DatHost login in `.env`:

```env
STATUS_SOURCE=dathost
EVENT_SOURCE=dathost
DATHOST_EMAIL=you@example.com
DATHOST_PASSWORD=...
DATHOST_SERVER_ID=...   # the ID in the control panel URL: dathost.com/control-panel/game-servers/<ID>
```

Use the login of the account that owns the server. If you sign in with Google, set a password first.

The bot reads the server's state, player names and last 200 console lines. It only sends commands when an
admin uses `/start`, `/stop` or `/restart`.

Then set the basics under `[display]` in `config.toml`:

```toml
[display]
server_name   = "My Valheim Server"
max_players   = 10
category_name = "Server Status"
```

## 3. Docker

```bash
mkdir -p data                 # create it as your user, otherwise Docker creates it root-owned
docker compose up -d --build  # build the image and start the bot in the background
```

If you update the code or .env, run the same `docker compose up -d --build`. 

If you update `config.toml` you only need to run `docker compose restart bot`. 

To check that the bot started, run
`docker compose logs -f bot` and look for `Synced 6 application commands` and `Logged in as ...`. Ctrl+C
only stops watching the log, not the bot.

The status category appears at the top of your channel list within 30 seconds. The bot comes back by itself
after reboots. Only one instance may run per bot token.

The bot stops at startup if it can't read or write `data/`. The folder must be writable by uid 1000, the
user the bot runs as in Docker.

The bot is developed and tested on Linux. With Docker it should also run on Windows and macOS, but that
hasn't been tested. On Windows, create the folder with `mkdir data`.

## Commands

By default, only members with *Manage Server* can use `/announce`, the edit action, `/start`, `/stop` and
`/restart`. Change that per command under **Server Settings → Integrations → your bot**.

- **`/announce [channel] [mention] [style] [image]`:** opens a form for an optional title and the message
  (Markdown works).
  - **Images:** PNG, JPG, GIF or WebP. With an image the message is optional. The bot needs *Attach Files*
    in that channel.
  - **Pings:** only the role you pick under `mention` gets pinged; @mentions in the text never ping.
- **Edit an announcement:** right-click the bot's message → **Apps → Edit announcement**. The image stays,
  but can't be swapped.
- **`/status`:** a status card only you can see.
- **`/start`, `/stop`, `/restart`:** control the server. Only available when the bot uses DatHost.
  - **Confirmation:** `/stop` and `/restart` ask first, since they kick everyone online.
  - **Restart:** the bot stops the server, checks every 5 seconds until it reports off, then starts it. If
    it hasn't stopped after 3 minutes, the bot tells you and doesn't start it.

## Customizing

Change `config.toml`, then run `docker compose restart bot`. A typo in a setting, `{placeholder}` or switch
stops the bot at startup with an error naming it.

- **Sidebar rows:** switching a row off deletes the channel the bot created for it; switching it back on
  recreates it.
- **Your own channels:** set their IDs in `STATUS_CHANNEL_ID` / `PLAYERS_CHANNEL_ID` and the bot only renames
  them, never deletes them.
- **Standard emojis:** paste them straight into the text.
- **Custom emojis:** `:name:` stays plain text, so use the full code. Type `\:name:` in your Discord server
  and send it to get the code:
  ```toml
  player_joined = "<:name:123456789012345678>・**{name}** has joined {server}"
  ```
  - They only work in `[events]` and `[replies]`, not in channel names or the presence.
  - The bot must be in the server that owns the emoji. If that isn't the one it posts in, it also needs
    **Use External Emojis**.
  - Re-uploading an emoji gives it a new ID, so update the code too.

## Good to know

- **The sidebar can lag by up to 5 minutes.** Discord allows only 2 renames per channel every 10 minutes.
  The presence, `/status` and the event feed update on every poll.
- **DatHost's player count can lag by 10-15 minutes.** The sidebar, presence and `/status` use that number.
  DatHost doesn't enforce a rate limit, but polling more often doesn't help. Join, leave and death posts
  aren't affected, since they come from the console log.

### Getting a faster player count

Query the server directly with Steam's query protocol (A2S) and keep DatHost for the log. In `.env`:

```env
STATUS_SOURCE=a2s
A2S_HOST=...   # the server's IP or domain from the DatHost panel
A2S_PORT=...   # the game port + 1
```

- **The server must be public.** A private server doesn't answer these queries, so the bot sees it as
  offline. A public server is listed in the server browser, but still needs its password to join. With
  `STATUS_SOURCE=dathost`, private servers work fine.
- **Crossplay may break the count.** With crossplay on, Valheim reportedly always answers 0 players.
- **Trade-off:** 3 missed answers in a row (about 90 seconds) count as offline, so a short network hiccup
  can post offline and online. To change it, edit `MISSES_FOR_OFFLINE` in
  [bot/sources/a2s_query.py](bot/sources/a2s_query.py) and rebuild.

## Development

Run the tests from the project root, after `pip install -r requirements-dev.txt` in a virtual environment:

```bash
.venv/bin/pytest
```

Running the bot without Docker (Python 3.11+) and testing against a local Valheim server are covered in
[dev/README.md](dev/README.md). Reading a local container's log with `EVENT_SOURCE=docker` needs Docker 23+.

| File | What it does |
|---|---|
| `bot/main.py` | Bot setup: picks the sources from config, loads the features, syncs slash commands |
| `bot/config.py` | Every `.env` setting, plus `[display]` and `[timing]` from `config.toml` |
| `bot/messages.py` | All texts and on/off switches from `config.toml`, checked at startup |
| `bot/events.py` | Log line → event (death, join, leave, raid) |
| `bot/state.py` | `data/state.json`: what the bot remembers across restarts |
| `bot/sources/` | Where status and logs come from: DatHost API, Steam query, Docker log, log file |
| `bot/cogs/` | The Discord features: status, events, announce, server control |
