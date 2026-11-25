# Changelog

## [1.2.3] — 2026-11-25
- Added live account connection status (`google_accounts.json` email / OAuth session / Gemini API key detection) to `/api/status` and the Ingress Web UI banner.
- Added `/api/logout` endpoint and **Abmelden** button in the Ingress Web UI to disconnect and clear persisted session credentials.

## [1.2.2] — 2026-11-23
- Fixed OSC-8 hyperlink extraction so the Ingress Web UI provides a clean single-`scope` Google OAuth login link.

## [1.2.1] — 2026-11-23
- Added Remote SSH OAuth PTY environment (`TERM`, `SSH_CONNECTION`, wide `TIOCSWINSZ`) and clickable Google login button in Ingress Web UI.

## [1.2.0] — 2026-11-23
- Initial Home Assistant App release running Google Antigravity CLI (`agy`) and an OpenAI-compatible `/v1` bridge directly on Home Assistant OS with persistent `/data` storage.
