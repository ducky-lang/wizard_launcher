package dev.wizardlauncher.core

import com.google.gson.JsonParser
import dev.wizardlauncher.core.game.GameOptions
import dev.wizardlauncher.core.game.ModConfigs
import dev.wizardlauncher.core.game.OfflineUuid
import dev.wizardlauncher.core.game.ServersDat
import dev.wizardlauncher.core.install.InstallState
import dev.wizardlauncher.core.install.Library
import dev.wizardlauncher.core.install.ModpackInstaller
import dev.wizardlauncher.core.install.OfflineBundle
import dev.wizardlauncher.core.install.Rules
import dev.wizardlauncher.core.install.SafeZip
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.security.Redactor
import dev.wizardlauncher.core.security.SecretStore
import dev.wizardlauncher.core.security.Signatures
import java.net.URI
import java.nio.file.Files
import java.nio.file.Path
import java.security.KeyPairGenerator
import java.security.Signature
import java.util.Base64
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

class CoreTest {
    @TempDir lateinit var tmp: Path

    @Test fun `tokens never survive redaction`() {
        val jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
        val line = "java ... --accessToken $jwt --username Harry"
        assertFalse(jwt in Redactor.redact(line))
        assertTrue("--username Harry" in Redactor.redact(line))
        assertFalse("secret123" in Redactor.redact("""{"refresh_token":"secret123"}"""))
        assertFalse("abc.def" in Redactor.redact("Authorization: Bearer abc.def"))
    }

    @Test fun `downloads are limited to https and allow-listed hosts`() {
        val d = SecureDownloader(setOf("modrinth.com"))
        assertTrue(d.isAllowed(URI.create("https://cdn.modrinth.com/x.jar")))
        assertFalse(d.isAllowed(URI.create("http://cdn.modrinth.com/x.jar")))
        assertFalse(d.isAllowed(URI.create("https://evilmodrinth.com/x.jar")))
        assertFalse(d.isAllowed(URI.create("https://modrinth.com.evil.net/x.jar")))
    }

    @Test fun `zip slip is rejected and wrapper folders are unwrapped`() {
        val good = tmp.resolve("good.zip")
        ZipOutputStream(Files.newOutputStream(good)).use { z ->
            z.putNextEntry(ZipEntry("World/level.dat")); z.write(1); z.closeEntry()
        }
        SafeZip.extract(good, tmp.resolve("world"))
        assertTrue(Files.exists(tmp.resolve("world/level.dat")))

        val evil = tmp.resolve("evil.zip")
        ZipOutputStream(Files.newOutputStream(evil)).use { z ->
            z.putNextEntry(ZipEntry("../../escape.txt")); z.write(1); z.closeEntry()
        }
        assertFailsWith<LauncherException> { SafeZip.extract(evil, tmp.resolve("out")) }
        assertFalse(Files.exists(tmp.resolve("escape.txt")))
    }

    @Test fun `modpack paths are fenced into allowed folders`() {
        assertEquals("mods/sodium.jar", ModpackInstaller.safeRelative("mods/sodium.jar"))
        assertNull(ModpackInstaller.safeRelative("../mods/x.jar"))
        assertNull(ModpackInstaller.safeRelative("saves/world/level.dat"))
        assertNull(ModpackInstaller.safeRelative("C:/Windows/x.dll"))
        assertNull(ModpackInstaller.safeRelative("/etc/passwd"))
    }

    @Test fun `ed25519 catalog signatures verify and tampering is detected`() {
        val kp = KeyPairGenerator.getInstance("Ed25519").generateKeyPair()
        val data = """{"schema_version":2}""".toByteArray()
        val sig = Signature.getInstance("Ed25519").run { initSign(kp.private); update(data); sign() }
        val pub = Base64.getEncoder().encodeToString(kp.public.encoded)
        val sigB64 = Base64.getEncoder().encodeToString(sig)
        assertTrue(Signatures.verify(data, sigB64, pub))
        assertFalse(Signatures.verify(data + ' '.code.toByte(), sigB64, pub))
        assertFalse(Signatures.verify(data, sigB64, "not-a-key"))
    }

    @Test fun `offline uuid matches the server's derivation`() {
        assertEquals("b50ad385-829d-3141-a216-7e7d7539ba7f", OfflineUuid.of("Notch").toString())
    }

    @Test fun `servers dat round-trips and keeps other entries`() {
        val file = tmp.resolve("servers.dat")
        ServersDat.upsert(file, "Friend", "example.org")
        ServersDat.upsert(file, "Castle", "127.0.0.1:25566")
        ServersDat.upsert(file, "Castle", "127.0.0.1:40000")
        val entries = ServersDat.read(file)
        assertEquals(listOf("Castle" to "127.0.0.1:40000", "Friend" to "example.org"), entries.map { it.name to it.ip })
    }

    @Test fun `options txt swaps the old pack for the converted one`() {
        val file = tmp.resolve("options.txt")
        Files.writeString(file, "fov:0.5\nresourcePacks:[\"vanilla\",\"file/Resource Pack\"]\n")
        GameOptions.enableResourcePack(file, "Resource Pack (1.20.1).zip", compatible = true, stale = listOf("Resource Pack"))
        val text = Files.readString(file)
        assertTrue("resourcePacks:[\"vanilla\",\"file/Resource Pack (1.20.1).zip\"]" in text, text)
        assertTrue("fov:0.5" in text)
    }

