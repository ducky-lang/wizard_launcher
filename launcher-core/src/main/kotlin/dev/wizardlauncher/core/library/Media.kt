package dev.wizardlauncher.core.library

import dev.wizardlauncher.core.Log
import java.awt.RenderingHints
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant
import javax.imageio.IIOImage
import javax.imageio.ImageIO
import javax.imageio.ImageWriteParam
import java.util.zip.ZipFile

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

class HeroArt(
    private val gameDir: Path,
    private val cacheDir: Path,
    private val screenshots: ScreenshotLibrary,
    private val extraPacks: List<Path> = emptyList(),
) {
    private class Source(val key: String, val stamp: Long, val read: () -> BufferedImage?)

    fun image(): ByteArray? {
        val source = packArt() ?: screenshots.list().firstOrNull()?.let { screenshots.resolve(it.file) }?.let(::fileSource) ?: return null
        val cached = cacheDir.resolve("hero").resolve("hero-${source.key.hashCode().toUInt()}-${source.stamp}.jpg")
        if (Files.isRegularFile(cached)) return Files.readAllBytes(cached)
        return runCatching {
            val image = source.read() ?: return null
            val top = photoTop(image)
            val height = image.height - top
            val width = minOf(1920, image.width)
            val outHeight = (height * width.toDouble() / image.width).toInt().coerceAtLeast(1)
            val out = BufferedImage(width, outHeight, BufferedImage.TYPE_INT_RGB)
            out.createGraphics().apply {
                setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC)
                setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY)
                drawImage(image, 0, 0, width, outHeight, 0, top, image.width, image.height, null)
                dispose()
            }
            val bytes = jpeg(out, 0.9f)
            Files.createDirectories(cached.parent)
            Files.list(cached.parent).use { old -> old.filter { it != cached }.forEach { runCatching { Files.deleteIfExists(it) } } }
            Files.write(cached, bytes)
            bytes
        }.onFailure { Log.file("Home artwork could not be prepared: ${it.message}") }.getOrNull()
    }

    private fun jpeg(image: BufferedImage, quality: Float): ByteArray {
        val writer = ImageIO.getImageWritersByFormatName("jpg").next()
        val out = ByteArrayOutputStream()
        ImageIO.createImageOutputStream(out).use { stream ->
            writer.output = stream
            val params = writer.defaultWriteParam.apply {
                compressionMode = ImageWriteParam.MODE_EXPLICIT
                compressionQuality = quality
            }
            writer.write(null, IIOImage(image, null, null), params)
            writer.dispose()
        }
        return out.toByteArray()
    }

    private fun fileSource(path: Path) = Source(path.toString(), Files.getLastModifiedTime(path).toMillis()) { ImageIO.read(path.toFile()) }

    private fun packArt(): Source? {
        val packs = gameDir.resolve("resourcepacks")
        val installed = if (Files.isDirectory(packs)) Files.list(packs).use { it.toList() } else emptyList()
        return (installed + extraPacks)
            .sortedBy { if (it.fileName.toString().startsWith("Resource Pack")) 0 else 1 }
            .firstNotNullOfOrNull(::artIn)
    }

    private fun artIn(pack: Path): Source? = runCatching {
        when {
            Files.isDirectory(pack) -> pack.resolve(ART).takeIf(Files::isRegularFile)?.let(::fileSource)
            Files.isRegularFile(pack) && pack.fileName.toString().lowercase().endsWith(".zip") -> ZipFile(pack.toFile()).use { zip ->
                zip.getEntry(ART)?.let {
                    Source("$pack!$ART", Files.getLastModifiedTime(pack).toMillis()) {
                        ZipFile(pack.toFile()).use { z -> z.getInputStream(z.getEntry(ART)).use(ImageIO::read) }
                    }
                }
            }
            else -> null
        }
    }.getOrNull()

    private fun photoTop(image: BufferedImage): Int {
        var y = image.height - 1
        while (y > 0 && !mostlyDark(image, y - 1)) y--
        return if (image.height - y < image.height / 4) 0 else y
    }

    private fun mostlyDark(image: BufferedImage, y: Int): Boolean {
        val step = maxOf(1, image.width / 64)
        var dark = 0
        var total = 0
        for (x in 0 until image.width step step) {
            val rgb = image.getRGB(x, y)
            val alpha = rgb ushr 24
            val luma = ((rgb shr 16 and 0xFF) * 3 + (rgb shr 8 and 0xFF) * 6 + (rgb and 0xFF)) / 10
            if (alpha < 16 || luma < 10) dark++
            total++
        }
        return dark * 2 > total
    }

    companion object {
        const val ART = "assets/minecraft/textures/guis/main_menu/background/background.png"
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
