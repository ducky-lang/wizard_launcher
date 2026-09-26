package dev.wizardlauncher.legacypacks.mixin;

import dev.wizardlauncher.legacypacks.LegacyEffects;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.network.protocol.game.ClientboundUpdateMobEffectPacket;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ClientPacketListener.class)
public abstract class EffectPacketMixin {
    @Inject(method = "handleUpdateMobEffect", at = @At("RETURN"))
    private void wizard$keepLegacyAmplifier(ClientboundUpdateMobEffectPacket packet, CallbackInfo ci) {
        int legacy = LegacyEffects.signed(packet.getEffectAmplifier());
        LegacyEffects.trace("received amplifier " + packet.getEffectAmplifier() + " for entity " + packet.getEntityId());
        if (legacy >= 0 && legacy <= 255) {
            return;
        }
        if (!LegacyEffects.fromLocalWorld(((ClientPacketListener) (Object) this).getConnection().getRemoteAddress())) {
            LegacyEffects.trace("not from the local world, left as is");
            return;
        }
        Entity entity = Minecraft.getInstance().level == null ? null : Minecraft.getInstance().level.getEntity(packet.getEntityId());
        if (!(entity instanceof LivingEntity living)) {
            return;
        }
        MobEffectInstance effect = living.getEffect(packet.getEffect());
        if (effect == null) {
            LegacyEffects.trace("effect not found on the entity");
            return;
        }
        int modern = effect.getAmplifier();
        ((MobEffectInstanceAccessor) effect).wizard$setAmplifier(legacy);
        LegacyEffects.restored(effect.getDescriptionId(), legacy, modern);
    }
}
