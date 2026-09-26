package dev.wizardlauncher.legacypacks.mixin;

import dev.wizardlauncher.legacypacks.LegacyPacks;
import java.util.List;
import net.minecraft.server.packs.PackResources;
import net.minecraft.server.packs.repository.PackRepository;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(PackRepository.class)
public abstract class PackRepositoryMixin {
    @Inject(method = "openAllSelected", at = @At("RETURN"), cancellable = true)
    private void wizard$readLegacyPacks(CallbackInfoReturnable<List<PackResources>> cir) {
        cir.setReturnValue(LegacyPacks.wrap(cir.getReturnValue()));
    }
}
