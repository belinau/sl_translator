# App auto-reloads — do NOT restart after edits

The translation editor (`main.py`) is run by launchd (`com.bel.translator`,
plist `~/Library/LaunchAgents/com.bel.translator.plist`) **with uvicorn
watchfiles auto-reload enabled**. Editing any watched source file
(`main.py`, `ui/*.py`, `translate_core/*.py`, `config.py`, `tests/*.py`, …)
triggers an automatic worker reload — no restart needed.

## Rules

- After editing source files, **DO NOT** `launchctl kickstart -k` the app.
  The reload is automatic; a manual restart just kills the user's active
  editor session for no reason.
- Only restart via `launchctl kickstart -k gui/$(id -u)/com.bel.translator`
  when the process is actually **dead/crashed/not listening on :8080**, or
  when an edit touched something watchfiles doesn't pick up (rare).
- After an edit, wait for the reload to settle (watch the log for the next
  `NiceGUI ready to go on http://localhost:8080` line in
  `logs/launchd.err.log` / `launchd.out.log`) before asking the user to test.
- Data-file writes (`data/knowledge.db`, `data/projects/*.json`) do **not**
  trigger reloads — only source files do. So debounced `kg.save` is safe.

## Verifying a code change took effect
- Check the log tail for the `WatchFiles detected changes in '<file>'`
  + subsequent `NiceGUI ready to go` line.
- Ask the user to **reload their browser tab** (the WebSocket reconnects to
  the new worker) — do not assume the old tab is live.