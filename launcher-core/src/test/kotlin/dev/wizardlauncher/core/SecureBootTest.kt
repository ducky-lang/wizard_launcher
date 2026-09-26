package dev.wizardlauncher.core

import java.io.File
import java.util.Base64
import java.util.concurrent.TimeUnit
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

object FakeGameMain {
    @JvmStatic fun main(args: Array<String>) {
        println("ARGS=" + args.joinToString("|"))
        println("CMDLINE=" + ProcessHandle.current().info().commandLine().orElse("?"))
    }
}

class SecureBootTest {
    @Test fun `the access token reaches the game but never the command line`() {
        val token = "eyJhbGciOiJIUzI1NiJ9.SECRET-TOKEN-PAYLOAD.signature"
        val cp = System.getProperty("java.class.path") + File.pathSeparator + System.getProperty("wizard.bootJar")
        val java = File(System.getProperty("java.home"), "bin/java").path
        val process = ProcessBuilder(java, "-cp", cp, "dev.wizardlauncher.boot.SecureBoot").redirectErrorStream(true).start()
        val b64 = Base64.getEncoder()
        val args = listOf("--username", "Harry", "--accessToken", token)
        process.outputStream.bufferedWriter().use { w ->
            w.write("WIZARD-BOOT/1\n")
            w.write(b64.encodeToString(FakeGameMain::class.java.name.toByteArray()) + "\n")
            w.write("${args.size}\n")
            args.forEach { w.write(b64.encodeToString(it.toByteArray()) + "\n") }
        }
        val output = process.inputStream.readBytes().toString(Charsets.UTF_8)
        assertTrue(process.waitFor(30, TimeUnit.SECONDS))
        assertEquals(0, process.exitValue(), output)
        val received = output.lines().first { it.startsWith("ARGS=") }
        assertEquals("ARGS=--username|Harry|--accessToken|$token", received)
        val cmdline = output.lines().first { it.startsWith("CMDLINE=") }
        assertFalse(token in cmdline, cmdline)
        assertFalse("SECRET-TOKEN" in cmdline, cmdline)
    }

    @Test fun `a launch without the header is refused`() {
        val cp = System.getProperty("wizard.bootJar")
        val java = File(System.getProperty("java.home"), "bin/java").path
        val process = ProcessBuilder(java, "-cp", cp, "dev.wizardlauncher.boot.SecureBoot").redirectErrorStream(true).start()
        process.outputStream.use { it.write("junk\n".toByteArray()) }
        assertTrue(process.waitFor(30, TimeUnit.SECONDS))
        assertTrue(process.exitValue() != 0)
    }
}
