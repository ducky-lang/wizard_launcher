package dev.wizardlauncher.core

import com.google.gson.JsonParser
import dev.wizardlauncher.core.auth.Account
import dev.wizardlauncher.core.game.ClientRunner
import dev.wizardlauncher.core.game.ProcessSupervisor
import dev.wizardlauncher.core.install.Library
import dev.wizardlauncher.core.install.VersionProfile
import java.io.File
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

class ClientRunnerTest {
    @TempDir lateinit var tmp: Path

    @Test fun `the classpath holds whole files, never path segments`() {
        val paths = AppPaths(tmp.resolve("Users/Harry Potter/AppData/Local/WizardLauncher")).ensure()
        val lib = Library("org.example:lib:1.0", "org/example/lib/1.0/lib-1.0.jar", "https://libraries.minecraft.net/x", null)
        Files.createDirectories(paths.libraries.resolve(lib.path).parent)
        Files.writeString(paths.libraries.resolve(lib.path), "jar")
        val clientJar = paths.versions.resolve("1.20.1/1.20.1.jar")
        Files.createDirectories(clientJar.parent); Files.writeString(clientJar, "jar")
        val boot = tmp.resolve("app/wizard-client-boot.jar")
        Files.createDirectories(boot.parent); Files.writeString(boot, "jar")
        val jvm = JsonParser.parseString("""["-cp", "${'$'}{classpath}"]""").asJsonArray.toList()
        val profile = VersionProfile("fabric-loader-0.15.11-1.20.1", "net.fabricmc.loader.impl.launch.knot.KnotClient", listOf(lib),
            clientJar, "5", emptyList(), jvm, paths.versions.resolve("1.20.1/natives"), "release")
        val settings = Settings(tmp.resolve("settings.json"))
        val plan = ClientRunner(paths, settings, ProcessSupervisor(tmp.resolve("state.json")), Path.of("java"), boot)
            .plan(profile, Account.offline("Harry"), "127.0.0.1:25566")
        val cp = plan.command[plan.command.indexOf("-cp") + 1].split(File.pathSeparator)
        assertEquals(listOf(paths.libraries.resolve(lib.path).toString(), clientJar.toString(), boot.toString()), cp)
        assertTrue(cp.all { Path.of(it).isAbsolute })
        assertFalse(plan.command.any { it.contains("Harry") && it.contains("accessToken") })
        assertTrue("--quickPlayMultiplayer" !in plan.command)
    }
}
