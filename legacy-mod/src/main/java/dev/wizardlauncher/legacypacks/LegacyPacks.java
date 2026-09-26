package dev.wizardlauncher.legacypacks;

import dev.wizardlauncher.legacy.LegacyTranslator;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.server.packs.PackResources;
import net.minecraft.server.packs.resources.IoSupplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class LegacyPacks {
    public static final Logger LOGGER = LoggerFactory.getLogger("WizardLegacyPacks");

    private LegacyPacks() {
    }

    public static List<PackResources> wrap(List<PackResources> packs) {
        List<PackResources> out = new ArrayList<>(packs.size());
        boolean changed = false;
        for (PackResources pack : packs) {
            PackResources wrapped = wrapOne(pack);
            changed |= wrapped != pack;
            out.add(wrapped);
        }
        return changed ? List.copyOf(out) : packs;
    }

    private static PackResources wrapOne(PackResources pack) {
        if (Compat.isBuiltin(pack) || pack instanceof LegacyPackResources) {
            return pack;
        }
        try {
            IoSupplier<InputStream> meta = pack.getRootResource("pack.mcmeta");
            if (meta == null) {
                return pack;
            }
            int format;
            try (InputStream in = meta.get()) {
                format = LegacyTranslator.readFormat(in.readAllBytes());
            }
            if (!LegacyTranslator.needsTranslation(format, Compat.TARGET_FORMAT)) {
                return pack;
            }
            ResourcesView view = new ResourcesView(pack);
            if (view.namespaces().isEmpty()) {
                return pack;
            }
            return new LegacyPackResources(pack, OverlayCache.load(pack, view, format, extraRules()));
        } catch (Exception e) {
            LOGGER.error("Could not adapt '{}'; loading it unchanged", pack.packId(), e);
            return pack;
        }
    }

    private static List<String> extraRules() {
        Path file = FabricLoader.getInstance().getConfigDir().resolve("wizard-states.json");
        if (!Files.isRegularFile(file)) {
            return List.of();
        }
        try {
            return List.of(Files.readString(file, StandardCharsets.UTF_8));
        } catch (Exception e) {
            LOGGER.warn("Ignoring unreadable {}", file, e);
            return List.of();
        }
    }
}
