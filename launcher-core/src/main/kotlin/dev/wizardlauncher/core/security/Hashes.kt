package dev.wizardlauncher.core.security

import java.io.InputStream
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest

object Hashes {
    fun of(path: Path, algorithm: String): String = Files.newInputStream(path).use { of(it, algorithm) }

    fun of(input: InputStream, algorithm: String): String {
        val digest = MessageDigest.getInstance(algorithm)
        val buffer = ByteArray(1 shl 16)
        while (true) {
            val n = input.read(buffer)
            if (n < 0) break
            digest.update(buffer, 0, n)
        }
        return digest.digest().toHex()
    }

    fun of(bytes: ByteArray, algorithm: String): String = MessageDigest.getInstance(algorithm).digest(bytes).toHex()

    fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

    /** Constant-time comparison of two hex digests. */
    fun matches(actual: String, expected: String): Boolean =
        MessageDigest.isEqual(actual.lowercase().toByteArray(), expected.lowercase().toByteArray())
}

/** An expected digest: `sha1`, `sha256` or `sha512`. */
data class Checksum(val algorithm: String, val hex: String) {
    val javaName get() = when (algorithm.lowercase()) {
        "sha1" -> "SHA-1"; "sha256" -> "SHA-256"; "sha512" -> "SHA-512"
        else -> throw IllegalArgumentException("unsupported hash $algorithm")
    }

    fun newDigest(): MessageDigest = MessageDigest.getInstance(javaName)

    fun verify(path: Path) = Hashes.matches(Hashes.of(path, javaName), hex)

    companion object {
        fun sha1(hex: String?) = hex?.takeIf { it.isNotBlank() }?.let { Checksum("sha1", it) }
        fun sha256(hex: String?) = hex?.takeIf { it.isNotBlank() }?.let { Checksum("sha256", it) }
        fun sha512(hex: String?) = hex?.takeIf { it.isNotBlank() }?.let { Checksum("sha512", it) }
    }
}
