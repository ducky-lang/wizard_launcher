"""Single source of truth for the launcher's identity and version.

Bump VERSION here; the About dialog, the log header, the update check and
the Inno Setup script all read from this one place.
"""

VERSION = "1.0.1+2"
EDITION = "Beta Version"
VERSION_STRING = f"v{VERSION} - {EDITION}"

APP_NAME = "Wizard Launcher"
APP_AUTHOR = "Foxy"
APP_AUTHOR_HANDLE = "@.phungminh (unoffical launcher)"

MAP_CREDIT = "The Floo Network"
MAP_CREDIT_URL = "https://www.thefloonetwork.net/"

USER_AGENT = f"WizardLauncher/{VERSION} (+https://github.com/foxy/wizard-launcher)"

# Optional: a plain-text or JSON endpoint that serves the latest version
# string. Left empty means "no update check" - the launcher silently skips
# it rather than pestering the user with a failed request on every start.
UPDATE_MANIFEST_URL = ""

ABOUT_TEXT = (
    "Wizard Launcher exists so that stepping into Witchcraft and Wizardry takes "
    "one click. No Java to install, no mods to sideload, "
    "no server console to babysit - just press play and walk into the castle.\n\n"
    "It was built by one person, for friends who wanted to see Hogwarts without "
    "fighting a config file first."
)

CREDITS = [
    ("Original map", "Witchcraft and Wizardry by The Floo Network"),
    ("Launcher", f"{APP_AUTHOR} ({APP_AUTHOR_HANDLE})"),
    ("Version bridging", "ViaProxy by RaphiMC"),
    ("Modpack", "Fabulously Optimized, via Modrinth"),
    ("Skins", "CustomSkinLoader by xfl03"),
    ("Typefaces", "Cinzel and Poppins, via Google Fonts"),
]

LEGAL_NOTICE = (
    "Wizard Launcher is a fan project. It is not affiliated with, endorsed by, or "
    "associated with Mojang, Microsoft, or Warner Bros. Minecraft is a trademark of "
    "Mojang AB. You must own a legitimate copy of Minecraft to play. No game files "
    "are redistributed by this launcher."
)
