# Brand assets

## `floo-logo.png`

Drop the logo here and it becomes the launcher's mark everywhere: the title
bar, the About dialog, and the Windows/macOS/Linux application icon.

**Requirements**

| | |
|---|---|
| Format | PNG, 8-bit, non-interlaced |
| Size | square, 512×512 or larger (non-square is padded, not stretched) |
| Background | transparent |

**After adding or changing it**

```bash
python tools/make_icon.py
```

That regenerates `installer/app.ico`, `installer/linux/app.png` and
`installer/macos/app.iconset`, and copies the file to
`launcher_core/data/floo-logo.png` — which is the copy a packaged build
actually ships, because PyInstaller is told to collect `launcher_core`'s data
and knows nothing about this folder.

**If the file is missing**, the launcher falls back to the mark it draws in
code (`ui/brand.py:drawn_mark`). That is deliberate: a build without the asset
shows a real logo rather than an empty box, which would read as a broken
install.
