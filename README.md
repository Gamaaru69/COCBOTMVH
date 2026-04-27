# ClashBot

ClashBot is a Telegram bot focused on clan war status, war statistics, and war history logging.

## Commands

### General
- `/start`
- `/help`
- `/clan`
- `/events`

### War
- `/war`
- `/warstats`
- `/warlog`
- `/warhistory`

### Admin
- `/adminhelp`
- `/warlogupdate`
- `/addwarlogmanually <clan> <opponent> <result> <clan_stars> <opponent_stars> <clan_destruction> <opponent_destruction>`

## Requirements

- `BOT_TOKEN`
- `COC_TOKEN`
- `CLAN_TAG`

## Storage

- `logs/war_storage.json` keeps the latest saved war entries.
- `logs/War_History_Log_YYYYMMDD.log` stores formatted war history snapshots.
