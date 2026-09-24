package dev.wizardlauncher.legacy

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.awt.image.BufferedImage
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream
import javax.imageio.ImageIO
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

class LegacyTranslatorTest {
    @TempDir lateinit var tmp: Path

    private fun legacyFiles(): MutableMap<String, ByteArray> {
        val files = linkedMapOf(
            "pack.mcmeta" to """{"pack":{"pack_format":6,"description":"Castle"}}""",
            "assets/minecraft/blockstates/grass_path.json" to """{"variants":{"":{"model":"minecraft:block/grass_path"}}}""",
            "assets/minecraft/blockstates/cauldron.json" to """{"variants":{
                "level=0":{"model":"minecraft:block/cauldron"},
                "level=1":{"model":"minecraft:block/cauldron_level1"},
                "level=2":{"model":"wizard:block/cauldron_two"},
                "level=3":{"model":"minecraft:block/cauldron_level3"}}}""",
            "assets/minecraft/textures/block/grass_path_top.png" to "png",
            "assets/wizard/models/block/cauldron_two.json" to """{"parent":"minecraft:block/cauldron_level1"}""",
            "assets/wizard/models/item/wand.json" to """{"parent":"item/generated","textures":{
                "layer0":"wizard:custom/wand/core","layer1":"minecraft:entity/chest/normal"}}""",
            "assets/wizard/textures/custom/wand/core.png" to "png",
            "assets/minecraft/lang/en_us.json" to """{"block.minecraft.grass_path":"Floo Path"}""",
            "assets/minecraft/shaders/program/wobble.fsh" to """
                #version 120
                uniform sampler2D DiffuseSampler;
                varying vec2 texCoord;
                void main(){ gl_FragColor = texture2D(DiffuseSampler, texCoord); }
            """.trimIndent(),
            "assets/minecraft/font/default.json" to """{"providers":[
                {"type":"bitmap","file":"minecraft:font/ascii.png","ascent":7,"chars":["a"]},
                {"type":"legacy_unicode","sizes":"minecraft:font/glyph_sizes.bin","template":"minecraft:font/unicode_page_%s.png"}]}""",
            LegacyTranslator.PACK_RULES to """{"format":1,
                "states":{"minecraft:note_block":{"note=1,instrument=harp,powered=false":{"model":"wizard:block/crystal"}}},
                "items":{"minecraft:stick":[
                    {"predicate":{"custom_model_data":2},"model":"wizard:item/wand_b"},
                    {"predicate":{"custom_model_data":1},"model":"wizard:item/wand"}]}}""",
        ).mapValues { it.value.toByteArray() }.toMutableMap()
        files["assets/minecraft/textures/font/unicode_page_e0.png"] = fontPage()
        files["assets/minecraft/font/glyph_sizes.bin"] = ByteArray(65536).also {
            it[0xE000] = 0x24
            it[0xE001] = 0x07
        }
        return files
    }

    private fun zipOf(files: Map<String, ByteArray>, name: String = "legacy.zip"): Path {
        val zip = tmp.resolve(name)
        ZipOutputStream(Files.newOutputStream(zip)).use { out ->
            files.forEach { (n, data) -> out.putNextEntry(ZipEntry("Castle Pack/$n")); out.write(data); out.closeEntry() }
        }
        return zip
    }

    private fun fontPage(): ByteArray {
        val img = BufferedImage(256, 256, BufferedImage.TYPE_INT_ARGB)
        for (y in 0 until 16) for (x in 0 until 16) img.setRGB(x, y, if (x in 2..4) 0xFFFF0000.toInt() else 0)
        img.setRGB(15, 0, 0xFF00FF00.toInt())
        return ByteArrayOutputStream().also { ImageIO.write(img, "png", it) }.toByteArray()
    }

    private fun overlay(files: Map<String, ByteArray> = legacyFiles()): Overlay =
        FilePackView.open(zipOf(files)).use { LegacyTranslator.translate(it, 6) }

    private fun Overlay.json(path: String): JsonObject =
        JsonParser.parseString(String(assertNotNull(file(path), "missing $path"))).asJsonObject

    @Test fun `format detection covers every pre 1_20 pack`() {
        assertEquals(6, LegacyTranslator.readFormat("""{"pack":{"pack_format":6}}""".toByteArray()))
        assertTrue(LegacyTranslator.needsTranslation(6))
        assertTrue(LegacyTranslator.needsTranslation(13))
        assertFalse(LegacyTranslator.needsTranslation(15))
        assertFalse(LegacyTranslator.needsTranslation(3))
        assertEquals(-1, LegacyTranslator.readFormat("nope".toByteArray()))
    }

    @Test fun `grass path overrides are served under the dirt path names`() {
        val o = overlay()
        assertEquals("assets/minecraft/textures/block/grass_path_top.png", o.aliasOf("assets/minecraft/textures/block/dirt_path_top.png"))
        assertEquals("minecraft:block/dirt_path",
            o.json("assets/minecraft/blockstates/dirt_path.json").getAsJsonObject("variants").getAsJsonObject("").get("model").asString)
        assertEquals("Floo Path", o.json("assets/minecraft/lang/en_us.json").get("block.minecraft.dirt_path").asString)
    }

