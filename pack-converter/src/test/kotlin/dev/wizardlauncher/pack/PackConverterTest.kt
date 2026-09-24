package dev.wizardlauncher.pack

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.awt.image.BufferedImage
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
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

class PackConverterTest {
    @TempDir lateinit var tmp: Path

    private fun legacyPack(extra: Map<String, ByteArray> = emptyMap()): Path {
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
            PackConverter.PACK_RULES to """{"format":1,
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
        files.putAll(extra)
        val zip = tmp.resolve("legacy.zip")
        ZipOutputStream(Files.newOutputStream(zip)).use { out ->
            files.forEach { (name, data) ->

                out.putNextEntry(ZipEntry("Castle Pack/$name")); out.write(data); out.closeEntry()
            }
        }
        return zip
    }

    private fun fontPage(): ByteArray {
        val img = BufferedImage(256, 256, BufferedImage.TYPE_INT_ARGB)
        for (y in 0 until 16) for (x in 0 until 16) img.setRGB(x, y, if (x in 2..4) 0xFFFF0000.toInt() else 0)
        img.setRGB(15, 0, 0xFF00FF00.toInt())
        return ByteArrayOutputStream().also { ImageIO.write(img, "png", it) }.toByteArray()
    }

    private fun convert(): Pair<ZipFile, ConversionReport> {
        val out = tmp.resolve("out.zip")
        val report = PackConverter().convert(legacyPack(), out)
        return ZipFile(out.toFile()) to report
    }

    private fun ZipFile.json(path: String): JsonObject {
        val entry = assertNotNull(getEntry(path), "missing $path")
        return JsonParser.parseString(getInputStream(entry).readBytes().toString(Charsets.UTF_8)).asJsonObject
    }

    @Test fun `pack format is raised and the wrapper folder is removed`() {
        val (zip, _) = convert()
        zip.use { assertEquals(15, it.json("pack.mcmeta").getAsJsonObject("pack").get("pack_format").asInt) }
    }

    @Test fun `grass path overrides reach dirt path`() {
        val (zip, _) = convert()
        zip.use {
            assertNotNull(it.getEntry("assets/minecraft/textures/block/dirt_path_top.png"))
            val state = it.json("assets/minecraft/blockstates/dirt_path.json")
            assertEquals("minecraft:block/dirt_path", state.getAsJsonObject("variants").getAsJsonObject("").get("model").asString)
            assertEquals("Floo Path", it.json("assets/minecraft/lang/en_us.json").get("block.minecraft.dirt_path").asString)
        }
    }

    @Test fun `cauldron is split into cauldron and water_cauldron`() {
        val (zip, _) = convert()
        zip.use {
            val dry = it.json("assets/minecraft/blockstates/cauldron.json").getAsJsonObject("variants")
            assertEquals(setOf(""), dry.keySet())
            val water = it.json("assets/minecraft/blockstates/water_cauldron.json").getAsJsonObject("variants")
            assertEquals(setOf("level=1", "level=2", "level=3"), water.keySet())
            assertEquals("minecraft:block/water_cauldron_level1", water.getAsJsonObject("level=1").get("model").asString)
            assertEquals("wizard:block/cauldron_two", water.getAsJsonObject("level=2").get("model").asString)
            assertEquals("minecraft:block/water_cauldron_full", water.getAsJsonObject("level=3").get("model").asString)

            assertEquals("minecraft:block/water_cauldron_level1",
                it.json("assets/wizard/models/block/cauldron_two.json").get("parent").asString)
        }
    }

    @Test fun `textures outside block and item are added to the block atlas`() {
        val (zip, _) = convert()
        zip.use {
            val sources = it.json("assets/minecraft/atlases/blocks.json").getAsJsonArray("sources").map { s -> s.asJsonObject }
            assertTrue(sources.any { s -> s.get("type").asString == "directory" && s.get("source").asString == "custom" })
            assertTrue(sources.any { s -> s.get("type").asString == "single" && s.get("resource").asString == "minecraft:entity/chest/normal" })
        }
    }

    @Test fun `legacy unicode pages become bitmap providers with spacing preserved`() {
        val (zip, _) = convert()
        zip.use {
            val providers = it.json("assets/minecraft/font/default.json").getAsJsonArray("providers").map { p -> p.asJsonObject }
            assertFalse(providers.any { p -> p.get("type").asString == "legacy_unicode" })
            val space = providers.first { p -> p.get("type").asString == "space" }
            assertEquals(5, space.getAsJsonObject("advances").get("\uE001").asInt)
            val bitmap = providers.first { p -> p.get("file")?.asString?.contains("wizard_legacy") == true }
            assertEquals('\uE000', bitmap.getAsJsonArray("chars")[0].asString[0])
            val page = ImageIO.read(it.getInputStream(it.getEntry("assets/minecraft/textures/font/wizard_legacy_unicode_page_e0.png")))
            assertEquals(0xFFFF0000.toInt(), page.getRGB(0, 0))
            assertEquals(0, page.getRGB(15, 0) ushr 24)
        }
    }

    @Test fun `post shaders are upgraded to GLSL 150`() {
        val (zip, _) = convert()
        zip.use {
            val fsh = it.getInputStream(it.getEntry("assets/minecraft/shaders/program/wobble.fsh")).readBytes().toString(Charsets.UTF_8)
            assertTrue(fsh.startsWith("#version 150"))
            assertTrue("out vec4 fragColor;" in fsh && "in vec2 texCoord" in fsh && "texture(DiffuseSampler" in fsh)
        }
    }

    @Test fun `states and item overrides defined by rules are applied`() {
        val (zip, report) = convert()
        zip.use {
            val note = it.json("assets/minecraft/blockstates/note_block.json").getAsJsonObject("variants")
            assertEquals("wizard:block/crystal", note.getAsJsonObject("instrument=harp,note=1,powered=false").get("model").asString)
            val overrides = it.json("assets/minecraft/models/item/stick.json").getAsJsonArray("overrides")
            assertEquals(listOf(1, 2), overrides.map { o -> o.asJsonObject.getAsJsonObject("predicate").get("custom_model_data").asInt })
            assertNotNull(it.getEntry(PackConverter.REPORT))
        }
        assertTrue(report.info.any { "state(s) defined" in it })
    }

    @Test fun `zip slip entries are never written`() {
        assertFalse(PackSource.safePath("../evil.txt"))
        assertFalse(PackSource.safePath("assets/../../x"))
        assertFalse(PackSource.safePath("C:/x"))
        assertTrue(PackSource.safePath("assets/minecraft/x.png"))
    }

    @Test fun `bad rules files are rejected with a clear message`() {
        val error = runCatching { StateRules.parse("""{"copy_files":[{"from":"../x","to":"assets/y"}]}""", "test.json") }
        assertTrue(error.exceptionOrNull()?.message?.contains("unsafe path") == true)
    }
}
