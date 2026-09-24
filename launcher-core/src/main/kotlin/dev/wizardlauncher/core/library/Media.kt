package dev.wizardlauncher.core.library

import dev.wizardlauncher.core.Log
import java.awt.RenderingHints
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant
import javax.imageio.ImageIO

data class Screenshot(val file: String, val takenAt: Long, val sizeBytes: Long)

class ScreenshotLibrary(private val gameDir: Path, private val cacheDir: Path) {
    private val dir: Path get() = gameDir.resolve("screenshots")

    fun list(): List<Screenshot> {
        if (!Files.isDirectory(dir)) return emptyList()
        return Files.list(dir).use { s -> s.filter { it.fileName.toString().lowercase().endsWith(".png") }.toList() }
            .map { Screenshot(it.fileName.toString(), Files.getLastModifiedTime(it).toMillis(), Files.size(it)) }
            .sortedByDescending { it.takenAt }
    }

    fun resolve(name: String): Path? {
        if (!name.lowercase().endsWith(".png") || name.contains('/') || name.contains('\\') || name.startsWith(".")) return null
        return dir.resolve(name).takeIf(Files::isRegularFile)
    }

    fun thumbnail(name: String): ByteArray? {
        val source = resolve(name) ?: return null
        val stamp = Files.getLastModifiedTime(source).toMillis()
        val cached = cacheDir.resolve("thumbs").resolve("${name.removeSuffix(".png")}-$stamp.jpg")
        if (Files.isRegularFile(cached)) return Files.readAllBytes(cached)
        return runCatching {
            val image = ImageIO.read(source.toFile()) ?: return null
            val width = 480
            val height = (image.height * width.toDouble() / image.width).toInt().coerceAtLeast(1)
            val thumb = BufferedImage(width, height, BufferedImage.TYPE_INT_RGB)
            thumb.createGraphics().apply {
                setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR)
                drawImage(image, 0, 0, width, height, null)
                dispose()
            }
            val bytes = ByteArrayOutputStream().also { ImageIO.write(thumb, "jpg", it) }.toByteArray()
            Files.createDirectories(cached.parent)
            Files.write(cached, bytes)
            bytes
        }.onFailure { Log.file("Thumbnail failed for $name: ${it.message}") }.getOrNull()
    }

    fun delete(name: String) {
        resolve(name)?.let(Files::deleteIfExists)
    }
}

object Images {
    fun face(skin: ByteArray, size: Int = 64): ByteArray? = runCatching {
        val image = ImageIO.read(skin.inputStream()) ?: return null
        val out = BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB)
        val g = out.createGraphics()
        g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_NEAREST_NEIGHBOR)
        g.drawImage(image, 0, 0, size, size, 8, 8, 16, 16, null)
        if (image.width >= 48) g.drawImage(image, 0, 0, size, size, 40, 8, 48, 16, null)
        g.dispose()
        ByteArrayOutputStream().also { ImageIO.write(out, "png", it) }.toByteArray()
    }.getOrNull()

    fun now() = Instant.now().toEpochMilli()
}
