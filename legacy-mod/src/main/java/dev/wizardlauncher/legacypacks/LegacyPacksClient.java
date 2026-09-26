package dev.wizardlauncher.legacypacks;

import net.fabricmc.api.ClientModInitializer;

public final class LegacyPacksClient implements ClientModInitializer {
    @Override
    public void onInitializeClient() {
        LegacyPacks.LOGGER.info("Wizard Legacy Packs active: packs for pack_format {}-{} are read natively",
            dev.wizardlauncher.legacy.LegacyTranslator.OLDEST_FORMAT, Compat.TARGET_FORMAT - 1);
    }
}
