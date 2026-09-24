package dev.wizardlauncher.core.security

import com.sun.jna.platform.win32.Crypt32Util
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Platform
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.PosixFilePermissions
import java.security.SecureRandom
import java.util.Base64
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Where the Microsoft refresh token lives. Never in plain text:
 *
 * | OS      | Primary                                 | Fallback                   |
 * |---------|-----------------------------------------|----------------------------|
 * | Windows | DPAPI (bound to the Windows user)       | -                          |
 * | macOS   | Keychain via `security`                 | AES-GCM file               |
 * | Linux   | Secret Service via `secret-tool`        | AES-GCM file               |
 *
 * The AES-GCM fallback keeps its key in a separate owner-only (0600) file.
 * That protects against the token being read out of a copied data folder or
 * a backup, not against malware running as the same user - which no
 * user-space store can.
 */
class SecretStore(private val dir: Path) {
    private val service = "WizardLauncher"

    fun put(name: String, value: String) {
        Files.createDirectories(dir)
        restrict(dir)
        when {
            Platform.current == Platform.WINDOWS -> writeFile(name, dpapiProtect(value.toByteArray()))
            Platform.current == Platform.MACOS && keychainPut(name, value) -> deleteFile(name)
            Platform.current == Platform.LINUX && secretToolPut(name, value) -> deleteFile(name)
            else -> writeFile(name, aesEncrypt(value.toByteArray()))
        }
    }

    fun get(name: String): String? = runCatching {
        when (Platform.current) {
            Platform.WINDOWS -> readFile(name)?.let { String(dpapiUnprotect(it)) }
            Platform.MACOS -> keychainGet(name) ?: readFile(name)?.let { String(aesDecrypt(it)) }
            Platform.LINUX -> secretToolGet(name) ?: readFile(name)?.let { String(aesDecrypt(it)) }
        }
    }.onFailure { Log.file("Secret '$name' could not be read: ${it.message}") }.getOrNull()

    fun delete(name: String) {
        deleteFile(name)
        runCatching {
            when (Platform.current) {
                Platform.MACOS -> run("security", "delete-generic-password", "-s", service, "-a", name)
                Platform.LINUX -> run("secret-tool", "clear", "service", service, "account", name)
                else -> Unit
            }
        }
    }

    // --- Windows ---------------------------------------------------------
    private fun dpapiProtect(data: ByteArray): ByteArray = Crypt32Util.cryptProtectData(data)
    private fun dpapiUnprotect(data: ByteArray): ByteArray = Crypt32Util.cryptUnprotectData(data)

    // --- macOS -----------------------------------------------------------
    private fun keychainPut(name: String, value: String): Boolean =
        // -U updates in place. The secret goes through argv briefly; `security`
        // has no stdin mode for add-generic-password.
        run("security", "add-generic-password", "-U", "-s", service, "-a", name, "-w", value)?.first == 0

    private fun keychainGet(name: String): String? =
        run("security", "find-generic-password", "-s", service, "-a", name, "-w")
            ?.takeIf { it.first == 0 }?.second?.trim()?.takeIf { it.isNotEmpty() }

    // --- Linux -----------------------------------------------------------
    private fun secretToolPut(name: String, value: String): Boolean =
        run("secret-tool", "store", "--label=Wizard Launcher", "service", service, "account", name, stdin = value)?.first == 0

    private fun secretToolGet(name: String): String? =
        run("secret-tool", "lookup", "service", service, "account", name)
            ?.takeIf { it.first == 0 }?.second?.takeIf { it.isNotEmpty() }

    private fun run(vararg cmd: String, stdin: String? = null): Pair<Int, String>? = runCatching {
        val p = ProcessBuilder(*cmd).redirectErrorStream(false).start()
        p.outputStream.use { out -> stdin?.let { out.write(it.toByteArray()) } }
        val text = p.inputStream.readBytes().toString(Charsets.UTF_8)
        if (!p.waitFor(10, TimeUnit.SECONDS)) { p.destroyForcibly(); return null }
        p.exitValue() to text
    }.getOrNull()

    // --- AES-GCM file fallback -------------------------------------------
    private fun key(): ByteArray {
        val keyFile = dir.resolve(".key")
        if (Files.exists(keyFile)) return Base64.getDecoder().decode(Files.readString(keyFile).trim())
        val key = ByteArray(32).also(SecureRandom()::nextBytes)
        writeAtomic(keyFile, Base64.getEncoder().encode(key))
        return key
    }

    private fun aesEncrypt(plain: ByteArray): ByteArray {
        val iv = ByteArray(12).also(SecureRandom()::nextBytes)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key(), "AES"), GCMParameterSpec(128, iv))
        return iv + cipher.doFinal(plain)
    }

    private fun aesDecrypt(blob: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key(), "AES"), GCMParameterSpec(128, blob, 0, 12))
        return cipher.doFinal(blob, 12, blob.size - 12)
    }

    // --- files -----------------------------------------------------------
    private fun file(name: String) = dir.resolve("$name.bin")
    private fun readFile(name: String): ByteArray? = file(name).takeIf(Files::exists)?.let(Files::readAllBytes)
    private fun writeFile(name: String, data: ByteArray) = writeAtomic(file(name), data)
    private fun deleteFile(name: String) { Files.deleteIfExists(file(name)) }

    private fun writeAtomic(target: Path, data: ByteArray) {
        val tmp = target.resolveSibling(target.fileName.toString() + ".tmp")
        Files.write(tmp, data)
        restrict(tmp)
        Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }

    private fun restrict(path: Path) {
        if (Platform.current == Platform.WINDOWS) return
        runCatching {
            Files.setPosixFilePermissions(path, PosixFilePermissions.fromString(if (Files.isDirectory(path)) "rwx------" else "rw-------"))
        }
    }
}
