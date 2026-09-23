import asyncio
import base64
import json
import logging
import os
import sys

from telethon import TelegramClient
from telethon.errors import ChatForwardsRestrictedError, ChatWriteForbiddenError, FloodWaitError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oneshot")

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION = (os.environ.get("SESSION", "cloner") or "cloner").strip(".session")
SRC = os.environ.get("SRC", "")
DST = os.environ.get("DST", "")
BATCH = int(os.environ.get("BATCH", "10"))
BATCH_SLEEP = float(os.environ.get("BATCH_SLEEP", "12"))
COPY = os.environ.get("COPY", "").lower() in ("1", "true", "yes")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_ID = os.environ.get("GIST_ID", "")

TMP = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cloner_dl")
RAM = os.environ.get("RAM", "").lower() in ("1", "true", "yes")
os.makedirs(TMP, exist_ok=True)

PROGRESS_GIST_FILE = "progress.json"


def progress_pull():
    if not (GIST_TOKEN and GIST_ID):
        return None
    try:
        import requests

        r = requests.get(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={"Authorization": f"token {GIST_TOKEN}"},
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        if PROGRESS_GIST_FILE in files and files[PROGRESS_GIST_FILE].get("content"):
            return json.loads(files[PROGRESS_GIST_FILE]["content"]).get("ids", [])
    except Exception as e:
        log.warning(f"progress pull failed: {e}")
    return None


def progress_push(copied_file):
    if not (GIST_TOKEN and GIST_ID):
        return
    try:
        import requests

        content = open(copied_file).read()
        r = requests.patch(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={"Authorization": f"token {GIST_TOKEN}"},
            json={"files": {PROGRESS_GIST_FILE: {"content": content}}},
        )
        r.raise_for_status()
    except Exception as e:
        log.warning(f"progress push failed: {e}")


async def send_copy(client, dst, m):
    if getattr(m, "media", None) is not None and m.file:
        try:
            if RAM:
                import io

                buf = io.BytesIO()
                buf.name = getattr(m.file, "name", None) or f"media_{m.id}"
                await m.download_media(buf)
                buf.seek(0)
                caption = (getattr(m, "message", "") or "").strip() or None
                await client.send_file(dst, buf, caption=caption)
                buf.close()
                return True
            path = await m.download_media(TMP)
            if not path:
                return False
            caption = (getattr(m, "message", "") or "").strip() or None
            await client.send_file(dst, path, caption=caption)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            return True
        except Exception as e:
            log.warning(f"copy media {m.id} failed: {type(e).__name__}: {e}")
            return False
    if getattr(m, "text", None):
        try:
            await client.send_message(dst, m.text)
            return True
        except Exception as e:
            log.warning(f"copy text {m.id} failed: {type(e).__name__}: {e}")
            return False
    return None


async def run():
    client = TelegramClient(
        SESSION + ".session",
        API_ID,
        API_HASH,
        flood_sleep_threshold=30,
        connection_retries=10,
        request_retries=8,
        retry_delay=2,
    )
    await client.connect()
    if not await client.is_user_authorized():
        log.error("not authorized")
        await client.disconnect()
        return

    src = await client.get_entity(SRC)
    dst = await client.get_entity(DST)
    protected = bool(getattr(src, "noforwards", False))
    copy_mode = COPY or protected
    log.info(f"SOURCE: {getattr(src, 'title', src)} (id={src.id}) noforwards={protected}")
    log.info(f"DST   : {getattr(dst, 'title', dst)} (id={dst.id})")
    log.info(f"MODE  : {'COPY (download+re-upload)' if copy_mode else 'FORWARD'}")

    total = (await client.get_messages(src, limit=1)).total or 0
    log.info(f"TOTAL messages: {total}")

    iterator = client.iter_messages(src, reverse=True)

    done = skipped = fails = 0
    fails_list = []
    copied_file = "oneshot_progress.json"
    try:
        with open(copied_file) as f:
            copied = set(json.load(f).get("ids", []))
    except Exception:
        copied = set()
    gist_ids = progress_pull()
    if gist_ids:
        merged = copied | set(gist_ids)
        if merged != copied:
            copied = merged
            with open(copied_file, "w") as f:
                json.dump({"ids": sorted(copied)}, f)
        log.info(f"resume: {len(copied)} ids are already copied (merged with gist)")

    log.info("clone started...")
    start = asyncio.get_event_loop().time()
    batch = []
    cfg = {"copy": copy_mode}

    async def flush():
        nonlocal done, skipped, fails, fails_list
        for m in batch:
            if getattr(m, "action", None) is not None or m.id in copied:
                continue
            ok = False
            r = None
            for attempt in range(3):
                try:
                    if cfg["copy"]:
                        r = await send_copy(client, dst, m)
                        ok = bool(r)
                        if r is None:
                            break
                    else:
                        await client.send_message(dst, m)
                        ok = True
                    break
                except FloodWaitError as e:
                    log.info(f"flood wait {e.seconds}s")
                    await asyncio.sleep(e.seconds + 2)
                except ChatForwardsRestrictedError:
                    log.info("forward blocked -> switching to copy mode")
                    cfg["copy"] = True
                    r = await send_copy(client, dst, m)
                    ok = bool(r)
                    if r is None:
                        break
                    break
                except ChatWriteForbiddenError:
                    log.error("destination write forbidden - not admin?")
                    await client.disconnect()
                    sys.exit(2)
                except Exception as e:
                    log.warning(f"msg {m.id} attempt {attempt+1}: {type(e).__name__}: {e}")
                    if attempt < 2:
                        await asyncio.sleep(2)
            if r is None:
                skipped += 1
            elif ok:
                done += 1
                copied.add(m.id)
                with open(copied_file, "w") as f:
                    json.dump({"ids": sorted(copied)}, f)
            else:
                fails += 1
                fails_list.append(m.id)
        batch.clear()

    counter = 0
    while True:
        try:
            m = await anext(iterator)
        except StopAsyncIteration:
            break
        if getattr(m, "action", None) is not None or m.id in copied:
            continue
        batch.append(m)
        if len(batch) >= BATCH:
            counter += 1
            await flush()
            elapsed = asyncio.get_event_loop().time() - start
            rate = done / elapsed if elapsed else 0
            log.info(f"batch {counter}: done {done}/{total} ({done/total*100:.1f}%) fails {fails} | {rate:.2f} msg/s")
            if counter % 5 == 0:
                progress_push(copied_file)
            await asyncio.sleep(BATCH_SLEEP)

    await flush()
    elapsed = asyncio.get_event_loop().time() - start
    log.info("========== SUMMARY ==========")
    log.info(f"done: {done} | skipped: {skipped} | fails: {fails} | total: {total} | time: {elapsed/60:.1f} min")
    if fails_list:
        log.info(f"failed ids (first 50): {fails_list[:50]}")
    with open("oneshot_progress.json", "w") as f:
        json.dump({"ids": sorted(copied)}, f)
    progress_push("oneshot_progress.json")
    await client.disconnect()


asyncio.run(run())