package dev.wizardlauncher.legacypacks;

import net.minecraft.client.gui.screens.GenericDirtMessageScreen;
import net.minecraft.network.chat.Component;

abstract class MessageScreen extends GenericDirtMessageScreen {
    MessageScreen(Component title) {
        super(title);
    }
}
