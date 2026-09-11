import os
import logging
import asyncio
import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============ CONFIG (env vars override these Railway-friendly defaults) ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8967846178:AAF0vh_5j3d_vxgmZwx86GdU868NE7WZhKs")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "7764565509").split(",")]
FORCE_CHANNEL_ID = int(os.environ.get("FORCE_CHANNEL_ID", "-1003936834388"))
FORCE_CHANNEL_LINK = os.environ.get("FORCE_CHANNEL_LINK", "https://t.me/+ZR85xbLwVysxNTFh")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "downloads")
# =====================================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("ytbot")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

user_links = {}          # user_id -> last youtube url
runtime_config = {"force_link": FORCE_CHANNEL_LINK}


# ---------------- Force join check ----------------
async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(FORCE_CHANNEL_ID, user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception as e:
        log.warning(f"Membership check failed: {e}")
        return False


def join_keyboard():
    kb = [
        [InlineKeyboardButton("📢 Join Channel", url=runtime_config["force_link"])],
        [InlineKeyboardButton("✅ I Joined", callback_data="check_join")],
    ]
    return InlineKeyboardMarkup(kb)


async def require_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if await is_member(user_id, context):
        return True
    text = "🔒 You must join our channel to use this bot.\n\nAfter joining, tap ✅ I Joined."
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=join_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=join_keyboard())
    return False


# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_join(update, context):
        return
    await update.message.reply_text(
        "👋 <b>YouTube Downloader Bot</b>\n\n"
        "Send me a YouTube link and I'll let you download it as video or audio "
        "in different qualities.",
        parse_mode=ParseMode.HTML,
    )


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = q.from_user.id
    if await is_member(user_id, context):
        await q.answer("✅ Verified! You can use the bot now.", show_alert=True)
        await q.message.edit_text("✅ Membership verified. Send me a YouTube link to start.")
    else:
        await q.answer("❌ You haven't joined the channel yet.", show_alert=True)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_join(update, context):
        return
    url = update.message.text.strip()
    if "youtu" not in url:
        await update.message.reply_text("⚠️ Please send a valid YouTube link.")
        return

    user_links[update.effective_user.id] = url
    kb = [
        [
            InlineKeyboardButton("🎬 Video", callback_data="mode_video"),
            InlineKeyboardButton("🎵 Audio", callback_data="mode_audio"),
        ]
    ]
    await update.message.reply_text(
        "Choose download type:", reply_markup=InlineKeyboardMarkup(kb)
    )


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, context):
        return
    mode = q.data.split("_")[1]  # video / audio

    if mode == "video":
        kb = [
            [InlineKeyboardButton("360p", callback_data="dl_video_360")],
            [InlineKeyboardButton("480p", callback_data="dl_video_480")],
            [InlineKeyboardButton("720p", callback_data="dl_video_720")],
            [InlineKeyboardButton("1080p", callback_data="dl_video_1080")],
        ]
    else:
        kb = [
            [InlineKeyboardButton("128 kbps", callback_data="dl_audio_128")],
            [InlineKeyboardButton("192 kbps", callback_data="dl_audio_192")],
            [InlineKeyboardButton("320 kbps", callback_data="dl_audio_320")],
        ]
    await q.message.edit_text("Choose quality:", reply_markup=InlineKeyboardMarkup(kb))


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("⏳ Downloading...")
    if not await require_join(update, context):
        return

    user_id = q.from_user.id
    url = user_links.get(user_id)
    if not url:
        await q.message.reply_text("⚠️ Send the link again.")
        return

    _, kind, quality = q.data.split("_")
    status_msg = await q.message.reply_text("⏳ Downloading, please wait...")

    out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_%(id)s.%(ext)s")

    try:
        if kind == "video":
            ydl_opts = {
                "format": f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]",
                "merge_output_format": "mp4",
                "outtmpl": out_template,
                "noplaylist": True,
                "concurrent_fragment_downloads": 8,
                "quiet": True,
                "no_warnings": True,
            }
        else:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": out_template,
                "noplaylist": True,
                "concurrent_fragment_downloads": 8,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": quality,
                    }
                ],
                "quiet": True,
                "no_warnings": True,
            }

        loop = asyncio.get_event_loop()

        def run_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        filename = await loop.run_in_executor(None, run_download)

        if kind == "audio" and not filename.endswith(".mp3"):
            filename = os.path.splitext(filename)[0] + ".mp3"

        await status_msg.edit_text("⬆️ Uploading...")

        with open(filename, "rb") as f:
            if kind == "video":
                await context.bot.send_video(
                    chat_id=q.message.chat_id,
                    video=f,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60,
                )
            else:
                await context.bot.send_audio(
                    chat_id=q.message.chat_id,
                    audio=f,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60,
                )

        await status_msg.delete()
        os.remove(filename)

    except Exception as e:
        log.error(f"Download error: {e}")
        await status_msg.edit_text(f"❌ Error: {e}")


# ---------------- Admin panel ----------------
def admin_keyboard():
    kb = [
        [InlineKeyboardButton("🔗 Change Force-Join Link", callback_data="admin_change_link")],
        [InlineKeyboardButton("📊 Bot Stats", callback_data="admin_stats")],
    ]
    return InlineKeyboardMarkup(kb)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return
    await update.message.reply_text("🛠 <b>Admin Panel</b>", reply_markup=admin_keyboard(),
                                     parse_mode=ParseMode.HTML)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("⛔ Not authorized", show_alert=True)
        return
    await q.answer()

    if q.data == "admin_change_link":
        context.user_data["awaiting_link"] = True
        await q.message.reply_text("Send the new force-join channel link (https://t.me/...):")

    elif q.data == "admin_stats":
        await q.message.reply_text(
            f"📊 Users tracked this session: {len(user_links)}\n"
            f"🔗 Current join link: {runtime_config['force_link']}"
        )


async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if context.user_data.get("awaiting_link"):
        new_link = update.message.text.strip()
        if new_link.startswith("https://t.me/"):
            runtime_config["force_link"] = new_link
            context.user_data["awaiting_link"] = False
            await update.message.reply_text(f"✅ Force-join link updated to:\n{new_link}")
        else:
            await update.message.reply_text("⚠️ Invalid link. Must start with https://t.me/")
        return
    # not admin input, fall through to normal link handler
    await handle_link(update, context)


# ---------------- Main ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(mode_callback, pattern="^mode_"))
    app.add_handler(CallbackQueryHandler(download_callback, pattern="^dl_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_input))

    log.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
