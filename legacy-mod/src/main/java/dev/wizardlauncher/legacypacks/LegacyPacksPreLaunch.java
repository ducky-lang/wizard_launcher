package dev.wizardlauncher.legacypacks;

import net.fabricmc.loader.api.entrypoint.PreLaunchEntrypoint;

public final class LegacyPacksPreLaunch implements PreLaunchEntrypoint {
    @Override
    public void onPreLaunch() {
        if (System.getProperty("java.awt.headless") == null) {
            System.setProperty("java.awt.headless", "true");
        }
    }
}
