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
- 2026-09-23: Login done on Termux (account L359D, +917878086689). 2FA was on, disabled it.
  Sign-in via login_helper.py (phone_code_hash flow; code 80898 was reused/valid). Added .env
  auto-load, .gitignore for *.session/.env/config/progress/logs. Bot running in background (PID noted).
- 2026-09-23: User tested bot ("done") — media/buttons working. Next is GitHub 24x7 deploy.

## Decisions
- Forward mode (keeps original sender) — user chose forward over copy.
- Buttons only, no commands (except /start welcome).
- Session kept as base64 in a GitHub gist; secrets (API_ID/HASH/PHONE) in GitHub Secrets, never in repo.
- progress.json tracks copied message IDs to avoid re-copying on restart.
- Credentials stored in .env (gitignored) for local Termux runs.

## Next Steps
- GitHub deploy: create repo (public preferred for free scheduled jobs) + push code (session gitignored).
- Create private gist + PAT (gist scope) → set Secrets: API_ID, API_HASH, PHONE, GIST_TOKEN, GIST_ID.
- Session persists via gist (gist_push on login, gist_pull on start) — no OTP on GitHub restarts.
- Optional: add session.b64 to gist from local now so first GitHub run is automatic.

## Open Questions
- Repo public vs private (scheduled jobs free on public).
- Whether media-saver "single forward" also needs to post to destination channel (currently replies to sender).