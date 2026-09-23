import asyncio, os, time
from telethon import TelegramClient

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
PHONE = os.environ.get("PHONE", "")
SESSION = os.environ.get("SESSION", "cloner") + ".session"
CODE_FILE = os.environ.get("CODE_FILE", "/data/data/com.termux/files/usr/tmp/opencode/otp.txt")


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH, flood_sleep_threshold=30)
    await client.connect()
    if await client.is_user_authorized():
        print("ALREADY_AUTHORIZED")
        await client.disconnect()
        return
    req = await client.send_code_request(PHONE)
    print("CODE_REQUESTED", req.phone_code_hash, flush=True)
    print("WAITING_FOR_OTP", flush=True)
    if os.path.exists(CODE_FILE):
        os.remove(CODE_FILE)
    deadline = time.time() + 180
    while time.time() < deadline:
        if os.path.exists(CODE_FILE):
            code = open(CODE_FILE).read().strip()
            if code:
                try:
                    await client.sign_in(PHONE, code, phone_code_hash=req.phone_code_hash)
                    print("SIGN_IN_OK", flush=True)
                    break
                except Exception as e:
                    print("SIGN_IN_FAIL", type(e).__name__, str(e), flush=True)
                    break
        await asyncio.sleep(2)
    else:
        print("OTP_TIMEOUT", flush=True)
    await client.disconnect()


asyncio.run(main())