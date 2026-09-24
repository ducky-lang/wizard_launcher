## Wizard Launcher 2.0.1 · Performance Update

Faster Play, a lighter launcher, and legacy resource packs that load reliably and stay out of the game's way.

### Faster Play

- **The world and Minecraft start together.** The world server now boots while Minecraft loads instead of before it. Minecraft waits on an *Opening the castle gates…* screen and joins the moment the world is ready, with no second click. If the world fails to start, the game is closed and the error is shown in the launcher.
- **Resource packs are cached.** A 1.16.5 pack is adapted once, then cached in memory and on disk. The cache is keyed by the pack's files and the adapter version, so later launches reuse it and any change to the pack is picked up automatically.

### Legacy packs that just work

- **Adaptation runs in the background.** It used to run on the game's main thread, which could hold back network replies long enough for the world to drop the player with *Timed out* on large packs such as the castle's 10,000-file pack. It now runs on a background thread.
- **A single bad file no longer breaks the pack.** A file the game lists but cannot read used to stop the whole pack from being adapted, so it loaded unchanged. That file is now skipped with a note in the pack report, and the rest of the pack is adapted.

### A lighter launcher

- **No more heavy effects.** The full-screen particle canvas, the blurred aurora, the frosted-glass panels and the looping glow effects are gone. The window no longer redraws on every frame while idle, so it uses far less CPU and GPU. Page transitions and small, GPU-friendly motion stay.
- **The console keeps up.** It receives log lines in batches instead of one call per line, so heavy output no longer stutters the window.
- **New home banner.** It now shows the castle from the map's own resource pack.

### Downloads

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `WizardLauncher-Windows-Setup-2.0.1.exe` |
| macOS (Apple silicon) | `WizardLauncher-macOS.dmg` |
| Linux (x86_64) | `WizardLauncher-Linux-x86_64.AppImage` |

Each installer includes its own Java runtime. Install over 2.0.0 to update; your worlds, accounts and settings are kept. Verify downloads against `SHA256SUMS.txt`.
