import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


MANUAL_EVENTS = """Current Events / Announcements

DRAGON ESCAPE SEASON - March 1-31

March 1-31: Gold Pass
Boost your village with bonus perks, items, and unlock the Dragonscale Prince Hero Skin.

March 1-7: Dragon Duke Unleashed
Use this Hero and his Equipment at their max power allowed by your Town Hall level.

March 1-31: Dragon Duke Challenge Levels
Play challenge levels in three batches throughout the month and unlock rewards.

March 3-31: Dragon troop events
Expect rotating Dragon troop boosts throughout the season.

March 3-31: Dragon Escape Scenery
Seasonal scenery available during the event window.

March 5-31: Dragonscale Hero Skins
Seasonal Hero skins available during the event window.

March 7-14: Dragon Dash Medal Event
Earn event currency and unlock extra rewards.

March 13-20: Clan Rush
Collect Gold with your clan and complete the reward track.

March 22-28: Clan Games - Dragon Edition
Dragon-related tasks appear more often during Clan Games."""


PROCESSING_MESSAGES = [
    "Fetching the latest war data...",
    "Checking the clan battlefield...",
    "Scanning the enemy base...",
    "Collecting the latest clan report...",
]


# Optional local development support. Railway provides env vars directly.
if load_dotenv and Path(".env").exists():
    load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
COC_TOKEN = os.getenv("COC_TOKEN")
RAW_CLAN_TAG = os.getenv("CLAN_TAG")
CLAN_TAG = RAW_CLAN_TAG.replace("#", "%23") if RAW_CLAN_TAG else None
RAW_ADMIN_IDS = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = {int(user_id.strip()) for user_id in RAW_ADMIN_IDS.split(",") if user_id.strip().isdigit()}
RAW_GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "").strip()
GROUP_CHAT_ID = int(RAW_GROUP_CHAT_ID) if RAW_GROUP_CHAT_ID.lstrip("-").isdigit() else None

STORAGE_FILE = "logs/war_storage.json"
RECENT_WAR_GRACE_PERIOD = timedelta(hours=2)


