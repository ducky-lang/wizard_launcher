## Wizard Launcher 2.0.2 · Legacy Blocks & Motion

1.16.5 resource packs now show doors, vines, fire, sounds and custom glyphs the way 1.16.5 did, and the launcher moves with a new, lightweight motion system.

### Legacy packs, read like 1.16.5

- **Doors and vines.** 1.20 redrew every door with new models, and 1.17 replaced the vine face models. When a pack draws iron doors, wooden doors or vines with the old models, the 1.16.5 block states come back, so the pack's own models are shown.
- **Soul fire, torches and lanterns.** 79 models were removed from the game after 1.16.5, including `door_bottom`, `fire_floor`, `torch_wall` and `hanging_lantern`. Any pack model built on one of them gets the 1.16.5 original, so custom shapes keep their form.
- **Sounds.** Door and chest events switched to new files in later versions. Replaced 1.16.5 sound files play again, and the renamed sweet berry event is moved to its new name.
- **Custom glyphs.** Characters drawn on the private use unicode pages (`unicode_page_e0` to `f8`) keep showing even though 1.20 dropped those pages. Glyph widths are measured from the pixels when `glyph_sizes.bin` is missing.
- **Player skins.** `steve.png` and `alex.png` are served from the folders 1.19.3 moved them to.

### Fixed

- **Map text no longer shows as `'''''`.** The castle pack redraws characters on `unicode_page_02`, which 1.16.5 read through its built-in unicode font and 1.20 no longer has, so those characters fell back to apostrophe-like marks. Every unicode page a pack overrides is now served again, for exactly the characters 1.16.5 took from it.
- **No more seeing through remodelled blocks.** The castle pack draws campfires and soul campfires as locked doors with glass tops. The modpack's MoreCulling treated those models as solid and hid the blocks next to them, so the world behind showed through. The launcher now keeps MoreCulling's model-based block culling off, so these blocks cull exactly like 1.16.5 and vanilla 1.20.1.

### A launcher that moves

- Pages glide in from the direction you travel, and the title follows.
- The sidebar and home art reveal on start, and the menu indicator and tabs move on springs.
- Cards lift with a light that follows the pointer.
- The Play button ripples when pressed, shines on hover and pulses when the game starts.
- Launch steps pop as they complete, progress and memory bars grow smoothly, and numbers roll to their values.
- Dialogs, menus, notifications (now with a timer bar) and the first-run guide move with direction.

Every animation runs once and uses only GPU-friendly properties, so nothing animates while the launcher is idle and the 2.0.1 performance gains stay. *Settings → Animations* and the system's reduced-motion setting turn motion off.

### Downloads

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `WizardLauncher-Windows-Setup-2.0.2.exe` |
| macOS (Apple silicon) | `WizardLauncher-macOS.dmg` |
| Linux (x86_64) | `WizardLauncher-Linux-x86_64.AppImage` |

Each installer includes its own Java runtime. Install over 2.0.1 to update; your worlds, accounts and settings are kept. Verify downloads against `SHA256SUMS.txt`.
