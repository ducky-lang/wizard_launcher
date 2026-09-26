package dev.wizardlauncher.legacypacks.mixin;

import dev.wizardlauncher.legacypacks.LegacyRiding;
import net.minecraft.world.entity.Entity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(Entity.class)
public abstract class RidingMixin {
    @Inject(method = "positionRider(Lnet/minecraft/world/entity/Entity;Lnet/minecraft/world/entity/Entity$MoveFunction;)V", at = @At("HEAD"), cancellable = true)
    private void wizard$legacyRiderPosition(Entity rider, Entity.MoveFunction move, CallbackInfo ci) {
        Entity vehicle = (Entity) (Object) this;
        if (vehicle.hasPassenger(rider) && LegacyRiding.applies(vehicle)) {
            move.accept(rider, vehicle.getX(), LegacyRiding.riderY(vehicle, rider), vehicle.getZ());
            ci.cancel();
        }
    }
}
