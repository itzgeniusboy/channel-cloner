# Memory — telegram-video-bot (Channel Cloner Userbot)

> Managed by opencode. This is your project's memory — edit it freely.

## Project
- **Goal:** Telethon userbot that clones any Telegram channel (all content as-is) to a destination
  channel via forward, runs 24/7 on GitHub Actions, buttons-only UI, retry button for failed items.
- **Last worked:** 2026-09-23

## Progress Log
- 2026-09-23: Created project. Wrote userbot.py (buttons: single / clone channel, destination
  setup, batch forward w/ 12s sleep, flood-safe, stop button, retry-failed button, duplicate-skip
  via progress.json, yt-dlp direct-URL download, gist session persistence). Added requirements.txt
  and .github/workflows/run.yml (cron every 6h, timeout 350min = 5h50m restart trick).

## Decisions
- Forward mode (keeps original sender) — user chose forward over copy.
- Buttons only, no commands (except /start welcome).
- Session kept as base64 in a GitHub gist; secrets (API_ID/HASH/PHONE) in GitHub Secrets, never in repo.
- progress.json tracks copied message IDs to avoid re-copying on restart.

## Next Steps
- User to create Telegram API credentials (my.telegram.org): API_ID, API_HASH.
- User to create GitHub repo + gist + add Secrets.
- Local Termux test: pip install -r requirements.txt && python userbot.py (login with phone+OTP).
- First GitHub run needs CODE secret (one-time OTP) OR run local once and push the gist session.

## Open Questions
- Repo public vs private (scheduled jobs free on public).
- Whether media-saver "single forward" also needs to post to destination channel (currently replies to sender).