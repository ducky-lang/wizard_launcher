package dev.wizardlauncher.core

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import dev.wizardlauncher.core.install.ContentInstaller
import dev.wizardlauncher.core.install.InstallState
import dev.wizardlauncher.core.install.Progress
import dev.wizardlauncher.core.instance.Instance
import dev.wizardlauncher.core.instance.InstanceManager
import dev.wizardlauncher.core.net.HuggingFace
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.runtime.JavaRuntimes
import dev.wizardlauncher.core.security.Hashes
import java.nio.file.Files
import java.nio.file.Path
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

class InstancesTest {
    @TempDir lateinit var tmp: Path

    private fun mrpack(
        name: String = "Cozy Pack",
        deps: Map<String, String> = mapOf("minecraft" to "1.21.1", "fabric-loader" to "0.16.9"),
        path: String = "mods/sodium.jar",
        url: String = "https://cdn.modrinth.com/data/AANobbMI/versions/x/sodium.jar",
        sha512: String? = "a".repeat(128),
        extra: (ZipOutputStream) -> Unit = {},
    ): Path {
        val file = tmp.resolve("${name.replace(' ', '_')}-${System.nanoTime()}.mrpack")
        val index = JsonObject().apply {
            addProperty("formatVersion", 1)
            addProperty("game", "minecraft")
            addProperty("versionId", "1.0")
            addProperty("name", name)
            add("dependencies", JsonObject().apply { deps.forEach { (k, v) -> addProperty(k, v) } })
            add("files", JsonArray().apply {
                add(JsonObject().apply {
                    addProperty("path", path)
                    add("hashes", JsonObject().apply { sha512?.let { addProperty("sha512", it) } })
                    add("downloads", JsonArray().apply { add(url) })
                })
            })
        }
        ZipOutputStream(Files.newOutputStream(file)).use { z ->
            z.putNextEntry(ZipEntry("modrinth.index.json")); z.write(index.toString().toByteArray()); z.closeEntry()
            extra(z)
        }
        return file
    }

    @Test fun `a Fabric mrpack is read and anything unsafe is refused`() {
        val info = InstanceManager.readMrpack(mrpack())
        assertEquals("Cozy Pack", info.name)
        assertEquals("1.21.1", info.minecraft)
        assertEquals("0.16.9", info.loader)
        assertEquals(1, info.mods)
        assertFailsWith<LauncherException> { InstanceManager.readMrpack(mrpack(deps = mapOf("minecraft" to "1.20.1", "forge" to "47.2.0"))) }
        assertFailsWith<LauncherException> { InstanceManager.readMrpack(mrpack(path = "../evil.jar")) }
        assertFailsWith<LauncherException> { InstanceManager.readMrpack(mrpack(url = "https://evil.example.com/sodium.jar")) }
        assertFailsWith<LauncherException> { InstanceManager.readMrpack(mrpack(url = "http://cdn.modrinth.com/sodium.jar")) }
        assertFailsWith<LauncherException> { InstanceManager.readMrpack(mrpack(sha512 = null)) }
        val curse = tmp.resolve("curse.zip")
        ZipOutputStream(Files.newOutputStream(curse)).use { z -> z.putNextEntry(ZipEntry("manifest.json")); z.write("{}".toByteArray()); z.closeEntry() }
        val e = assertFailsWith<LauncherException> { InstanceManager.readMrpack(curse) }
        assertTrue("CurseForge" in e.message!!)
    }

    @Test fun `imported modpacks become separate selectable installations`() {
        val paths = AppPaths(tmp.resolve("data")).ensure()
        val settings = Settings(paths.root.resolve("settings.json")).load()
        val manager = InstanceManager(paths, settings)
        assertEquals(listOf("1.20.1", "1.21.1"), manager.all().map { it.id })
        assertEquals("1.20.1", manager.selected().id)

        val first = manager.importModpack(mrpack())
        val second = manager.importModpack(mrpack())
        assertEquals("custom-cozy-pack", first.id)
        assertEquals("custom-cozy-pack-2", second.id)
        assertEquals("1.21.1", first.minecraft)
        assertEquals(21, first.requiredJava)
        assertFalse(first.builtin)
        assertTrue(Files.isRegularFile(first.modpackFile!!))
        assertTrue(first.modpackFile!!.startsWith(paths.instanceRoot(first.id)))

        manager.select(first.id)
        assertEquals(first.id, paths.instanceId)
        assertEquals(paths.gameDir(first.id), paths.gameDir)
        assertEquals(first.id, InstanceManager(paths, Settings(paths.root.resolve("settings.json")).load()).selected().id)

        manager.remove(first.id)
        assertFalse(Files.exists(paths.instanceRoot(first.id)))
        assertEquals("1.20.1", manager.selected().id)
        assertFailsWith<LauncherException> { manager.remove("1.21.1") }
    }

