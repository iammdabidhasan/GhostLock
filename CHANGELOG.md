# Changelog

## 2.2.0
**Design**
- New **hexagon shield** lock animation: a grid forms from the ghost, covers the screen, shimmers and fades away. Four patterns (Ripple, Sweep, Scan down, Random), three colors, 1-5 s, a Preview button, and an off switch in Settings.

**Privacy**
- Optional **intruder photo**: a webcam snapshot on wrong passwords (off by default, consent prompt, stored only on your PC, auto-deleted, max 5 per lock). Includes a *Test camera* button.
- **Activity log** of failed attempts and a "while you were away" report after unlocking.

**Convenience**
- Global lock hotkey (default `Ctrl+Alt+L`, presets, reports if another program owns it).
- Automatic lock after inactivity with a warning, and optional lock on wake from sleep.
- Custom message in the password box.
- Optional weekly update check (the only network request, off by default) and a "Check for updates" button in About.

**Security and hardening (from an independent code review)**
- Fixed: with a second monitor to the left of or above the main one, the lock cover could miss the main screen (negative window positions were written wrongly).
- Passwords are checked at setup, change and settings so you can never save one the lock screen cannot type (over 128 characters, tabs or line breaks).
- New password hashes use 600,000 PBKDF2 rounds; existing passwords keep working.
- The lock keeps keyboard focus even when started by the hotkey, idle timer or wake-up, and a failure while building the lock screen can no longer leave a stuck cover behind.
- A hand-edited config with bad numbers or "false" text no longer wipes your settings; the config file is flushed to disk when saved.
- Renamed installers (`Setup (1).exe`, `GhostLock-Setup-2.2.0.exe`) are recognised, the single-instance check is reliable, and PowerShell paths with curly quotes work.
- Uninstall reports it if the program folder could not be scheduled for removal; cleanup tools read non-English Windows output correctly.
- Intruder photos: no more missed first photo while the camera library loads, one shared attempt log, bounded waiting. Update-check dialogs never appear behind the lock screen.

**Reliability**
- Fixed: after installing or uninstalling, the window's Finish / Close button was squeezed out of view (only the title-bar X remained). The window now resizes to fit, shows a clear button, and closes itself after a few seconds.
- Fixed: the desktop and Start menu shortcuts are now created in the folders Windows really uses (including a OneDrive-redirected Desktop), and Setup tells you if one could not be created.
- Exit now asks first and says how to start GhostLock again. Starting GhostLock while it is already running brings the ghost back with its menu instead of showing an error.
- Fixed: uninstalling (and Finish in Setup) could show "Failed to execute script pyi_rth_inspect". Child copies of the exe now unpack their own files instead of using the parent's temp folder.
- Camera build now explicitly bundles OpenCV's binaries; a load failure is written to the error log.
- A watchdog releases the lock if the app's UI ever freezes while locked (sleep is not counted as a freeze).
- Auto-lock no longer fires while a full-screen video, game or presentation is running.
- The shortcut reacts instantly (checked several times a second) and the camera module loads in the background, so the first photo is not delayed.
- `GhostLock.exe --selftest` reports what a build contains. `build.ps1` runs it and stops if the camera module is missing.
- Error log is capped (one previous log kept). `requirements.txt` lists the optional camera dependency.
- Uninstall with "delete my data" now also removes the activity log and intruder photos.
- CI tests and a tag-triggered release workflow (build, SHA-256 checksum, optional code signing).
- `build.ps1 -NoCamera` for a smaller build.

## 2.1.1
**New logo**
- Fresh, smooth ghost (round dome, scalloped hem, oval eyes, teal-to-blue gradient) that stays clear on white and dark backgrounds and at 16 px. All icons, the .ico and the README screenshots were regenerated.

**Upgrade and cleanup**
- Fixed: after installing a new build, "Start with Windows" could launch the *old* version. A stale install is no longer preferred over the build you are running, and GhostLock repairs its own startup entry on every start.
- New smart upgrade: Setup (or a new `GhostLock.exe` started outside the install folder) scans for older GhostLock leftovers: running programs, Run-key entries (user and machine), startup shortcuts, scheduled tasks, old install folders, shortcuts and Apps entries.
- Asks permission and lists exactly what will be removed. Optional **Start fresh** removes old passwords and settings so the setup wizard opens clean.
- Administrator permission is requested once, only when machine-wide items exist. The elevated helper re-scans on its own and trusts nothing from the calling process.
- Setup keeps your existing "Start with Windows" choice and stops a running old copy instead of failing.
- 17 new automated tests (fake Windows, so they run anywhere).

## 2.1.0
**Design**
- New backgroundless ghost logo ("ghost fading into data"), crisp at every size.
- Lock screen shows only a transparent password capsule (no logo, no blur). Messages appear inside it.
- The password capsule fades out completely after inactivity (default 15 s, adjustable) and returns on any mouse move or key press.
- Friendlier setup wizard (welcome, password, backup password, recovery question) and tabbed Settings.
- Dark, consistent UI with DPI-aware sizing.

**Features**
- Double-click the ghost to lock instantly (can be turned off).
- Uninstall from the ghost menu (password required), Settings > Apps, or the Start Menu.
- Start with Windows now uses the per-user Run registry key.

**Security and fixes**
- Escalating cool-down after repeated wrong passwords or recovery answers.
- Recovery now lets you set a new password without the old one.
- Re-running `--setup` on an existing install asks for your password first.
- Alt+F4 can no longer close the lock screen; the floating button is hidden while locked.
- Settings save all-or-nothing; corrupt config is moved aside instead of overwritten.
- Errors are logged to `%APPDATA%\GhostLock\ghostlock.log`.

## 2.0.0
- Single-click floating icon, transparent lock overlay, installer and recovery.
