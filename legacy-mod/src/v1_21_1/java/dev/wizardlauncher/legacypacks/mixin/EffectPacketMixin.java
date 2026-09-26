package dev.wizardlauncher.legacypacks.mixin;

import dev.wizardlauncher.legacypacks.LegacyEffects;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.network.protocol.game.ClientboundUpdateMobEffectPacket;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ClientPacketListener.class)
public abstract class EffectPacketMixin {
    @Inject(method = "handleUpdateMobEffect", at = @At("HEAD"))
    private void wizard$keepLegacyAmplifier(ClientboundUpdateMobEffectPacket packet, CallbackInfo ci) {
        if (Minecraft.getInstance().isSameThread()) {
            LegacyEffects.expect(((ClientPacketListener) (Object) this).getConnection().getRemoteAddress(), packet.getEffectAmplifier());
        }
    }

    @Inject(method = "handleUpdateMobEffect", at = @At("RETURN"))
    private void wizard$done(ClientboundUpdateMobEffectPacket packet, CallbackInfo ci) {
        if (Minecraft.getInstance().isSameThread()) {
            LegacyEffects.clear();
        }
    }
}
