from datetime import date, datetime
from zoneinfo import ZoneInfo

LAST_UPDATED = date(2026, 11, 17)

# Add events from the in-game event page.
# Each entry: name, start, end (inclusive), notes.
EVENTS = [
    # {"name": "Clan Games", "start": date(2026, 11, 22), "end": date(2026, 11, 28), "notes": ""},
]

MALDIVES_TZ = ZoneInfo("Indian/Maldives")

FYI = (
    "FYI: This information is manually entered by the Boss Admin and may not be "
    "completely accurate or up to date. If you notice any incorrect data, please "
    "inform the Boss Admin to update the bot."
)


def current_events(today=None):
    """Return events that have not yet ended, in Maldives time."""
    if today is None:
        today = datetime.now(MALDIVES_TZ).date()
    return sorted(
        (e for e in EVENTS if e["end"] >= today),
        key=lambda e: e["start"],
    )


def format_events(today=None):
    """Build the text that /events sends."""
    items = current_events(today)
    if not items:
        return (
            "No upcoming events listed. Check the in-game event page.\n\n"
            f"[Source: Manual] Last updated {LAST_UPDATED:%d %b %Y}\n{FYI}"
        )
    lines = ["📅 Current Events", ""]
    for e in items:
        dates = f"{e['start']:%d %b} – {e['end']:%d %b %Y}"
        line = f"• {e['name']}: {dates}"
        if e.get("notes"):
            line += f" ({e['notes']})"
        lines.append(line)
    lines += ["", f"[Source: Manual] Last updated {LAST_UPDATED:%d %b %Y}", FYI]
    return "\n".join(lines)