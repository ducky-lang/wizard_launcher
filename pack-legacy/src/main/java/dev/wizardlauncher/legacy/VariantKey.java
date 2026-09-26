package dev.wizardlauncher.legacy;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Collectors;

public final class VariantKey {
    private final Map<String, String> properties;

    public VariantKey(Map<String, String> properties) {
        this.properties = Collections.unmodifiableMap(new TreeMap<>(properties));
    }

    public Map<String, String> properties() {
        return properties;
    }

    public static VariantKey parse(String key) {
        Map<String, String> out = new LinkedHashMap<>();
        if (key.isBlank() || key.equals("normal")) {
            return new VariantKey(out);
        }
        for (String part : key.split(",")) {
            int eq = part.indexOf('=');
            if (eq <= 0) {
                throw new IllegalArgumentException("bad variant key '" + key + "'");
            }
            out.put(part.substring(0, eq).trim(), part.substring(eq + 1).trim());
        }
        return new VariantKey(out);
    }

    public boolean matches(Map<String, Set<String>> condition) {
        for (Map.Entry<String, Set<String>> e : condition.entrySet()) {
            String value = properties.get(e.getKey());
            if (value == null || !e.getValue().contains(value)) {
                return false;
            }
        }
        return true;
    }

    @Override
    public String toString() {
        return properties.entrySet().stream().map(e -> e.getKey() + "=" + e.getValue()).collect(Collectors.joining(","));
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof VariantKey other && other.properties.equals(properties);
    }

    @Override
    public int hashCode() {
        return properties.hashCode();
    }
}
