## Wizard Launcher 3.0.1 · Castle Fixes for 1.21.1

Conversations, the camera and the castle's own HUD now behave on Minecraft 1.21.1 exactly as they do on 1.20.1, and both versions switch to the lightweight FPS Modpack.

### Fixed on 1.21.1

- **Conversation choices no longer slide down on their own.** The castle holds you still during conversations with a 1.16.5 trick: levitation at strength 255, which 1.16.5 reads as "hover in place". Minecraft 1.20.5 and newer clamp effect strengths, so 1.21.1 turned it into a slow float. The map pulled you back every tick and read the drift as scrolling, so the selection kept moving down and the top choices could not be picked. Effects from the castle now keep their 1.16.5 strength, and you hover perfectly still again.
- **NPCs no longer bob up and down.** The bobbing was the camera being pulled back every tick by the same drift. It is gone with the fix above.
- **The castle's HUD shows correctly.** The castle hides the vanilla hotbar, hearts, hunger, experience and boss bar textures to draw its own. On 1.21.1 those hidden parts showed the vanilla graphics instead. They now stay hidden, so the castle's health bar, quest line, hotbar and Q/F prompts appear exactly as on 1.20.1.

### FPS Modpack

- **Both versions now run the [FPS Modpack](https://modrinth.com/modpack/fps)** (1.8 on 1.20.1, 2.6 on 1.21.1) instead of Fabulously Optimized: Sodium, Iris, ImmediatelyFast and a few engine optimisations, without the extra client mods that got in the castle's way. CustomSkinLoader is still included.
- Your mods, settings and worlds are kept. The previous modpack's mods are removed and the new ones are downloaded on the next Play.

### Downloads

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `WizardLauncher-Windows-Setup-3.0.1.exe` |
| macOS (Apple silicon) | `WizardLauncher-macOS.dmg` |
| Linux (x86_64) | `WizardLauncher-Linux-x86_64.AppImage` |

Each installer includes its own Java runtime. Install over 3.0.0 or 2.0.2 to update; your worlds, accounts and settings are kept. Verify downloads against `SHA256SUMS.txt`.
