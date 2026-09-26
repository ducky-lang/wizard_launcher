package dev.wizardlauncher.legacypacks.mixin;

import dev.wizardlauncher.legacy.LegacyTranslator;
import dev.wizardlauncher.legacypacks.Compat;
import net.minecraft.server.packs.repository.PackCompatibility;
import net.minecraft.util.InclusiveRange;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(PackCompatibility.class)
public abstract class PackCompatibilityMixin {
    @Inject(method = "forVersion", at = @At("HEAD"), cancellable = true, require = 0)
    private static void wizard$legacyIsCompatible(InclusiveRange<Integer> range, int current, CallbackInfoReturnable<PackCompatibility> cir) {
        if (current == Compat.TARGET_FORMAT && LegacyTranslator.needsTranslation(range.maxInclusive(), current)) {
            cir.setReturnValue(PackCompatibility.COMPATIBLE);
        }
    }
}
