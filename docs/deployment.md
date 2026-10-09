# Deployment

memedexer runs as a single container with a SQLite database on a Docker
volume. The image is built from [.docker/Dockerfile](../.docker/Dockerfile)
and run with [docker-compose.yaml](../docker-compose.yaml).

## 1. Create the bot
1. In [@BotFather](https://t.me/BotFather): `/newbot`, keep the token.
2. `/setprivacy` → your bot → **Disable**. With privacy on, the bot never
   sees ordinary group messages. (Making it a group admin also works.)
3. From the account that will own the bot, open the bot and send `/start`.
   Telegram doesn't let bots DM someone first, and approval requests arrive
   by DM.
4. Find that account's numeric user id (e.g. via
   [@userinfobot](https://t.me/userinfobot)).

## 2. Configure
```bash
cp .env.example .env
```
Fill in at least:
- `TELEGRAM_BOT_TOKEN`
- `OWNER_USER_ID`
- `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`

Every other variable falls back to its default when left empty. The defaults
and meanings live in [config.py](../src/memedexer/config.py). Compose pins
`DATABASE_URL` to the volume, so the value in `.env` is ignored there.

## 3. Run
```bash
docker compose up -d --build
docker compose logs -f bot
```
On startup the bot migrates the database, registers its command menus, and
starts polling. It logs a warning if it can't reach the owner (step 1.3).

Updating to a newer version is the same command after `git pull`;
migrations run automatically on start.

## 4. Use
1. Add the bot to a group. The owner gets a DM with **Approve** / **Reject**.
2. After approval, an admin sends `/captions on` in each topic (or once, in
   a group without topics) where memes should be captioned.
3. Optional: `/model …`, `/language …`, `/settings` — see the
   [command list](../src/memedexer/telegram/README.md#group-commands-adminpy).

The owner manages chats with `/chats` in the bot's DM.

## Operations
- **Restarts:** the service restarts unless stopped. A fatal error, such as
  an invalid token or a dead worker, exits the process so Docker restarts it,
  backing off between restarts; check `docker compose logs bot`.
- **Logs** are capped at 3 × 10 MB by the json-file driver.
- **Backup:** the whole state is one SQLite file on the `data` volume. Use
  SQLite's online backup so a running bot doesn't produce a torn copy:
  ```bash
  docker compose exec bot python -c "import sqlite3; sqlite3.connect('/data/memedexer.db').backup(sqlite3.connect('/data/backup.db'))"
  docker compose cp bot:/data/backup.db ./memedexer-backup.db
  ```
- **Costs:** per-chat limits come from `COST_LIMIT_*_USD`; the owner gets a
  DM when a chat reaches one, and `/settings` shows current spend.
