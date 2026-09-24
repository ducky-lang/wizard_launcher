package dev.wizardlauncher.pack

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import javax.imageio.ImageIO

internal class FontConverter(
    private val source: PackSource,
    private val report: ConversionReport,
) {
    val generated = LinkedHashMap<String, ByteArray>()

    fun convert(fontPath: String, font: JsonObject): Boolean {
        val providers = font.getAsJsonArray("providers") ?: return false
        if (providers.none { it.isJsonObject && it.asJsonObject.get("type")?.asString == "legacy_unicode" }) return false

        val out = JsonArray()
        for (p in providers) {
            val provider = p.asJsonObject
            if (provider.get("type")?.asString != "legacy_unicode") {
                out.add(provider)
                continue
            }
            val template = Res.normalize(provider.get("template")?.asString ?: "minecraft:font/unicode_page_%s.png")
            val sizes = provider.get("sizes")?.asString?.let { Res.split(it) }
                ?.let { (ns, p) -> "assets/$ns/$p" }
                ?.takeIf(source::exists)?.let(source::read)
            convertPages(template, sizes, fontPath).forEach(out::add)
        }
        font.add("providers", out)
        return true
    }

    private fun convertPages(template: String, sizes: ByteArray?, fontPath: String): List<JsonObject> {
        val (ns, pattern) = Res.split(template)
        val result = ArrayList<JsonObject>()
        val spaces = JsonObject()
        var pages = 0
        for (page in 0 until 256) {
            val hex = "%02x".format(page)
            val texturePath = "assets/$ns/textures/${pattern.replace("%s", hex)}"
            if (!source.exists(texturePath)) continue
            val image = source.open(texturePath).use { ImageIO.read(it) } ?: continue
            pages++
            val cell = image.width / 16
            if (cell <= 0 || image.height / 16 != cell) {
                report.warn("$texturePath: page is not a 16x16 grid of square cells; skipped")
                continue
            }
            val scale = cell / 16.0
            val cropped = BufferedImage(image.width, image.height, BufferedImage.TYPE_INT_ARGB)
            val rows = JsonArray()
            for (row in 0 until 16) {
                val line = StringBuilder()
                for (col in 0 until 16) {
                    val codepoint = page * 256 + row * 16 + col
                    val size = sizes?.getOrNull(codepoint)?.toInt()?.and(0xFF) ?: -1
                    val start = if (size > 0) size ushr 4 else 0
                    val end = if (size > 0) size and 0xF else 15
                    val blank = copyGlyph(image, cropped, col * cell, row * cell, cell,
                        (start * scale).toInt(), ((end + 1) * scale).toInt())
                    when {
                        size == 0 -> line.append('\u0000')
                        blank && size > 0 -> {
                            line.append('\u0000')
                            spaces.addProperty(String(Character.toChars(codepoint)), (end - start + 1) / 2 + 1)
                        }
                        blank -> line.append('\u0000')
                        else -> line.appendCodePoint(codepoint)
                    }
                }
                rows.add(line.toString())
            }
            val newTexture = "font/wizard_legacy_${pattern.substringAfterLast('/').replace("%s", hex)}"
            generated["assets/$ns/textures/$newTexture"] = png(cropped)
            result += JsonObject().apply {
                addProperty("type", "bitmap")
                addProperty("file", "$ns:$newTexture")
                addProperty("height", 8)
                addProperty("ascent", 7)
                add("chars", rows)
            }
        }
        if (spaces.size() > 0) {
            result.add(0, JsonObject().apply {
                addProperty("type", "space")
                add("advances", spaces)
            })
        }
        report.info("$fontPath: legacy_unicode -> $pages bitmap page(s)" +
            if (sizes == null && pages > 0) " (no glyph_sizes.bin, widths detected from pixels)" else "")
        return result
    }

    private fun copyGlyph(src: BufferedImage, dst: BufferedImage, x0: Int, y0: Int, cell: Int, from: Int, to: Int): Boolean {
        var blank = true
        for (y in 0 until cell) for (x in from until minOf(to, cell)) {
            val argb = src.getRGB(x0 + x, y0 + y)
            if (argb ushr 24 != 0) blank = false
            dst.setRGB(x0 + x - from, y0 + y, argb)
        }
        return blank
    }

    private fun png(image: BufferedImage): ByteArray =
        ByteArrayOutputStream().also { ImageIO.write(image, "png", it) }.toByteArray()
}
