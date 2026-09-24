package dev.wizardlauncher.legacypacks;

import dev.wizardlauncher.legacypacks.mixin.QuickPlayAccessor;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.GenericDirtMessageScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.network.chat.CommonComponents;
import net.minecraft.network.chat.Component;

public final class WaitingForWorldScreen extends GenericDirtMessageScreen {
    private static final long GIVE_UP_MS = 5 * 60_000L;
    private final String address;
    private final long started = System.currentTimeMillis();
    private CompletableFuture<Boolean> probe = CompletableFuture.completedFuture(false);
    private boolean done;

    public WaitingForWorldScreen(String address) {
        super(Component.translatableWithFallback("wizard.waiting.title", "Opening the castle gates..."));
        this.address = address;
    }

    @Override
    protected void init() {
        addRenderableWidget(Button.builder(CommonComponents.GUI_CANCEL, b -> {
            done = true;
            WorldGate.release();
            minecraft.setScreen(new TitleScreen());
        }).bounds(width / 2 - 100, height / 2 + 36, 200, 20).build());
    }

    @Override
    public void tick() {
        if (done) {
            return;
        }
        boolean timedOut = System.currentTimeMillis() - started > GIVE_UP_MS;
        if (probe.getNow(false) || timedOut) {
            done = true;
            WorldGate.release();
            QuickPlayAccessor.wizard$joinMultiplayerWorld(minecraft, address);
            return;
        }
        if (probe.isDone()) {
            probe = CompletableFuture.supplyAsync(() -> WorldGate.isOpen(address));
        }
    }

    @Override
    public void render(GuiGraphics graphics, int mouseX, int mouseY, float partialTick) {
        super.render(graphics, mouseX, mouseY, partialTick);
        long seconds = (System.currentTimeMillis() - started) / 1000;
        graphics.drawCenteredString(font, Component.translatableWithFallback("wizard.waiting.detail",
            "The world is still starting on this computer (%ss). You will join automatically.", seconds),
            width / 2, 90, 0xA0A0A0);
    }
}
