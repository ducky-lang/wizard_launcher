package dev.wizardlauncher.legacypacks;

import net.minecraft.client.gui.screens.GenericMessageScreen;
import net.minecraft.network.chat.Component;

abstract class MessageScreen extends GenericMessageScreen {
    MessageScreen(Component title) {
        super(title);
    }
}
