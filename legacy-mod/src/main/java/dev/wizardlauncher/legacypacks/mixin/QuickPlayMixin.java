package dev.wizardlauncher.legacypacks.mixin;

import dev.wizardlauncher.legacypacks.LegacyPacks;
import dev.wizardlauncher.legacypacks.WaitingForWorldScreen;
import dev.wizardlauncher.legacypacks.WorldGate;
import net.minecraft.client.Minecraft;
import net.minecraft.client.quickplay.QuickPlay;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(QuickPlay.class)
public abstract class QuickPlayMixin {
    @Inject(method = "joinMultiplayerWorld", at = @At("HEAD"), cancellable = true)
    private static void wizard$waitForWorld(Minecraft minecraft, String address, CallbackInfo ci) {
        if (WorldGate.shouldWait(address)) {
            ci.cancel();
            LegacyPacks.LOGGER.info("Holding the one-click join to {} until the world on this computer is ready", address);
            minecraft.setScreen(new WaitingForWorldScreen(address));
        }
    }
}
