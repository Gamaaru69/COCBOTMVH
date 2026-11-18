import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SNAPSHOT_FILE = Path("logs/clan_snapshot.json")
INTERVAL_SECONDS = 30 * 60
PLAYER_DELAY_SECONDS = 1.0
MALDIVES_TZ = ZoneInfo("Indian/Maldives")
TELEGRAM_LIMIT = 3900


def _home_levels(items):
    return {
        item["name"]: item.get("level", 0)
        for item in (items or [])
        if item.get("village") == "home" and "name" in item
    }


def build_member_entry(row, player=None, previous=None):
    """Combine a members-list row with player details.
    If the player fetch failed, keep the previous unit levels so no false changes appear."""
    entry = {"name": row.get("name", "Unknown"), "th": row.get("townHallLevel", 0)}
    if player is not None:
        entry["heroes"] = _home_levels(player.get("heroes"))
        entry["troops"] = _home_levels(player.get("troops"))
        entry["spells"] = _home_levels(player.get("spells"))
    elif previous:
        for key in ("heroes", "troops", "spells"):
            if key in previous:
                entry[key] = previous[key]
    return entry


def diff_snapshots(old, new):
    """Return plain-text change lines between two snapshots. No baseline means no messages."""
    if old is None:
        return []

    old_m = old.get("members", {})
    new_m = new.get("members", {})
    lines = []

    for tag, m in new_m.items():
        if tag not in old_m:
            lines.append(f"{m['name']} joined (Town Hall {m['th']})")

    for tag, m in old_m.items():
        if tag not in new_m:
            lines.append(f"{m['name']} (Town Hall {m['th']}) left the clan")

    for tag, m in new_m.items():
        prev = old_m.get(tag)
        if prev is None:
            continue
        name = m["name"]
        if m["th"] > prev.get("th", 0):
            lines.append(f"{name} upgraded Town Hall to Level {m['th']}")

        for key in ("heroes", "troops", "spells"):
            if key not in prev or key not in m:
                continue
            for unit, level in m[key].items():
                before = prev[key].get(unit)
                if before is not None and level > before:
                    lines.append(f"{name} upgraded {unit} to Level {level}")
    return lines


def load_snapshot():
    if not SNAPSHOT_FILE.exists():
        return None
    try:
        return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_snapshot(snapshot):
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_FILE.with_name(SNAPSHOT_FILE.name + ".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SNAPSHOT_FILE)


def format_update(lines):
    now = datetime.now(MALDIVES_TZ).strftime("%d %b %Y %H:%M")
    return f"Clan Members Update ({now} MVT)\n\n" + "\n".join(lines)


def split_message(text, limit=TELEGRAM_LIMIT):
    """Split on line breaks so no line is cut in half."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


async def run_check(fetch, send, logger, clan_tag):
    rows = await fetch(f"clans/{clan_tag}/members")
    if not isinstance(rows, dict) or rows.get("_api_error") or "items" not in rows:
        logger.error("MONITOR_SKIPPED: could not fetch clan members; snapshot unchanged")
        return

    old = load_snapshot()
    previous = (old or {}).get("members", {})

    members = {}
    for row in rows["items"]:
        tag = row["tag"]
        player = await fetch(f"players/{tag.replace('#', '%23')}")
        if isinstance(player, dict) and player.get("_api_error"):
            player = None
        members[tag] = build_member_entry(row, player, previous.get(tag))
        await asyncio.sleep(PLAYER_DELAY_SECONDS)

    new = {"updated_at": datetime.now(timezone.utc).isoformat(), "members": members}
    lines = diff_snapshots(old, new)

    # If sending fails, the exception propagates and the snapshot is not saved,
    # so the same changes are retried on the next check.
    if lines:
        await send(format_update(lines))
    save_snapshot(new)
    logger.info(f"MONITOR_CHECK: {len(members)} members, {len(lines)} changes")


async def monitor_loop(fetch, send, logger, clan_tag):
    while True:
        try:
            await run_check(fetch, send, logger, clan_tag)
        except Exception as exc:
            logger.error(f"MONITOR_ERROR: {exc}")
        await asyncio.sleep(INTERVAL_SECONDS)