    @Test fun `library coordinates and native arch filtering`() {
        assertEquals("org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1-natives-linux.jar", Library.pathOf("org.lwjgl:lwjgl:3.3.1:natives-linux"))
        val rules = JsonParser.parseString("""[{"action":"allow"},{"action":"disallow","os":{"name":"${Platform.current.mojangName}"}}]""").asJsonArray
        assertFalse(Rules.allow(rules))
        assertTrue(Rules.allow(null))
        assertEquals(Platform.arch == "aarch64", Rules.nativesMatchArch("natives-macos-arm64"))
    }

    @Test fun `secret store never writes plain text`() {
        val store = SecretStore(tmp.resolve("secure"))
        store.put("t", "super-secret-refresh-token")
        assertEquals("super-secret-refresh-token", store.get("t"))
        Files.walk(tmp.resolve("secure")).use { s ->
            s.filter(Files::isRegularFile).forEach { f ->
                assertFalse("super-secret" in String(Files.readAllBytes(f), Charsets.ISO_8859_1))
            }
        }
        store.delete("t")
        assertNull(store.get("t"))
    }

    @Test fun `settings reject hostile values`() {
        val file = tmp.resolve("settings.json")
        Files.writeString(file, """{"server_ram_mb":999999,"client_ram_mb":-5,"offline_name":"x;rm -rf /","memory_profile":"EVIL"}""")
        val s = Settings(file).load()
        assertEquals(32768, s.serverRamMb)
        assertEquals(0, s.clientRamMb)
        assertEquals("", s.offlineName)
        assertEquals(Settings.MemoryProfile.BALANCED, s.memoryProfile)
        assertEquals("127.0.0.1", s.bindAddress)
    }

    @Test fun `offline bundle round-trips and rejects tampering`() {
        val src = AppPaths(tmp.resolve("a")).ensure()
        val lib = src.root.resolve("resources/client/1.20.1/minecraft/libraries/x.jar")
        Files.createDirectories(lib.parent); Files.writeString(lib, "jar")
        Files.writeString(src.root.resolve("install_state.json"), "{}")
        val bundle = tmp.resolve("b.wizardpack")
        OfflineBundle.export(src, bundle)

        val dst = AppPaths(tmp.resolve("b")).ensure()
        OfflineBundle.import(dst, bundle)
        assertEquals("jar", Files.readString(dst.root.resolve("resources/client/1.20.1/minecraft/libraries/x.jar")))

        val tampered = tmp.resolve("t.wizardpack")
        java.util.zip.ZipFile(bundle.toFile()).use { zin ->
            ZipOutputStream(Files.newOutputStream(tampered)).use { zout ->
                zin.entries().asSequence().forEach { e ->
                    zout.putNextEntry(ZipEntry(e.name))
                    zout.write(if (e.name.endsWith("x.jar")) "evil".toByteArray() else zin.getInputStream(e).readBytes())
                    zout.closeEntry()
                }
            }
        }
        val fresh = AppPaths(tmp.resolve("c")).ensure()
        assertFailsWith<LauncherException> { OfflineBundle.import(fresh, tampered) }
        assertFalse(Files.exists(fresh.root.resolve("resources/client")))
    }

    @Test fun `install fingerprints change with their inputs`() {
        assertEquals(InstallState.fingerprint("a", 1), InstallState.fingerprint("a", 1))
        assertTrue(InstallState.fingerprint("a", 1) != InstallState.fingerprint("a", 2))
    }

    @Test fun `bundled catalog parses`() {
        val c = Catalog.bundled()
        assertEquals("1.20.1", c.minecraft.clientVersion)
        assertEquals("hosted", c.server.mode)
        assertEquals(6, c.resource("resource_pack").convertFrom)
    }

    @Test fun `model based culling is switched off so remodelled blocks cull like vanilla`() {
        val game = tmp.resolve("game")
        val defaults = game.resolve("config/yosbr/config/moreculling.toml")
        Files.createDirectories(defaults.parent)
        Files.write(defaults, listOf("cloudCulling = true", "useBlockStateCulling = true", "", "[modCompatibility]", "minecraft = true"))
        ModConfigs.enforce(game)
        val live = Files.readAllLines(game.resolve("config/moreculling.toml"))
        assertTrue("useBlockStateCulling = false" in live)
        assertTrue("cloudCulling = true" in live)
        assertTrue(live.indexOf("useBlockStateCulling = false") < live.indexOf("[modCompatibility]"))
        assertTrue("useBlockStateCulling = false" in Files.readAllLines(defaults))
        Files.write(game.resolve("config/moreculling.toml"), listOf("[modCompatibility]", "minecraft = true"))
        ModConfigs.enforce(game)
        assertEquals("useBlockStateCulling = false", Files.readAllLines(game.resolve("config/moreculling.toml")).first())
        assertFalse(ModConfigs.patchToml(game.resolve("config/moreculling.toml"), mapOf("useBlockStateCulling" to "false")))
    }
}
