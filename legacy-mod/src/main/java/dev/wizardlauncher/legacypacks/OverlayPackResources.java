package dev.wizardlauncher.legacypacks;

import dev.wizardlauncher.legacy.Overlay;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.CompletableFuture;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.PackResources;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.metadata.MetadataSectionSerializer;
import net.minecraft.server.packs.resources.IoSupplier;

abstract class OverlayPackResources implements PackResources {
    protected final PackResources delegate;
    private final CompletableFuture<Overlay> pending;
    private volatile Overlay overlay;
    private volatile boolean failed;

    OverlayPackResources(PackResources delegate, CompletableFuture<Overlay> pending) {
        this.delegate = delegate;
        this.pending = pending;
    }

    private Overlay overlay() {
        Overlay o = overlay;
        if (o != null || failed) {
            return o;
        }
        try {
            o = pending.join();
            overlay = o;
            return o;
        } catch (RuntimeException e) {
            synchronized (this) {
                if (!failed) {
                    failed = true;
                    LegacyPacks.LOGGER.error("Could not adapt '{}'; loading it unchanged", delegate.packId(), e.getCause() != null ? e.getCause() : e);
                }
            }
            return null;
        }
    }

    private static IoSupplier<InputStream> bytes(byte[] data) {
        return () -> new ByteArrayInputStream(data);
    }

    private static String full(ResourceLocation loc) {
        return "assets/" + loc.getNamespace() + "/" + loc.getPath();
    }

    @Override
    public IoSupplier<InputStream> getRootResource(String... elements) {
        if (elements.length == 1 && elements[0].equals("pack.mcmeta")) {
            Overlay o = overlay();
            if (o != null && o.file("pack.mcmeta") != null) {
                return bytes(o.file("pack.mcmeta"));
            }
        }
        return delegate.getRootResource(elements);
    }

    @Override
    public IoSupplier<InputStream> getResource(PackType type, ResourceLocation location) {
        Overlay o = type == PackType.CLIENT_RESOURCES ? overlay() : null;
        if (o != null) {
            String path = full(location);
            byte[] data = o.file(path);
            if (data != null) {
                return bytes(data);
            }
            String alias = o.aliasOf(path);
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
        Overlay o = type == PackType.CLIENT_RESOURCES ? overlay() : null;
        if (o == null) {
            delegate.listResources(type, namespace, path, output);
            return;
        }
        Set<ResourceLocation> seen = new HashSet<>();
        delegate.listResources(type, namespace, path, (loc, supplier) -> {
            seen.add(loc);
            byte[] data = o.file(full(loc));
            output.accept(loc, data != null ? bytes(data) : supplier);
        });
        String prefix = "assets/" + namespace + "/" + path + "/";
        for (Map.Entry<String, byte[]> e : o.files().entrySet()) {
            if (e.getKey().startsWith(prefix)) {
                ResourceLocation loc = ResourcesView.location(e.getKey());
                if (loc != null && seen.add(loc)) {
                    output.accept(loc, bytes(e.getValue()));
                }
            }
        }
        for (Map.Entry<String, String> e : o.aliases().entrySet()) {
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
            if (!pending.isDone()) {
                out.add(ResourceLocation.DEFAULT_NAMESPACE);
                return out;
            }
            Overlay o = overlay();
            if (o != null) {
                for (String path : o.files().keySet()) {
                    ResourceLocation loc = ResourcesView.location(path);
                    if (loc != null) {
                        out.add(loc.getNamespace());
                    }
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
    public void close() {
        delegate.close();
    }
}