    @Test fun `each installation gets the right Java and pack format`() {
        assertEquals(21, Instance.javaFor("1.21.1"))
        assertEquals(21, Instance.javaFor("1.20.5"))
        assertEquals(17, Instance.javaFor("1.20.1"))
        assertEquals(17, Instance.javaFor("1.18.2"))
        assertEquals(8, Instance.javaFor("1.16.5"))
        assertEquals("java-runtime-delta", JavaRuntimes.componentFor(21))
        assertEquals("java-runtime-gamma", JavaRuntimes.componentFor(17))
        assertEquals(21, JavaRuntimes.majorOf("java-runtime-delta", 0))
        assertEquals(34, Instance.PACK_FORMATS["1.21.1"])
    }

    @Test fun `Hugging Face links are understood`() {
        val loc = HuggingFace.parse("https://huggingface.co/datasets/owner/castle/resolve/main/packs/Resource%20Pack.zip?download=true")!!
        assertEquals("datasets", loc.kind)
        assertEquals("owner/castle", loc.repo)
        assertEquals("main", loc.revision)
        assertEquals("packs/Resource Pack.zip", loc.path)
        assertEquals("models", HuggingFace.parse("https://huggingface.co/owner/model/resolve/v1/map.zip")!!.kind)
        assertNull(HuggingFace.parse("https://example.com/owner/castle/resolve/main/map.zip"))
    }

    @Test fun `the resource pack is installed as a linked zip and refreshed when it changes`() {
        val paths = AppPaths(tmp.resolve("data")).ensure()
        val state = InstallState(paths.root.resolve("install_state.json"))
        val resource = Catalog.current.resource("resource_pack")
        val cached = paths.contentCache.resolve("${resource.name}.zip")
        Files.createDirectories(cached.parent)
        fun writePack(text: String) {
            ZipOutputStream(Files.newOutputStream(cached)).use { z ->
                z.putNextEntry(ZipEntry("pack.mcmeta")); z.write("""{"pack":{"pack_format":6,"description":"$text"}}""".toByteArray()); z.closeEntry()
            }
            state.mark("content:${resource.id}", Hashes.of(cached, "SHA-256"))
        }
        writePack("one")
        val content = ContentInstaller(paths, state, SecureDownloader(setOf("huggingface.co")), Progress.NONE, online = false)
        val game = paths.gameDir("1.21.1")
        val stale = game.resolve("resourcepacks").resolve(resource.name)
        Files.createDirectories(stale)
        Files.writeString(stale.resolve("pack.mcmeta"), "{}")

        val name = content.ensureResourcePack(game)
        assertEquals("${resource.name}.zip", name)
        val installed = game.resolve("resourcepacks").resolve(name)
        assertTrue(Files.isRegularFile(installed))
        assertFalse(Files.exists(stale))
        assertEquals(Hashes.of(cached, "SHA-256"), Hashes.of(installed, "SHA-256"))

        Files.delete(cached)
        writePack("two")
        content.ensureResourcePack(game)
        assertTrue(String(Files.readAllBytes(installed)).isNotEmpty())
        assertEquals(Hashes.of(cached, "SHA-256"), Hashes.of(installed, "SHA-256"))
    }

    @Test fun `the pack reader mod is picked per Minecraft version`() {
        val paths = AppPaths(tmp.resolve("data")).ensure()
        val content = ContentInstaller(paths, InstallState(paths.root.resolve("s.json")), SecureDownloader(emptySet()), Progress.NONE, online = false)
        val manager = InstanceManager(paths, Settings(paths.root.resolve("settings.json")).load())
        val mods = paths.resources.resolve("mods")
        Files.createDirectories(mods)
        Files.writeString(mods.resolve("wizard-legacy-packs-1.21.1.jar"), "121")
        val game = paths.gameDir("1.21.1")
        content.ensureLegacyPackSupport(manager.require("1.21.1"), game)
        assertEquals("121", Files.readString(game.resolve("mods").resolve(ContentInstaller.LEGACY_MOD)))
        val old = paths.gameDir("1.20.1")
        Files.createDirectories(old.resolve("mods"))
        Files.writeString(old.resolve("mods").resolve(ContentInstaller.LEGACY_MOD), "stale")
        content.ensureLegacyPackSupport(manager.require("1.20.1"), old)
        assertFalse(Files.exists(old.resolve("mods").resolve(ContentInstaller.LEGACY_MOD)))
    }
}
