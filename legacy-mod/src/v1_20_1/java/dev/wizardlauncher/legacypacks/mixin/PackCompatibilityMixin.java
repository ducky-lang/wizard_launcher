package dev.wizardlauncher.legacypacks.mixin;

import dev.wizardlauncher.legacy.LegacyTranslator;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.repository.PackCompatibility;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(PackCompatibility.class)
public abstract class PackCompatibilityMixin {
    @Inject(method = "forFormat", at = @At("HEAD"), cancellable = true, require = 0)
    private static void wizard$legacyIsCompatible(int format, PackType type, CallbackInfoReturnable<PackCompatibility> cir) {
        if (type == PackType.CLIENT_RESOURCES && LegacyTranslator.needsTranslation(format)) {
            cir.setReturnValue(PackCompatibility.COMPATIBLE);
        }
    }
}