    @Test fun `cauldron states are split the way 1_17 split the block`() {
        val o = overlay()
        assertEquals(setOf(""), o.json("assets/minecraft/blockstates/cauldron.json").getAsJsonObject("variants").keySet())
        val water = o.json("assets/minecraft/blockstates/water_cauldron.json").getAsJsonObject("variants")
        assertEquals(setOf("level=1", "level=2", "level=3"), water.keySet())
        assertEquals("minecraft:block/water_cauldron_level1", water.getAsJsonObject("level=1").get("model").asString)
        assertEquals("wizard:block/cauldron_two", water.getAsJsonObject("level=2").get("model").asString)
        assertEquals("minecraft:block/water_cauldron_full", water.getAsJsonObject("level=3").get("model").asString)
        assertEquals("minecraft:block/water_cauldron_level1", o.json("assets/wizard/models/block/cauldron_two.json").get("parent").asString)
    }

    @Test fun `textures outside block and item join the block atlas`() {
        val sources = overlay().json("assets/minecraft/atlases/blocks.json").getAsJsonArray("sources").map { it.asJsonObject }
        assertTrue(sources.any { it.get("type").asString == "directory" && it.get("source").asString == "custom" })
        assertTrue(sources.any { it.get("type").asString == "single" && it.get("resource").asString == "minecraft:entity/chest/normal" })
    }

    @Test fun `packs from 1_19_3 and later get no atlas but still get font fixes`() {
        val files = legacyFiles()
        files["pack.mcmeta"] = """{"pack":{"pack_format":13}}""".toByteArray()
        val o = FilePackView.open(zipOf(files)).use { LegacyTranslator.translate(it, 13) }
        assertNull(o.file("assets/minecraft/atlases/blocks.json"))
        assertNull(o.aliasOf("assets/minecraft/blockstates/dirt_path.json"))
        assertNotNull(o.file("assets/minecraft/textures/font/wizard_legacy_unicode_page_e0.png"))
    }

    @Test fun `legacy unicode pages become bitmap providers with spacing kept`() {
        val o = overlay()
        val providers = o.json("assets/minecraft/font/default.json").getAsJsonArray("providers").map { it.asJsonObject }
        assertFalse(providers.any { it.get("type").asString == "legacy_unicode" })
        assertEquals(5, providers.first { it.get("type").asString == "space" }.getAsJsonObject("advances").get("\uE001").asInt)
        val bitmap = providers.first { it.get("file")?.asString?.contains("wizard_legacy") == true }
        assertEquals('\uE000', bitmap.getAsJsonArray("chars")[0].asString[0])
        val page = ImageIO.read(ByteArrayInputStream(o.file("assets/minecraft/textures/font/wizard_legacy_unicode_page_e0.png")))
        assertEquals(0xFFFF0000.toInt(), page.getRGB(0, 0))
        assertEquals(0, page.getRGB(15, 0) ushr 24)
    }

    @Test fun `post shaders are upgraded to GLSL 150`() {
        val fsh = String(overlay().file("assets/minecraft/shaders/program/wobble.fsh")!!)
        assertTrue(fsh.startsWith("#version 150"))
        assertTrue("out vec4 fragColor;" in fsh && "in vec2 texCoord" in fsh && "texture(DiffuseSampler" in fsh)
    }

    @Test fun `states and item overrides from the rules file are applied`() {
        val o = overlay()
        val note = o.json("assets/minecraft/blockstates/note_block.json").getAsJsonObject("variants")
        assertEquals("wizard:block/crystal", note.getAsJsonObject("instrument=harp,note=1,powered=false").get("model").asString)
        val overrides = o.json("assets/minecraft/models/item/stick.json").getAsJsonArray("overrides")
        assertEquals(listOf(1, 2), overrides.map { it.asJsonObject.getAsJsonObject("predicate").get("custom_model_data").asInt })
    }

    @Test fun `a modern pack is left alone`() {
        val o = FilePackView.open(zipOf(mapOf(
            "pack.mcmeta" to """{"pack":{"pack_format":15}}""".toByteArray(),
            "assets/minecraft/models/block/stone.json" to """{"parent":"block/cube_all"}""".toByteArray(),
        ), "modern.zip")).use { LegacyTranslator.translate(it, 15) }
        assertEquals(setOf("pack.mcmeta"), o.files().keys)
        assertTrue(o.aliases().isEmpty())
    }

    @Test fun `export writes a standalone pack with the report`() {
        val out = tmp.resolve("out.zip")
        PackExporter.export(zipOf(legacyFiles()), out, emptyList(), null)
        ZipFile(out.toFile()).use { z ->
            val meta = JsonParser.parseString(String(z.getInputStream(z.getEntry("pack.mcmeta")).readBytes())).asJsonObject
            assertEquals(15, meta.getAsJsonObject("pack").get("pack_format").asInt)
            assertNotNull(z.getEntry("assets/minecraft/textures/block/dirt_path_top.png"))
            assertNotNull(z.getEntry("assets/minecraft/textures/block/grass_path_top.png"))
            assertNotNull(z.getEntry(PackExporter.REPORT))
        }
    }

    @Test fun `unsafe paths and bad rules are refused`() {
        assertFalse(FilePackView.safePath("../evil.txt"))
        assertFalse(FilePackView.safePath("assets/../../x"))
        assertFalse(FilePackView.safePath("C:/x"))
        assertTrue(FilePackView.safePath("assets/minecraft/x.png"))
        val error = runCatching { Rules.parse("""{"copy_files":[{"from":"../x","to":"assets/y"}]}""", "test.json") }
        assertTrue(error.exceptionOrNull()?.message?.contains("unsafe path") == true)
    }
}
