import asyncio
import base64
import json
import logging
import os
import re
import sys

from telethon import TelegramClient, events
from telethon.errors import ChatWriteForbiddenError, FloodWaitError
from telethon.tl.custom import Button

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cloner")


def load_env(path=".env"):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


load_env()

API_ID = int(os.environ.get("API_ID", "0") or 0)
API_HASH = os.environ.get("API_HASH", "")
PHONE = os.environ.get("PHONE", "")
CODE = os.environ.get("CODE", "")
SESSION = (os.environ.get("SESSION", "cloner") or "cloner").strip(".session")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_ID = os.environ.get("GIST_ID", "")

CONFIG_FILE = "config.json"
PROGRESS_FILE = "progress.json"

BATCH = int(os.environ.get("BATCH", "10"))
BATCH_SLEEP = float(os.environ.get("BATCH_SLEEP", "12"))
MAX_RETRIES = 2

WELCOME = (
    "Hi! I'm your channel cloner userbot. 🤖\n\n"
    "Send me:\n"
    "\u2022 a Telegram message link (`t.me/...`)\n"
    "\u2022 a channel @username\n"
    "\u2022 any URL (mp4/jpg/YouTube/TikTok...)\n"
    "\u2022 or simply forward any media to me\n\n"
    "Then tap the buttons below \u2014 no commands needed."
)

URL_RE = re.compile(r"t\.me/(?:c/|s/)?([^/\s?]+)(?:/(\d+))?")

states = {}   # chat-specific jobs etc.
pending = {}  # chats waiting for a destination reply
stop_events = {}


def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_user_conf(cid):
    cfg = load_json(CONFIG_FILE, {})
    return cfg.setdefault("users", {}).setdefault(str(cid), {"dest": None})


def set_user_dest(cid, dest):
    cfg = load_json(CONFIG_FILE, {})
    cfg.setdefault("users", {})[str(cid)] = {"dest": dest}
    save_json(CONFIG_FILE, cfg)


def get_copied(src_key):
    p = load_json(PROGRESS_FILE, {})
    return set(p.setdefault("sources", {}).get(str(src_key), []))


def mark_copied(src_key, mid):
    p = load_json(PROGRESS_FILE, {})
    s = p.setdefault("sources", {}).setdefault(str(src_key), [])
    if mid not in s:
        s.append(mid)
    save_json(PROGRESS_FILE, p)


# ---------- gist session persistence ----------
def gist_pull():
    if not (GIST_TOKEN and GIST_ID):
        return
    try:
        import requests

        r = requests.get(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={"Authorization": f"token {GIST_TOKEN}"},
        )
        r.raise_for_status()
        fname = SESSION + ".session.b64"
        files = r.json().get("files", {})
        if fname in files and files[fname].get("content"):
            with open(SESSION + ".session", "wb") as f:
                f.write(base64.b64decode(files[fname]["content"]))
            log.info("session restored from gist")
    except Exception as e:
        log.warning(f"gist pull failed: {e}")


def gist_push():
    if not (GIST_TOKEN and GIST_ID):
        return
    try:
        import requests

        fname = SESSION + ".session.b64"
        content = base64.b64encode(open(SESSION + ".session", "rb").read()).decode()
        r = requests.patch(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={"Authorization": f"token {GIST_TOKEN}"},
            json={"files": {fname: {"content": content}}},
        )
        r.raise_for_status()
        log.info("session pushed to gist")
    except Exception as e:
        log.warning(f"gist push failed: {e}")


# ---------- helpers ----------
def parse_source(text):
    m = URL_RE.search(text)
    if not m:
        return None, None
    grp, mid = m.group(1), m.group(2)
    if grp.isdigit():
        peer = int(("-100" + grp) if not grp.startswith("-100") else grp)
    else:
        peer = grp
    return peer, (int(mid) if mid else None)


def src_key(peer):
    return f"peer-{peer}"


async def do_login(client):
    if not (API_ID and API_HASH):
        log.error("API_ID and API_HASH are required. Get from https://my.telegram.org")
        sys.exit(1)
    await client.connect()
    if not await client.is_user_authorized():
        if not PHONE:
            log.error("PHONE number is required for first login")
            sys.exit(1)
        await client.send_code_request(PHONE)
        code = CODE or input(f"Enter the login code for {PHONE}: ").strip()
        try:
            await client.sign_in(PHONE, code)
        except Exception as e:
            log.error(f"sign-in failed: {e}")
            sys.exit(1)
        log.info("Logged in, saving session to gist")
        gist_push()
    else:
        log.info("Already authorized")


# ---------- single message ----------
async def handle_single(client, cid, peer, mid):
    try:
        msg = await client.get_messages(peer, ids=mid)
    except Exception as e:
        await client.send_message(cid, f"❌ Could not load the message: {e}")
        return
    if not msg:
        await client.send_message(cid, "❌ Message not found. Are you a member of that channel?")
        return
    await client.send_message(cid, msg)