def setup_logging():
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("ClashBot")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    log_file = os.path.join(log_dir, f"ClashBot_Logs_{datetime.now().strftime('%Y%m%d')}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    error_file = os.path.join(log_dir, f"ClashBot_Error_Sheet_{datetime.now().strftime('%Y%m%d')}.log")
    error_handler = logging.FileHandler(error_file)
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    return logger


logger = setup_logging()


def validate_configuration():
    missing = [
        name
        for name, value in (
            ("BOT_TOKEN", BOT_TOKEN),
            ("COC_TOKEN", COC_TOKEN),
            ("CLAN_TAG", RAW_CLAN_TAG),
        )
        if not value
    ]

    if missing:
        missing_list = ", ".join(missing)
        logger.error(f"CONFIG_ERROR: Missing required environment variables: {missing_list}")
        raise RuntimeError(f"Missing required environment variables: {missing_list}")


def ensure_storage_exists():
    storage_path = Path(STORAGE_FILE)
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    if not storage_path.exists():
        with open(storage_path, "w", encoding="utf-8") as file:
            json.dump({"warLogs": []}, file, indent=2)

    return storage_path


def load_storage():
    path = ensure_storage_exists()
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {"warLogs": []}


def save_storage(data):
    path = ensure_storage_exists()
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    logger.info(f"STORAGE_SAVED: {path}")


def add_war_log(war_data):
    storage = load_storage()
    storage["warLogs"].insert(
        0,
        {
            **war_data,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    storage["warLogs"] = storage["warLogs"][:5]
    save_storage(storage)
    return True


def get_war_logs():
    return load_storage().get("warLogs", [])


async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False

    user_id = user.id
    if user_id in ADMIN_IDS:
        return True

    if not update.message or not update.message.chat:
        return False

    chat_type = update.message.chat.type
    if chat_type == "private":
        if GROUP_CHAT_ID is None:
            return False
        try:
            admins = await context.bot.get_chat_administrators(GROUP_CHAT_ID)
            return any(admin.user.id == user_id for admin in admins)
        except Exception as exc:
            logger.error(f"ADMIN_CHECK_ERROR (private fallback): {exc}")
            return user_id in ADMIN_IDS

    try:
        admins = await context.bot.get_chat_administrators(update.message.chat.id)
        return any(admin.user.id == user_id for admin in admins)
    except Exception as exc:
        logger.error(f"ADMIN_CHECK_ERROR: {exc}")
        return user_id in ADMIN_IDS


def get_random_processing_message():
    return random.choice(PROCESSING_MESSAGES)


def coc_api(endpoint: str):
    url = f"https://api.clashofclans.com/v1/{endpoint}"
    headers = {"Authorization": f"Bearer {COC_TOKEN}"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            logger.info(f"API_SUCCESS: {endpoint}")
            return response.json()

        if response.status_code == 403:
            logger.error(f"COC_API_403: {endpoint} - {response.text}")
        else:
            logger.error(f"API_ERROR: {endpoint} - Status {response.status_code} - {response.text}")
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = {"message": response.text}

        if response.status_code == 403 and error_payload.get("reason") == "accessDenied.invalidIp":
            logger.error("COC_API_IP_WHITELIST_REQUIRED: Add Railway public IP to Clash API key whitelist.")

        return {
            "_api_error": {
                "status_code": response.status_code,
                "reason": error_payload.get("reason", "unknownError"),
                "message": error_payload.get("message", "Unknown API error."),
                "endpoint": endpoint,
            }
        }
    except requests.exceptions.Timeout:
        logger.error(f"API_TIMEOUT: {endpoint}")
        return {
            "_api_error": {
                "status_code": 408,
                "reason": "timeout",
                "message": "The Clash of Clans API request timed out.",
                "endpoint": endpoint,
            }
        }
    except Exception as exc:
        logger.error(f"API_EXCEPTION: {endpoint} - {exc}")
        return {
            "_api_error": {
                "status_code": 500,
                "reason": "requestException",
                "message": str(exc),
                "endpoint": endpoint,
            }
        }


async def coc_api_async(endpoint: str):
    return await asyncio.to_thread(coc_api, endpoint)


async def fetch_current_war_async():
    return await coc_api_async(f"clans/{CLAN_TAG}/currentwar")


async def fetch_league_group_async():
    return await coc_api_async(f"clans/{CLAN_TAG}/currentwar/leaguegroup")


async def fetch_league_war_by_tag_async(war_tag: str):
    encoded_war_tag = war_tag.replace("#", "%23")
    return await coc_api_async(f"clanwarleagues/wars/{encoded_war_tag}")


def get_api_error(data: dict | None):
    if isinstance(data, dict):
        return data.get("_api_error")
    return None


def get_war_attack_capacity(war_data: dict):
    team_size = war_data.get("teamSize", 0)
    attacks_per_member = war_data.get("attacksPerMember")

    if attacks_per_member is None:
        clan_attacks = war_data.get("clan", {}).get("attacks", 0)
        opponent_attacks = war_data.get("opponent", {}).get("attacks", 0)
        attacks_per_member = 2 if max(clan_attacks, opponent_attacks) > team_size else 1

    return team_size * attacks_per_member, attacks_per_member


def get_attack_breakdown(side_data: dict):
    members = side_data.get("members", [])
    attacks = []

    for member in members:
        attacks.extend(member.get("attacks", []))

    total_attacks = side_data.get("attacks")
    if total_attacks is None:
        total_attacks = len(attacks)

    three_star_attacks = sum(1 for attack in attacks if attack.get("stars", 0) == 3)
    return total_attacks, three_star_attacks


def get_war_result(clan_data: dict, opponent_data: dict):
    clan_stars = clan_data.get("stars", 0)
    opponent_stars = opponent_data.get("stars", 0)
    clan_destruction = clan_data.get("destructionPercentage", 0)
    opponent_destruction = opponent_data.get("destructionPercentage", 0)

    if clan_stars > opponent_stars:
        return "WIN"
    if clan_stars < opponent_stars:
        return "LOSS"
    if clan_destruction > opponent_destruction:
        return "WIN"
    if clan_destruction < opponent_destruction:
        return "LOSS"
    return "DRAW"


def get_war_status_indicator(clan_data: dict, opponent_data: dict):
    clan_stars = clan_data.get("stars", 0)
    opponent_stars = opponent_data.get("stars", 0)
    clan_destruction = clan_data.get("destructionPercentage", 0)
    opponent_destruction = opponent_data.get("destructionPercentage", 0)

    if clan_stars > opponent_stars:
        return "📈 WINNING"
    if clan_stars < opponent_stars:
        return "📉 LOSING"
    if clan_destruction > opponent_destruction:
        return "📈 WINNING (Destruction Advantage)"
    if clan_destruction < opponent_destruction:
        return "📉 LOSING (Destruction Disadvantage)"
    return "⚖️ TIED"


def build_api_error_message(data: dict | None):
    error = get_api_error(data)
    if not error:
        return None

    if error.get("reason") == "accessDenied.invalidIp":
        details = error.get("message", "")
        current_ip = "unknown"
        if "IP " in details:
            current_ip = details.split("IP ", 1)[1].strip()
        return (
            "Clash of Clans API access is blocked for this bot right now.\n"
            f"Current public IP: {current_ip}\n"
            "Add this IP to the API key whitelist in the Clash of Clans developer dashboard, "
            "then try the war command again."
        )

    return (
        "Unable to reach the Clash of Clans API.\n"
        f"Reason: {error.get('reason', 'unknownError')}\n"
        f"Message: {error.get('message', 'Unknown API error.')}"
    )


def parse_coc_time(value: str | None):
    if not value:
        return None

    for fmt in ("%Y%m%dT%H%M%S.000Z", "%Y%m%dT%H%M%S.%fZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_live_war_state(war_data: dict, include_recent_ended: bool = False):
    if not war_data or get_api_error(war_data):
        return False

    state = war_data.get("state")
    if state in {"preparation", "inWar"}:
        return True

    if include_recent_ended and state == "warEnded":
        end_time = parse_coc_time(war_data.get("endTime"))
        if end_time:
            return datetime.now(timezone.utc) - end_time <= RECENT_WAR_GRACE_PERIOD

    return False


def is_clan_in_war(war_data: dict):
    clan_tag = RAW_CLAN_TAG.upper() if RAW_CLAN_TAG else None
    if not war_data or not clan_tag or get_api_error(war_data):
        return False

    war_clan_tag = war_data.get("clan", {}).get("tag", "").upper()
    war_opponent_tag = war_data.get("opponent", {}).get("tag", "").upper()
    return clan_tag in {war_clan_tag, war_opponent_tag}


async def fetch_live_war_async(include_recent_ended: bool = False):
    current_war = await fetch_current_war_async()
    if is_live_war_state(current_war, include_recent_ended=include_recent_ended):
        return current_war

    league_group = await fetch_league_group_async()
    if not league_group or league_group.get("reason"):
        return current_war

    rounds = league_group.get("rounds", [])
    if not rounds:
        return current_war

    war_tags_to_check = []
    for round_info in reversed(rounds):
        for war_tag in round_info.get("warTags", []):
            if war_tag and war_tag != "#0":
                war_tags_to_check.append(war_tag)

    for war_tag in war_tags_to_check:
        league_war = await fetch_league_war_by_tag_async(war_tag)
        if not is_clan_in_war(league_war):
            continue
        if is_live_war_state(league_war, include_recent_ended=include_recent_ended):
            return league_war

    return current_war


def save_war_to_history(war_data: dict):
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    try:
        war_log_file = os.path.join(log_dir, f"War_History_Log_{datetime.now().strftime('%Y%m%d')}.log")

        clan_data = war_data.get("clan", {})
        opponent_data = war_data.get("opponent", {})
        clan_name = clan_data.get("name", "N/A")
        opponent_name = opponent_data.get("name", "N/A")
        clan_attacks, clan_3star = get_attack_breakdown(clan_data)
        opponent_attacks, opponent_3star = get_attack_breakdown(opponent_data)
        clan_stars = clan_data.get("stars", 0)
        opponent_stars = opponent_data.get("stars", 0)
        clan_destruction = clan_data.get("destructionPercentage", 0)
        opponent_destruction = opponent_data.get("destructionPercentage", 0)
        max_attacks, attacks_per_member = get_war_attack_capacity(war_data)
        result = get_war_result(clan_data, opponent_data)

        if result == "WIN":
            winner = clan_name
        elif result == "LOSS":
            winner = opponent_name
        else:
            winner = "Draw"

        war_entry = (
            f"\n{'=' * 80}\n"
            f"TIMESTAMP: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{'=' * 80}\n"
            f"Clan: {clan_name} vs Opponent: {opponent_name}\n"
            f"Winner: {winner}\n\n"
            f"{clan_name} Stats:\n"
            f"  Attacks Used: {clan_attacks} / {max_attacks}\n"
            f"  3-Star Attacks: {clan_3star} / {clan_attacks or max_attacks}\n"
            f"  Stars: {clan_stars}\n"
            f"  Destruction: {clan_destruction:.2f}%\n\n"
            f"{opponent_name} Stats:\n"
            f"  Attacks Used: {opponent_attacks} / {max_attacks}\n"
            f"  3-Star Attacks: {opponent_3star} / {opponent_attacks or max_attacks}\n"
            f"  Stars: {opponent_stars}\n"
            f"  Destruction: {opponent_destruction:.2f}%\n"
            f"  Attacks Per Member: {attacks_per_member}\n"
            f"{'=' * 80}\n"
        )

        with open(war_log_file, "a", encoding="utf-8") as file:
            file.write(war_entry)

        logger.info(f"WAR_LOGGED: {clan_name} vs {opponent_name} - Winner: {winner}")
        return True
    except Exception as exc:
        logger.error(f"WAR_LOG_ERROR: {exc}")
        return False


def get_last_war_from_history():
    log_dir = "logs"
    try:
        files = [name for name in os.listdir(log_dir) if name.startswith("War_History_Log_") and name.endswith(".log")]
        files.sort(reverse=True)
        for war_log_file in files:
            full_path = os.path.join(log_dir, war_log_file)
            if not os.path.exists(full_path):
                continue
            with open(full_path, "r", encoding="utf-8") as file:
                content = file.read()
            entries = content.split("=" * 80)
            if len(entries) >= 3:
                return entries[-2].strip()
        return None
    except Exception as exc:
        logger.error(f"WAR_HISTORY_READ_ERROR: {exc}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /start - User: {user_id}")
    await update.message.reply_text("Welcome to ClashBot.\nUse /help to see the available war commands.")
    logger.info(f"CMD_SUCCESS: /start - User: {user_id}")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /help - User: {user_id}")

    help_text = (
        "<b>Available Commands</b>\n\n"
        "<b>General</b>\n"
        "<b>/start</b> - Start the bot\n"
        "<b>/help</b> - Show command help\n"
        "<b>/clan</b> - Clan information\n"
        "<b>/events</b> - Current events / announcements\n\n"
        "<b>War</b>\n"
        "<b>/war</b> - Current war status\n"
        "<b>/warstats</b> - Detailed war statistics\n"
        "<b>/warlog</b> - View the last saved war log\n"
        "<b>/warhistory</b> - View the last 5 saved wars\n"
    )

    await update.message.reply_text(help_text, parse_mode="HTML")
    logger.info(f"CMD_SUCCESS: /help - User: {user_id}")


async def adminhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /adminhelp - User: {user_id}")

    if not await is_group_admin(update, context):
        logger.warning(f"PERMISSION_DENIED: /adminhelp - User {user_id}")
        await update.message.reply_text("Permission denied. Only Telegram group admins can view admin commands.")
        return

    help_text = (
        "<b>Admin Commands</b>\n\n"
        "<b>/warlogupdate</b> - Save the ended war to history\n"
        "<b>/addwarlogmanually &lt;clan&gt; &lt;opponent&gt; &lt;result&gt; &lt;clan_stars&gt; "
        "&lt;opponent_stars&gt; &lt;clan_destruction&gt; &lt;opponent_destruction&gt;</b> - "
        "Add a war log entry manually"
    )

    await update.message.reply_text(help_text, parse_mode="HTML")
    logger.info(f"CMD_SUCCESS: /adminhelp - User: {user_id}")


async def clan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /clan - User: {user_id}")

    processing_message = None
    try:
        processing_message = await update.message.reply_text(get_random_processing_message())
        data = await coc_api_async(f"clans/{CLAN_TAG}")

        if not data:
            await processing_message.delete()
            await update.message.reply_text("Unable to fetch clan data.")
            return

        message = (
            f"Clan Stats\n"
            f"Name: {data.get('name', 'N/A')}\n"
            f"Level: {data.get('clanLevel', 'N/A')}\n"
            f"Trophies: {data.get('clanPoints', 'N/A')}\n"
            f"War Wins: {data.get('warWins', 'N/A')}\n"
            f"Members: {data.get('members', 'N/A')}"
        )

        await processing_message.delete()
        await update.message.reply_text(message)
        logger.info(f"CMD_SUCCESS: /clan - User: {user_id}")
    except Exception as exc:
        logger.error(f"CMD_EXCEPTION: /clan - {exc} - User: {user_id}")
        if processing_message:
            try:
                await processing_message.delete()
            except Exception:
                pass
        await update.message.reply_text("Unable to fetch clan data.")


async def war(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /war - User: {user_id}")

    processing_message = None
    try:
        processing_message = await update.message.reply_text(get_random_processing_message())
        data = await fetch_live_war_async()

        api_error_message = build_api_error_message(data)
        if api_error_message:
            await processing_message.delete()
            await update.message.reply_text(api_error_message)
            return

        if not is_live_war_state(data):
            await processing_message.delete()
            await update.message.reply_text("No active war currently.")
            return

        clan_data = data.get("clan", {})
        opponent_data = data.get("opponent", {})
        clan_attacks, _ = get_attack_breakdown(clan_data)
        opponent_attacks, _ = get_attack_breakdown(opponent_data)
        max_attacks, _ = get_war_attack_capacity(data)
        status_indicator = get_war_status_indicator(clan_data, opponent_data)

        message = (
            "⚔️ WAR BATTLE STATUS ⚔️\n\n"
            f"🏰 {clan_data.get('name', 'N/A')}\n"
            f"⭐ {clan_data.get('stars', 0)}   ⚔️ {clan_attacks}/{max_attacks}\n\n"
            "🆚\n\n"
            f"🏰 {opponent_data.get('name', 'N/A')}\n"
            f"⭐ {opponent_data.get('stars', 0)}   ⚔️ {opponent_attacks}/{max_attacks}\n\n"
            "━━━━━━━━━━━━━━\n"
            f"{status_indicator}\n"
            f"📊 Status: {data.get('state', 'N/A')}\n"
            f"📉 Score: {clan_data.get('stars', 0)} - {opponent_data.get('stars', 0)}\n"
            "🎯 Attacks Used:\n"
            f"• Us: {clan_attacks} / {max_attacks}\n"
            f"• Enemy: {opponent_attacks} / {max_attacks}\n"
            "━━━━━━━━━━━━━━"
        )

        await processing_message.delete()
        await update.message.reply_text(message)
        logger.info(f"CMD_SUCCESS: /war - User: {user_id}")
    except Exception as exc:
        logger.error(f"CMD_EXCEPTION: /war - {exc} - User: {user_id}")
        if processing_message:
            try:
                await processing_message.delete()
            except Exception:
                pass
        await update.message.reply_text("Unable to fetch war data.")


async def warstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /warstats - User: {user_id}")

    processing_message = None
    try:
        processing_message = await update.message.reply_text(get_random_processing_message())
        data = await fetch_live_war_async()

        api_error_message = build_api_error_message(data)
        if api_error_message:
            await processing_message.delete()
            await update.message.reply_text(api_error_message)
            return

        if not is_live_war_state(data):
            await processing_message.delete()
            await update.message.reply_text("No active war currently.")
            return

        clan_data = data.get("clan", {})
        opponent_data = data.get("opponent", {})
        clan_name = clan_data.get("name", "N/A")
        opponent_name = opponent_data.get("name", "N/A")
        clan_attacks, clan_3star = get_attack_breakdown(clan_data)
        opponent_attacks, opponent_3star = get_attack_breakdown(opponent_data)
        clan_stars = clan_data.get("stars", 0)
        clan_destruction = clan_data.get("destructionPercentage", 0)
        opponent_stars = opponent_data.get("stars", 0)
        opponent_destruction = opponent_data.get("destructionPercentage", 0)
        max_attacks, attacks_per_member = get_war_attack_capacity(data)
        status_indicator = get_war_status_indicator(clan_data, opponent_data)

        end_time = data.get("endTime", "")
        time_remaining = "N/A"
        if end_time:
            try:
                end_datetime = parse_coc_time(end_time)
                now = datetime.now(timezone.utc)
                time_diff = end_datetime - now
                if time_diff.total_seconds() > 0:
                    hours, remainder = divmod(int(time_diff.total_seconds()), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    time_remaining = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            except Exception:
                time_remaining = "N/A"

        message = (
            "📊 WAR STATISTICS\n\n"
            f"⚔️ {clan_name} vs {opponent_name}\n\n"
            "━━━━━━━━━━━━━━\n"
            f"{status_indicator}\n\n"
            f"🏰 {clan_name}\n"
            f"⭐ Stars: {clan_stars}\n"
            f"💥 Destruction: {clan_destruction:.2f}%\n"
            f"⚔️ Attacks: {clan_attacks} / {max_attacks}\n"
            f"🎯 Remaining: {max(0, max_attacks - clan_attacks)}\n"
            f"🔥 3⭐ Attacks: {clan_3star}\n\n"
            f"🏰 {opponent_name}\n"
            f"⭐ Stars: {opponent_stars}\n"
            f"💥 Destruction: {opponent_destruction:.2f}%\n"
            f"⚔️ Attacks: {opponent_attacks} / {max_attacks}\n"
            f"🎯 Remaining: {max(0, max_attacks - opponent_attacks)}\n"
            f"🔥 3⭐ Attacks: {opponent_3star}\n\n"
            "━━━━━━━━━━━━━━\n"
            "⚙️ WAR INFO\n"
            f"👥 Attacks per Player: {attacks_per_member}\n"
            f"⏳ Time Remaining: {time_remaining}\n"
            "━━━━━━━━━━━━━━"
        )

        await processing_message.delete()
        await update.message.reply_text(message)
        logger.info(f"CMD_SUCCESS: /warstats - User: {user_id}")
    except Exception as exc:
        logger.error(f"CMD_EXCEPTION: /warstats - {exc} - User: {user_id}")
        if processing_message:
            try:
                await processing_message.delete()
            except Exception:
                pass
        await update.message.reply_text("Unable to fetch war statistics.")


async def events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /events - User: {user_id}")

    processing_message = None
    try:
        processing_message = await update.message.reply_text(get_random_processing_message())
        data = await coc_api_async(f"clans/{CLAN_TAG}/events")

        if data and data.get("items"):
            lines = ["Current Events / Announcements"]
            for event in data.get("items", []):
                name = event.get("name", "Unknown Event")
                description = event.get("description", "")
                lines.append(f"{name}: {description}" if description else name)
            message = "\n\n".join(lines)
        else:
            message = MANUAL_EVENTS

        await processing_message.delete()
        await update.message.reply_text(message)
        logger.info(f"CMD_SUCCESS: /events - User: {user_id}")
    except Exception as exc:
        logger.error(f"CMD_EXCEPTION: /events - {exc} - User: {user_id}")
        if processing_message:
            try:
                await processing_message.delete()
            except Exception:
                pass
        await update.message.reply_text(MANUAL_EVENTS)


async def warlogupdate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /warlogupdate - User: {user_id}")

    try:
        data = await fetch_live_war_async(include_recent_ended=True)
        api_error_message = build_api_error_message(data)
        if api_error_message:
            await update.message.reply_text(api_error_message)
            return

        if data.get("state") == "notInWar":
            await update.message.reply_text("No war to log. War must be active or ended.")
            return

        if data.get("state") != "warEnded":
            await update.message.reply_text("War is still ongoing. War must be ended to save to history.")
            return

        if save_war_to_history(data):
            clan_data = data.get("clan", {})
            opponent_data = data.get("opponent", {})
            add_war_log(
                {
                    "clan": clan_data.get("name", "N/A"),
                    "opponent": opponent_data.get("name", "N/A"),
                    "result": get_war_result(clan_data, opponent_data),
                    "clan_stars": clan_data.get("stars", 0),
                    "opponent_stars": opponent_data.get("stars", 0),
                    "clan_destruction": clan_data.get("destructionPercentage", 0),
                    "opponent_destruction": opponent_data.get("destructionPercentage", 0),
                    "source": "API",
                }
            )
            await update.message.reply_text(
                f"War logged successfully.\nSaved: {clan_data.get('name', 'N/A')} vs "
                f"{opponent_data.get('name', 'N/A')}"
            )
            logger.info(f"CMD_SUCCESS: /warlogupdate - User: {user_id}")
            return

        await update.message.reply_text("Failed to save war to history.")
    except Exception as exc:
        logger.error(f"CMD_EXCEPTION: /warlogupdate - {exc} - User: {user_id}")
        await update.message.reply_text("Unable to update war log.")


async def warlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /warlog - User: {user_id}")

    processing_message = None
    try:
        processing_message = await update.message.reply_text(get_random_processing_message())
        last_war = get_last_war_from_history()

        if not last_war:
            await processing_message.delete()
            await update.message.reply_text("No war history found.\nUse /warlogupdate to save a war result.")
            return

        await processing_message.delete()
        await update.message.reply_text(f"Last War Result\n{last_war}")
        logger.info(f"CMD_SUCCESS: /warlog - User: {user_id}")
    except Exception as exc:
        logger.error(f"CMD_EXCEPTION: /warlog - {exc} - User: {user_id}")
        if processing_message:
            try:
                await processing_message.delete()
            except Exception:
                pass
        await update.message.reply_text("Unable to retrieve war history.")


async def warhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /warhistory - User: {user_id}")

    processing_message = None
    try:
        processing_message = await update.message.reply_text(get_random_processing_message())
        wars = get_war_logs()

        if not wars:
            await processing_message.delete()
            await update.message.reply_text("War History\n\nNo wars have been saved yet.\nUse /warlogupdate to save a war.")
            return

        lines = ["War History (Last 5)", ""]
        for idx, war in enumerate(wars, 1):
            lines.append(
                f"{idx}. {war.get('clan', 'Unknown')} vs {war.get('opponent', 'Unknown')}\n"
                f"   Result: {war.get('result', 'N/A')}\n"
                f"   Saved: {war.get('saved_at', 'Unknown')}"
            )

        await processing_message.delete()
        await update.message.reply_text("\n\n".join(lines))
        logger.info(f"CMD_SUCCESS: /warhistory - User: {user_id}")
    except Exception as exc:
        logger.error(f"CMD_EXCEPTION: /warhistory - {exc} - User: {user_id}")
        if processing_message:
            try:
                await processing_message.delete()
            except Exception:
                pass
        await update.message.reply_text("Unable to retrieve war history.")


async def addwarlogmanually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"CMD_EXECUTED: /addwarlogmanually - User: {user_id}")

    if not await is_group_admin(update, context):
        logger.warning(f"PERMISSION_DENIED: /addwarlogmanually - User {user_id}")
        await update.message.reply_text("Permission denied. Only Telegram group admins can update war history.")
        return

    processing_message = None
    try:
        processing_message = await update.message.reply_text(get_random_processing_message())
        if not context.args or len(context.args) < 7:
            await processing_message.delete()
            await update.message.reply_text(
                "Usage: /addwarlogmanually <clan> <opponent> <result> <clan_stars> "
                "<opponent_stars> <clan_destruction> <opponent_destruction>"
            )
            return

        clan = context.args[0]
        opponent = context.args[1]
        result = context.args[2].upper()
        clan_stars = int(context.args[3])
        opponent_stars = int(context.args[4])
        clan_destruction = float(context.args[5])
        opponent_destruction = float(context.args[6])

        if result not in {"WIN", "LOSS", "DRAW"}:
            await processing_message.delete()
            await update.message.reply_text("Result must be WIN, LOSS, or DRAW.")
            return

        war_entry = {
            "clan": clan,
            "opponent": opponent,
            "result": result,
            "clan_stars": clan_stars,
            "opponent_stars": opponent_stars,
            "clan_destruction": clan_destruction,
            "opponent_destruction": opponent_destruction,
            "source": "Manual",
        }

        add_war_log(war_entry)
        await processing_message.delete()
        await update.message.reply_text(
            f"War log saved.\n{clan} vs {opponent} - {result}\n"
            f"Score: {clan_stars}-{opponent_stars} | Destruction: "
            f"{clan_destruction:.1f}%-{opponent_destruction:.1f}%"
        )
        logger.info(f"CMD_SUCCESS: /addwarlogmanually - User: {user_id}")
    except ValueError:
        if processing_message:
            await processing_message.delete()
        await update.message.reply_text("Invalid input. Make sure numeric fields are formatted correctly.")
    except Exception as exc:
        logger.error(f"CMD_EXCEPTION: /addwarlogmanually - {exc} - User: {user_id}")
        if processing_message:
            try:
                await processing_message.delete()
            except Exception:
                pass
        await update.message.reply_text("Unable to save war log.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception while handling an update: {context.error}")
    error_msg = str(context.error)

    if "NetworkError" in str(type(context.error)) or "httpx" in error_msg:
        logger.warning(f"Network error (will retry): {error_msg}")
        return

    if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
        logger.warning(f"Connection timeout (will retry): {error_msg}")
        return

    if "ReadError" in error_msg or "httpcore" in error_msg:
        logger.warning(f"Read error (will retry): {error_msg}")
        return

    import traceback

    logger.error(traceback.format_exc())


def main():
    validate_configuration()
    ensure_storage_exists()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("adminhelp", adminhelp))
    app.add_handler(CommandHandler("clan", clan))
    app.add_handler(CommandHandler("war", war))
    app.add_handler(CommandHandler("warstats", warstats))
    app.add_handler(CommandHandler("events", events))
    app.add_handler(CommandHandler("warlogupdate", warlogupdate))
    app.add_handler(CommandHandler("warlog", warlog))
    app.add_handler(CommandHandler("warhistory", warhistory))
    app.add_handler(CommandHandler("addwarlogmanually", addwarlogmanually))
    app.add_error_handler(error_handler)

    logger.info("BOT_STARTED")
    print("BOT_STARTED")

    try:
        public_ip = requests.get("https://api.ipify.org", timeout=10).text.strip()
        print(f"PUBLIC_IP: {public_ip}")
        logger.info(f"PUBLIC_IP: {public_ip}")
    except Exception as exc:
        print(f"PUBLIC_IP_ERROR: {exc}")
        logger.warning(f"PUBLIC_IP_ERROR: {exc}")

    print("ClashBot is running...")
    print("Use /help in Telegram to see the available commands.")
    app.run_polling()


if __name__ == "__main__":
    main()
