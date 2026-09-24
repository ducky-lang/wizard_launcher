package dev.wizardlauncher.legacy;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class ShaderUpgrader {
    private static final Pattern VERSION = Pattern.compile("(?m)^\\s*#version\\s+(\\d+).*$");
    private static final Pattern FIXED = Pattern.compile("\\b(gl_ModelViewMatrix|gl_ProjectionMatrix|gl_ModelViewProjectionMatrix|gl_Vertex|gl_MultiTexCoord\\d|gl_TexCoord|ftransform)\\b");

    public record Result(String source, boolean changed, String problem) {
    }

    private ShaderUpgrader() {
    }

    public static Result upgrade(String source, boolean fragment) {
        Matcher v = VERSION.matcher(source);
        int version = v.find() ? Integer.parseInt(v.group(1)) : 110;
        if (version >= 150) {
            return new Result(source, false, null);
        }
        Matcher fixed = FIXED.matcher(source);
        if (fixed.find()) {
            return new Result(source, false, "uses fixed-function '" + fixed.group(1) + "', which GLSL 150 core does not have");
        }
        String s = VERSION.matcher(source).find() ? VERSION.matcher(source).replaceFirst("#version 150") : "#version 150\n" + source;
        s = s.replaceAll("\\battribute\\b", "in");
        s = s.replaceAll("\\bvarying\\b", fragment ? "in" : "out");
        s = s.replaceAll("\\btexture2DLod\\b", "textureLod");
        s = s.replaceAll("\\btexture2D\\b", "texture");
        if (fragment && Pattern.compile("\\bgl_FragColor\\b").matcher(s).find()) {
            s = s.replaceAll("\\bgl_FragColor\\b", "fragColor");
            s = s.replaceFirst("#version 150", "#version 150\n\nout vec4 fragColor;");
        }
        return new Result(s, true, null);
    }
}
