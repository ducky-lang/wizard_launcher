package dev.wizardlauncher.legacy;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import javax.imageio.ImageIO;

final class FontUpgrader {
    private final PackView pack;
    private final Overlay overlay;

    FontUpgrader(PackView pack, Overlay overlay) {
        this.pack = pack;
        this.overlay = overlay;
    }

    boolean convert(String fontPath, JsonObject font) throws IOException {
        JsonArray providers = font.getAsJsonArray("providers");
        if (providers == null) {
            return false;
        }
        boolean legacy = false;
        for (JsonElement p : providers) {
            if (p.isJsonObject() && "legacy_unicode".equals(type(p.getAsJsonObject()))) {
                legacy = true;
            }
        }
        if (!legacy) {
            return false;
        }
        JsonArray out = new JsonArray();
        for (JsonElement p : providers) {
            JsonObject provider = p.getAsJsonObject();
            if (!"legacy_unicode".equals(type(provider))) {
                out.add(provider);
                continue;
            }
            String template = Res.normalize(provider.has("template") ? provider.get("template").getAsString() : "minecraft:font/unicode_page_%s.png");
            byte[] sizes = null;
            if (provider.has("sizes")) {
                String sizesId = provider.get("sizes").getAsString();
                String sizesPath = "assets/" + Res.namespace(sizesId) + "/" + Res.path(sizesId);
                if (pack.exists(sizesPath)) {
                    sizes = pack.read(sizesPath);
                }
            }
            for (JsonObject converted : convertPages(template, sizes, fontPath)) {
                out.add(converted);
            }
        }
        font.add("providers", out);
        return true;
    }

    private static String type(JsonObject provider) {
        return provider.has("type") ? provider.get("type").getAsString() : "";
    }

    private List<JsonObject> convertPages(String template, byte[] sizes, String fontPath) throws IOException {
        String ns = Res.namespace(template);
        String pattern = Res.path(template);
        List<JsonObject> result = new ArrayList<>();
        JsonObject spaces = new JsonObject();
        int pages = 0;
        for (int page = 0; page < 256; page++) {
            String hex = String.format("%02x", page);
            String texturePath = "assets/" + ns + "/textures/" + pattern.replace("%s", hex);
            if (!pack.exists(texturePath)) {
                continue;
            }
            BufferedImage image = ImageIO.read(new ByteArrayInputStream(pack.read(texturePath)));
            if (image == null) {
                continue;
            }
            pages++;
            int cell = image.getWidth() / 16;
            if (cell <= 0 || image.getHeight() / 16 != cell) {
                overlay.warn(texturePath + ": page is not a 16x16 grid of square cells; skipped");
                continue;
            }
            double scale = cell / 16.0;
            BufferedImage cropped = new BufferedImage(image.getWidth(), image.getHeight(), BufferedImage.TYPE_INT_ARGB);
            JsonArray rows = new JsonArray();
            for (int row = 0; row < 16; row++) {
                StringBuilder line = new StringBuilder();
                for (int col = 0; col < 16; col++) {
                    int codepoint = page * 256 + row * 16 + col;
                    int size = sizes != null && codepoint < sizes.length ? sizes[codepoint] & 0xFF : -1;
                    int start = size > 0 ? size >>> 4 : 0;
                    int end = size > 0 ? size & 0xF : 15;
                    boolean blank = copyGlyph(image, cropped, col * cell, row * cell, cell, (int) (start * scale), (int) ((end + 1) * scale));
                    if (size == 0 || blank) {
                        line.append('\u0000');
                        if (blank && size > 0) {
                            spaces.addProperty(new String(Character.toChars(codepoint)), (end - start + 1) / 2 + 1);
                        }
                    } else {
                        line.appendCodePoint(codepoint);
                    }
                }
                rows.add(line.toString());
            }
            String name = pattern.substring(pattern.lastIndexOf('/') + 1).replace("%s", hex);
            String newTexture = "font/wizard_legacy_" + name;
            overlay.put("assets/" + ns + "/textures/" + newTexture, png(cropped));
            JsonObject bitmap = new JsonObject();
            bitmap.addProperty("type", "bitmap");
            bitmap.addProperty("file", ns + ":" + newTexture);
            bitmap.addProperty("height", 8);
            bitmap.addProperty("ascent", 7);
            bitmap.add("chars", rows);
            result.add(bitmap);
        }
        if (spaces.size() > 0) {
            JsonObject space = new JsonObject();
            space.addProperty("type", "space");
            space.add("advances", spaces);
            result.add(0, space);
        }
        overlay.info(fontPath + ": legacy_unicode -> " + pages + " bitmap page(s)"
            + (sizes == null && pages > 0 ? " (no glyph_sizes.bin, widths detected from pixels)" : ""));
        return result;
    }

    private static boolean copyGlyph(BufferedImage src, BufferedImage dst, int x0, int y0, int cell, int from, int to) {
        boolean blank = true;
        for (int y = 0; y < cell; y++) {
            for (int x = from; x < Math.min(to, cell); x++) {
                int argb = src.getRGB(x0 + x, y0 + y);
                if (argb >>> 24 != 0) {
                    blank = false;
                }
                dst.setRGB(x0 + x - from, y0 + y, argb);
            }
        }
        return blank;
    }

    private static byte[] png(BufferedImage image) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ImageIO.write(image, "png", out);
        return out.toByteArray();
    }
}