# ---------- full channel clone ----------
def progress_text(total, done, fails, rate, stopped=False):
    pct = f"{done / total * 100:.1f}%" if total else "?"
    head = "🛑 Stopped early." if stopped else "Copying..."
    return (
        f"{head}\n"
        f"✅ {done} / {total} ({pct})\n"
        f"❌ fails: {fails}\n"
        f"⚡ {rate:.2f} msg/s"
    )


async def full_copy(client, cid, peer, dest):
    key = src_key(peer)
    stop = stop_events.get(cid)
    if stop and not stop.is_set():
        await client.send_message(cid, "A copy is already running for you. Use 🛑 Stop first.")
        return
    stop = asyncio.Event()
    stop_events[cid] = stop

    try:
        total = (await client.get_messages(peer, limit=1)).total or 0
    except Exception as e:
        await client.send_message(cid, f"❌ Can't open that channel: {e}\n\nYour session must be a member of it.")
        return

    done = scanned = fails_count = 0
    fails = []
    copied_before = get_copied(key)
    already = len(copied_before)

    msg = await client.send_message(
        cid,
        f"Starting copy of {total} messages to @{dest}...",
        buttons=[[Button.inline("🛑 Stop", data=f"stop:{cid}:0")]],
    )
    it = client.iter_messages(peer)
    batch = []
    start = asyncio.get_event_loop().time()

    async def flush():
        nonlocal done, fails_count, fails, scanned
        for m in batch:
            if stop.is_set():
                return
            if getattr(m, "action", None) is not None or m.id in copied_before:
                continue
            ok = False
            for attempt in range(MAX_RETRIES + 1):
                try:
                    await client.send_message(dest, m)
                    ok = True
                    break
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                except ChatWriteForbiddenError:
                    break
                except Exception:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(2)
            if ok:
                done += 1
                copied_before.add(m.id)
                mark_copied(key, m.id)
            else:
                fails_count += 1
                fails.append(m.id)
        batch.clear()

    progressed = 0
    while True:
        try:
            m = await anext(it)
        except StopAsyncIteration:
            break
        if stop.is_set():
            break
        batch.append(m)
        if len(batch) >= BATCH:
            scanned += len(batch)
            await flush()
            await asyncio.sleep(BATCH_SLEEP)
            elapsed = asyncio.get_event_loop().time() - start
            rate = done / elapsed if elapsed else 0
            progressed += 1
            if progressed % 1 == 0:
                try:
                    await msg.edit(
                        progress_text(total, done, fails_count, rate),
                        buttons=[[Button.inline("🛑 Stop", data=f"stop:{cid}:0")]],
                    )
                except Exception:
                    pass

    await flush()

    if stop.is_set():
        await msg.edit(progress_text(total, done, fails_count, 0, stopped=True))
    else:
        btns = ([f"\U0001f501 Retry failed ({fails_count})", f"retry:{cid}:0"] if fails_count else []) or None
        summary = (
            "✅ Copy finished.\n"
            f"Total: {total}\n"
            f"Done: {done} (already copied before: {already})\n"
            f"Failed: {fails_count}"
        )
        if fails_count:
            summary += f"\nFail IDs: {fails[:20]}{'...' if len(fails) > 20 else ''}"
        await msg.edit(summary, buttons=[[Button.inline("🔁 Retry failed", data=f"retry:{cid}:0")]] if fails_count else None)

    if fails_count and fails:
        states[f"faillist:{cid}"] = {"peer": peer, "ids": fails}


async def copy_failed(client, cid, peer, ids):
    u = get_user_conf(cid)
    dest = u.get("dest")
    if not dest:
        await client.send_message(cid, "No destination saved. Send a channel @username first.")
        return
    done, still = 0, 0
    for mid in ids:
        try:
            m = await client.get_messages(peer, ids=mid)
            await client.send_message(dest, m)
            done += 1
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
            try:
                m = await client.get_messages(peer, ids=mid)
                await client.send_message(dest, m)
                done += 1
            except Exception:
                still += 1
        except Exception:
            still += 1
        await asyncio.sleep(0.5)
    await client.send_message(cid, f"🔁 Retry done: ✅ {done} fixed, ❌ {still} still failed.")


