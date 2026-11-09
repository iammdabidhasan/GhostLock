# Security

GhostLock is a **soft privacy screen**, not a security product. It does not replace Windows Lock, BitLocker or account passwords. Known limits: Task Manager (via `Ctrl+Alt+Del`), UAC prompts, restart and sign-out remain available while locked, and a process running as the same user can end GhostLock.

## Reporting a vulnerability
Please use **GitHub > Security > Report a vulnerability** (private reporting) on this repository instead of opening a public issue. Include the Windows version, GhostLock version, and steps to reproduce.

## Administrator helper
When upgrading, GhostLock only asks for administrator permission if it finds machine-wide leftovers of an older install. The elevated helper (`--legacy-admin-clean`) takes no file or path from the normal process: it performs its own scan and removes only items that match known GhostLock locations, never the program that is running.

## What is stored
- `%APPDATA%\GhostLock\config.json`: salted PBKDF2-SHA256 hashes of your passwords and recovery answer, plus your settings.
- `attempts.json`: times of recent failed unlock attempts (last 200).
- `intruders\`: webcam photos, only if you turned the intruder photo on. Local only, deleted automatically after 7/30/90 days or on demand.
- An error log.

## Network
GhostLock collects nothing. The only network request is the optional update check (off by default): one HTTPS request to the GitHub releases API, sending no personal data. Photos and logs are never uploaded.

## Camera
The intruder photo is opt-in and shows a consent prompt. Use it only on a PC you own. Photos are not encrypted; protect them with Windows account permissions or disk encryption (BitLocker).
