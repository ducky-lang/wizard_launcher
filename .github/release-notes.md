## Wizard Launcher 2.0.0

A ground-up rewrite in Java and Kotlin with a new Chromium-based interface, native support for 1.16.5 resource packs on Minecraft 1.20.1, and an install that keeps working offline.

### Highlights

- **New interface.** Home screen with live launch steps, a library for mods, resource packs and shaders, screenshots, world backups, a live console, crash reports and guided first-run setup, all with smooth animations.
- **Legacy packs, unconverted.** 1.16.5 resource packs load directly in 1.20.1. The files on disk are never rewritten, and custom block-state rules can be added with `wizard-states.json`.
- **Offline first.** After one online launch, everything runs without a connection. Missing or damaged game files are detected and repaired on the next Play.
- **Lighter server.** The vanilla 1.16.5 server and the version bridge share a single JVM with tuned memory profiles.
- **Secure by default.** Signed catalog, pinned downloads, tokens kept out of process arguments, credentials in the system keychain and an interface with no network access.
- **Multiple accounts.** Microsoft and offline profiles side by side.

### Fixed

- "A game file is missing: Users" when pressing Play on Windows and macOS.

### Downloads

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `WizardLauncher-Windows-Setup-2.0.0.exe` |
| macOS (Apple silicon) | `WizardLauncher-macOS.dmg` |
| Linux (x86_64) | `WizardLauncher-Linux-x86_64.AppImage` |

Each installer includes its own Java runtime. Verify downloads against `SHA256SUMS.txt`.