# ---------- direct URL download ----------
async def handle_direct_url(client, cid, text):
    import yt_dlp

    st = await client.send_message(cid, "⬇️ Downloading...")
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "tdl")
    os.makedirs(path, exist_ok=True)
    opts = {
        "outtmpl": os.path.join(path, "%(title).100s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(text, download=True)
            fname = ydl.prepare_filename(info)
        if not os.path.exists(fname):
            cands = [f for f in os.listdir(path) if f.lower().endswith((".mp4", ".mkv", ".webm", ".jpg", ".png", ".gif"))]
            if not cands:
                raise RuntimeError("no file produced")
            fname = os.path.join(path, cands[0])
        await st.edit("⬆️ Uploading...")
        await client.send_file(cid, fname)
        await st.edit("✅ Done!")
        try:
            os.remove(fname)
        except Exception:
            pass
    except Exception as e:
        await st.edit(f"❌ Download failed: {e}")


# ---------- main message handler ----------
@events.register(events.NewMessage)
async def on_message(event):
    if not event.is_private or event.out:
        return
    cid = event.chat_id
    text = (event.message.text or event.message.message or "").strip() or (event.message.caption or "").strip()

    if text == "/start":
        await event.respond(WELCOME)
        return

    # a destination reply while pending
    if cid in pending and text:
        p = pending.pop(cid)
        dest = text.strip().lstrip("@").replace(" ", "")
        set_user_dest(cid, dest)
        await event.respond(f"Destination saved: @{dest}")
        if p.get("mid"):
            peer = p["peer"]
            await full_copy(client, cid, peer, dest)
        return

    if URL_RE.search(text):
        peer, mid = parse_source(text)
        if mid:
            states[f"peer:{cid}:{mid}"] = peer
            await event.respond(
                f"What do you want with this message?\n`{text}`",
                buttons=[
                    [Button.inline("📄 Only this one", data=f"single:{cid}:{mid}"),
                     Button.inline("📦 Clone whole channel", data=f"full:{cid}:{mid}")]
                ],
            )
        else:
            u = get_user_conf(cid)
            dest = u.get("dest")
            if not dest:
                pending[cid] = {"peer": peer, "mid": None}
                await event.respond("❓ Destination channel? Send its @username (you must be admin there).")
                return
            await event.respond("Scanning channel...")
            await full_copy(client, cid, peer, dest)
        return

    if text.startswith("http"):
        await handle_direct_url(client, cid, text)
        return

    if event.message.media or getattr(event.message, "forward", None):
        states[f"media:{cid}"] = event.message.id
        u = get_user_conf(cid)
        dest = u.get("dest")
        if dest:
            await event.respond(
                "Send this to your channel?",
                buttons=[[Button.inline("📤 Send to my channel", data=f"sendmedia:{cid}:0")]],
            )
        else:
            await event.respond("Set a destination first: send a channel @username.")
        return

    await event.respond(CALLOUT)


CALLOUT = "❓ Send a Telegram link, a @username, a direct URL, or forward any media to start."


@events.register(events.CallbackQuery)
async def on_callback(event):
    cid = event.chat_id
    try:
        key, a, b = event.data.decode().split(":")
    except Exception:
        return

    if key == "single":
        mid = int(b)
        peer = states.get(f"peer:{cid}:{mid}")
        if not peer:
            await event.answer("Message expired, send the link again", alert=True)
            return
        await event.answer()
        await handle_single(client, cid, peer, mid)

    elif key == "full":
        mid = int(b)
        peer = states.get(f"peer:{cid}:{mid}")
        if not peer:
            await event.answer("Message expired, send the link again", alert=True)
            return
        u = get_user_conf(cid)
        dest = u.get("dest")
        if not dest:
            pending[cid] = {"peer": peer, "mid": mid}
            await event.answer("Destination needed", alert=True)
            await event.client.send_message(cid, "❓ Destination channel? Send its @username (you must be admin there).")
            return
        try:
            total = (await client.get_messages(peer, limit=1)).total or 0
        except Exception as e:
            await event.answer(f"Can't open: {e}", alert=True)
            return
        states[f"gm:{cid}"] = peer
        await event.edit(
            f"📦 Clone all {total} messages → @{dest}?\n"
            f"Batch {BATCH} msgs | sleep {BATCH_SLEEP}s | forward as-is",
            buttons=[
                [Button.inline("✅ Start copy", data=f"go:{cid}:0"),
                 Button.inline("❌ Cancel", data=f"cancel:{cid}:0")]
            ],
        )

    elif key == "go":
        peer = states.get(f"gm:{cid}")
        u = get_user_conf(cid)
        dest = u.get("dest")
        if not peer or not dest:
            await event.answer("Missing info, send link again", alert=True)
            return
        states.pop(f"gm:{cid}", None)
        await event.edit("Starting...")
        await full_copy(client, cid, peer, dest)

    elif key == "cancel":
        await event.edit("❌ Canceled.")

    elif key == "stop":
        st = stop_events.get(cid)
        if st:
            st.set()
            await event.answer("Stopping...")
        else:
            await event.answer("Not running")

    elif key == "retry":
        fl = states.get(f"faillist:{cid}")
        if not fl:
            await event.answer("No failed list", alert=True)
            return
        await event.answer()
        await copy_failed(client, cid, fl["peer"], fl["ids"])

    elif key == "sendmedia":
        mid = states.pop(f"media:{cid}", None)
        u = get_user_conf(cid)
        dest = u.get("dest")
        await event.answer()
        if not mid or not dest:
            await event.edit("Destination not set. Send a @username first.")
            return
        try:
            m = await client.get_messages(cid, ids=mid)
            await event.edit("📤 Forwarding...")
            await client.send_message(dest, m)
            await event.edit("✅ Sent to your channel!")
        except Exception as e:
            await event.edit(f"❌ {e}")


async def main():
    client = TelegramClient(SESSION + ".session", API_ID, API_HASH, flood_sleep_threshold=30)
    client.add_event_handler(on_message)
    client.add_event_handler(on_callback)
    gist_pull()
    await do_login(client)
    log.info("Bot is running...")
    await asyncio.gather(client.run_until_disconnected())


if __name__ == "__main__":
    asyncio.run(main())