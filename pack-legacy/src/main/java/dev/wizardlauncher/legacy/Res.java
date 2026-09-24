package dev.wizardlauncher.legacy;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class Res {
    private static final Pattern BLOCKSTATE = Pattern.compile("^assets/([^/]+)/blockstates/(.+)\\.json$");

    private Res() {
    }

    static String normalize(String ref) {
        if (ref.startsWith("#") || ref.indexOf(':') >= 0) {
            return ref;
        }
        return "minecraft:" + ref;
    }

    static String namespace(String id) {
        String n = normalize(id);
        return n.substring(0, n.indexOf(':'));
    }

    static String path(String id) {
        String n = normalize(id);
        return n.substring(n.indexOf(':') + 1);
    }

    static String modelFile(String id) {
        return "assets/" + namespace(id) + "/models/" + path(id) + ".json";
    }

    static String textureFile(String id) {
        return "assets/" + namespace(id) + "/textures/" + path(id) + ".png";
    }

    static String blockstateFile(String id) {
        return "assets/" + namespace(id) + "/blockstates/" + path(id) + ".json";
    }

    static String blockOf(String file) {
        Matcher m = BLOCKSTATE.matcher(file);
        return m.matches() ? m.group(1) + ":" + m.group(2) : null;
    }
}
