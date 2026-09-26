## Wizard Launcher 3.0.0 · Two Versions & Modpacks

The castle can now be played on Minecraft 1.21.1 as well as 1.20.1, Modrinth modpacks can be imported, the resource pack download is reliable, and the first launch is much faster.

### Minecraft 1.21.1

- **Pick your version beside Play.** *Castle · 1.20.1* and *Castle · 1.21.1* are separate installations, each with its own mods, settings, resource packs, shaders and screenshots. Your current 1.20.1 installation is kept exactly as it is.
- **1.21.1 runs Fabulously Optimized 6** (Sodium 0.6) with CustomSkinLoader, and joins the same castle through the bundled bridge.
- **Java 21 is handled for you.** The first time you play 1.21.1, the launcher downloads Mojang's own Java 21 runtime once, verifying every file against Mojang's manifest. A Java 21 set in Settings is used instead.
- **Legacy packs work on 1.21.1 too.** The pack reader mod now ships for both versions. On 1.21.1 it also cuts the old GUI sheets (`widgets.png`, `icons.png`, containers…) into the 381 sprites 1.20.2 introduced, at the pack's own resolution with vanilla's nine-slice data, and serves `grass` and `scute` under their new names `short_grass` and `turtle_scute`.
- **Your settings come along.** A new installation starts with the controls and video settings of the one you played last.
- Game libraries and assets are shared, so the second version only downloads what differs.

### Modpacks

- **Import a Modrinth modpack (`.mrpack`)** from the version menu (or *Game → Import a modpack* in the classic window). It becomes a new installation that joins the castle like the built-in ones, and can be removed again from the same menu.
- Fabric modpacks only. Forge, NeoForge, Quilt and CurseForge packs are refused with a clear explanation, and every file must come from Modrinth, GitHub or GitLab with a SHA-512 fingerprint.

### Fixed

- **The resource pack now updates.** The launcher used to keep the first pack it downloaded forever. It now compares the cached copy with the fingerprint Hugging Face publishes and downloads the pack again when it changed.
- **Resource pack problems are reported.** A failed pack download was skipped silently, so the game started without it. You now get a clear message, a file missing from Hugging Face is named, and the copy already on your computer is used when the server cannot be reached.
- **A broken map upload is caught.** A map zip without `level.dat` is refused with an explanation instead of installing an empty world.

### Faster

- The castle and resource pack download **at the same time** as Minecraft, Fabric, the modpack and Java.
- Modpack mods download in parallel, and game files use more connections on faster machines.
- The resource pack is installed as its zip, hard-linked from the cache, instead of unpacking and copying thousands of files.
- On Java 21 the game keeps a class-data-sharing archive, so every start after the first loads faster.

### Command line

- `--instance <id>` for `--play`, `--verify-install` and `--smoke-client`.
- `--list-instances` and `--import-modpack <file.mrpack>`.
- `--export-pack … --target 1.21.1` writes a 1.21.1 copy of an older pack.

### Downloads

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `WizardLauncher-Windows-Setup-3.0.0.exe` |
| macOS (Apple silicon) | `WizardLauncher-macOS.dmg` |
| Linux (x86_64) | `WizardLauncher-Linux-x86_64.AppImage` |

Each installer includes its own Java runtime. Install over 2.0.2 to update; your worlds, accounts, settings and 1.20.1 installation are kept. Verify downloads against `SHA256SUMS.txt`.
