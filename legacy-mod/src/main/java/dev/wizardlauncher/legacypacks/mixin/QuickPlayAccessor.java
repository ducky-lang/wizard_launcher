package dev.wizardlauncher.legacypacks.mixin;

import net.minecraft.client.Minecraft;
import net.minecraft.client.quickplay.QuickPlay;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

@Mixin(QuickPlay.class)
public interface QuickPlayAccessor {
    @Invoker("joinMultiplayerWorld")
    static void wizard$joinMultiplayerWorld(Minecraft minecraft, String address) {
        throw new AssertionError();
    }
}
