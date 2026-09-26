package dev.wizardlauncher.legacypacks;

import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.PackResources;

public final class Compat {
    public static final int TARGET_FORMAT = 15;

    private Compat() {
    }

    static ResourceLocation location(String namespace, String path) {
        return new ResourceLocation(namespace, path);
    }

    static boolean isBuiltin(PackResources pack) {
        return pack.isBuiltin();
    }
}
