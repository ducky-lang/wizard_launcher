package dev.wizardlauncher.legacypacks;

import dev.wizardlauncher.legacy.Overlay;
import java.util.concurrent.CompletableFuture;
import net.minecraft.server.packs.PackResources;

public final class LegacyPackResources extends OverlayPackResources {
    LegacyPackResources(PackResources delegate, CompletableFuture<Overlay> pending) {
        super(delegate, pending);
    }

    @Override
    public String packId() {
        return delegate.packId();
    }

    @Override
    public boolean isBuiltin() {
        return delegate.isBuiltin();
    }
}
