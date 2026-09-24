package dev.wizardlauncher.legacypacks;

import dev.wizardlauncher.legacy.Overlay;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.PackResources;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.metadata.MetadataSectionSerializer;
import net.minecraft.server.packs.resources.IoSupplier;

public final class LegacyPackResources implements PackResources {
    private final PackResources delegate;
    private final Overlay overlay;

    LegacyPackResources(PackResources delegate, Overlay overlay) {
        this.delegate = delegate;
        this.overlay = overlay;
    }

    private static IoSupplier<InputStream> bytes(byte[] data) {
        return () -> new ByteArrayInputStream(data);
    }

    private static String full(ResourceLocation loc) {
        return "assets/" + loc.getNamespace() + "/" + loc.getPath();
    }

    @Override
    public IoSupplier<InputStream> getRootResource(String... elements) {
        if (elements.length == 1 && elements[0].equals("pack.mcmeta") && overlay.file("pack.mcmeta") != null) {
            return bytes(overlay.file("pack.mcmeta"));
        }
        return delegate.getRootResource(elements);
    }

    @Override
    public IoSupplier<InputStream> getResource(PackType type, ResourceLocation location) {
        if (type == PackType.CLIENT_RESOURCES) {
            String path = full(location);
            byte[] data = overlay.file(path);
            if (data != null) {
                return bytes(data);
            }
            String alias = overlay.aliasOf(path);
            if (alias != null) {
                ResourceLocation source = ResourcesView.location(alias);
                if (source != null) {
                    return delegate.getResource(type, source);
                }
            }
        }
        return delegate.getResource(type, location);
    }

    @Override
    public void listResources(PackType type, String namespace, String path, ResourceOutput output) {
        if (type != PackType.CLIENT_RESOURCES) {
            delegate.listResources(type, namespace, path, output);
            return;
        }
        Set<ResourceLocation> seen = new HashSet<>();
        delegate.listResources(type, namespace, path, (loc, supplier) -> {
            seen.add(loc);
            byte[] data = overlay.file(full(loc));
            output.accept(loc, data != null ? bytes(data) : supplier);
        });
        String prefix = "assets/" + namespace + "/" + path + "/";
        for (Map.Entry<String, byte[]> e : overlay.files().entrySet()) {
            if (e.getKey().startsWith(prefix)) {
                ResourceLocation loc = ResourcesView.location(e.getKey());
                if (loc != null && seen.add(loc)) {
                    output.accept(loc, bytes(e.getValue()));
                }
            }
        }
        for (Map.Entry<String, String> e : overlay.aliases().entrySet()) {
            if (e.getKey().startsWith(prefix)) {
                ResourceLocation loc = ResourcesView.location(e.getKey());
                ResourceLocation source = ResourcesView.location(e.getValue());
                if (loc != null && source != null && seen.add(loc)) {
                    IoSupplier<InputStream> supplier = delegate.getResource(type, source);
                    if (supplier != null) {
                        output.accept(loc, supplier);
                    }
                }
            }
        }
    }

    @Override
    public Set<String> getNamespaces(PackType type) {
        Set<String> out = new TreeSet<>(delegate.getNamespaces(type));
        if (type == PackType.CLIENT_RESOURCES) {
            for (String path : overlay.files().keySet()) {
                ResourceLocation loc = ResourcesView.location(path);
                if (loc != null) {
                    out.add(loc.getNamespace());
                }
            }
        }
        return out;
    }

    @Override
    public <T> T getMetadataSection(MetadataSectionSerializer<T> serializer) throws IOException {
        return delegate.getMetadataSection(serializer);
    }

    @Override
    public String packId() {
        return delegate.packId();
    }

    @Override
    public boolean isBuiltin() {
        return delegate.isBuiltin();
    }

    @Override
    public void close() {
        delegate.close();
    }
}
