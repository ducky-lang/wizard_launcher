package dev.wizardlauncher.legacypacks.mixin;

import dev.wizardlauncher.legacypacks.LegacyEffects;
import net.minecraft.world.effect.MobEffectInstance;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Mutable;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MobEffectInstance.class)
public abstract class MobEffectInstanceMixin {
    @Mutable
    @Shadow
    private int amplifier;

    @Inject(method = "<init>*", at = @At("RETURN"))
    private void wizard$legacyAmplifier(CallbackInfo ci) {
        Integer raw = LegacyEffects.take();
        if (raw != null) {
            this.amplifier = LegacyEffects.signed(raw);
        }
    }
}
