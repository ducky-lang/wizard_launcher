package dev.wizardlauncher.legacypacks;

import dev.wizardlauncher.legacy.PackView;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import net.minecraft.ResourceLocationException;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.PackResources;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.resources.IoSupplier;

final class ResourcesView implements PackView {
    private final PackResources pack;

    ResourcesView(PackResources pack) {
        this.pack = pack;
    }

    static ResourceLocation location(String path) {
        if (!path.startsWith("assets/")) {
            return null;
        }
        int slash = path.indexOf('/', 7);
        if (slash < 0) {
            return null;
        }
        try {
            return new ResourceLocation(path.substring(7, slash), path.substring(slash + 1));
        } catch (ResourceLocationException e) {
            return null;
        }
    }

    private IoSupplier<InputStream> supplier(String path) {
        if (path.equals("pack.mcmeta") || path.equals("wizard-states.json")) {
            return pack.getRootResource(path);
        }
        ResourceLocation loc = location(path);
        return loc == null ? null : pack.getResource(PackType.CLIENT_RESOURCES, loc);
    }

    @Override
    public boolean exists(String path) {
        return supplier(path) != null;
    }

    @Override
    public byte[] read(String path) throws IOException {
        IoSupplier<InputStream> s = supplier(path);
        if (s == null) {
            throw new IOException("not in pack: " + path);
        }
        try (InputStream in = s.get()) {
            return in.readAllBytes();
        }
    }

    @Override
    public Set<String> namespaces() {
        return new TreeSet<>(pack.getNamespaces(PackType.CLIENT_RESOURCES));
    }

    @Override
    public List<String> list(String namespace, String directory) {
        List<String> out = new ArrayList<>();
        pack.listResources(PackType.CLIENT_RESOURCES, namespace, directory,
            (loc, supplier) -> out.add("assets/" + loc.getNamespace() + "/" + loc.getPath()));
        return out;
    }
}
