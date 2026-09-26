package dev.wizardlauncher.legacy;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Predicate;
import javax.imageio.ImageIO;

final class GuiSprites {
    static final int FIRST_SPRITE_FORMAT = 18;
    private static volatile Map<String, JsonObject> table;

    private final PackView pack;
    private final Overlay overlay;
    private final Map<String, BufferedImage> sheets = new HashMap<>();

    GuiSprites(PackView pack, Overlay overlay) {
        this.pack = pack;
        this.overlay = overlay;
    }

    int apply(Predicate<String> provided) {
        int made = 0;
        Map<String, Integer> perSheet = new LinkedHashMap<>();
        for (Map.Entry<String, JsonObject> e : table().entrySet()) {
            String target = "assets/minecraft/textures/" + e.getKey();
            if (provided.test(target)) {
                continue;
            }
            JsonObject sprite = e.getValue();
            for (JsonElement f : sprite.getAsJsonArray("from")) {
                JsonArray from = f.getAsJsonArray();
                String sheetPath = "assets/minecraft/textures/" + from.get(0).getAsString();
                BufferedImage sheet = sheet(sheetPath);
                if (sheet == null) {
                    continue;
                }
                byte[] png = crop(sheet, from, sprite.get("w").getAsInt(), sprite.get("h").getAsInt());
                if (png != null) {
                    overlay.put(target, png);
                    if (sprite.has("gui")) {
                        JsonObject meta = new JsonObject();
                        meta.add("gui", sprite.get("gui").deepCopy());
                        overlay.put(target + ".mcmeta", meta.toString().getBytes(StandardCharsets.UTF_8));
                    }
                    perSheet.merge(from.get(0).getAsString(), 1, Integer::sum);
                    made++;
                }
                break;
            }
        }
        perSheet.forEach((sheet, n) -> overlay.info("textures/" + sheet + ": cut into " + n + " GUI sprite(s) for the new sprite system"));
        return made;
    }

    private BufferedImage sheet(String path) {
        if (sheets.containsKey(path)) {
            return sheets.get(path);
        }
        BufferedImage image = null;
        if (pack.exists(path)) {
            try {
                image = ImageIO.read(new ByteArrayInputStream(pack.read(path)));
            } catch (IOException | RuntimeException e) {
                overlay.warn(path + ": could not be read as an image, its GUI sprites are left to the game");
            }
        }
        sheets.put(path, image);
        return image;
    }

    private static byte[] crop(BufferedImage sheet, JsonArray from, int w, int h) {
        double sx = sheet.getWidth() / from.get(3).getAsDouble();
        double sy = sheet.getHeight() / from.get(4).getAsDouble();
        int x0 = (int) Math.round(from.get(1).getAsInt() * sx);
        int y0 = (int) Math.round(from.get(2).getAsInt() * sy);
        int cw = Math.max(1, (int) Math.round(w * sx));
        int ch = Math.max(1, (int) Math.round(h * sy));
        if (x0 + cw > sheet.getWidth() || y0 + ch > sheet.getHeight()) {
            return null;
        }
        BufferedImage out = new BufferedImage(cw, ch, BufferedImage.TYPE_INT_ARGB);
        boolean visible = false;
        for (int y = 0; y < ch; y++) {
            for (int x = 0; x < cw; x++) {
                int argb = sheet.getRGB(x0 + x, y0 + y);
                visible |= (argb >>> 24) != 0;
                out.setRGB(x, y, argb);
            }
        }
        if (!visible) {
            return null;
        }
        try {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            ImageIO.write(out, "png", bytes);
            return bytes.toByteArray();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private static Map<String, JsonObject> table() {
        Map<String, JsonObject> t = table;
        if (t == null) {
            synchronized (GuiSprites.class) {
                t = table;
                if (t == null) {
                    t = new LinkedHashMap<>();
                    try (InputStream in = GuiSprites.class.getResourceAsStream("gui-sprites.json")) {
                        if (in == null) {
                            throw new IllegalStateException("GUI sprite table missing");
                        }
                        JsonObject root = JsonParser.parseString(new String(in.readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject();
                        for (Map.Entry<String, JsonElement> e : root.entrySet()) {
                            t.put(e.getKey(), e.getValue().getAsJsonObject());
                        }
                    } catch (IOException e) {
                        throw new UncheckedIOException(e);
                    }
                    table = t;
                }
            }
        }
        return t;
    }
}
