# Testing Guide

## Basic Checks

1. Start the bot and run `/help`.
2. Run `/clan` and confirm clan data loads.
3. Run `/war` and `/warstats` during an active war.
4. Run `/warlog` and `/warhistory` after saving at least one war.

## Admin Checks

1. Run `/adminhelp` as a Telegram group admin.
2. Run `/warlogupdate` after a war ends.
3. Run `/addwarlogmanually` with a sample entry and confirm it appears in `/warhistory`.
