import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("jvm") version "2.0.21" apply false
}

subprojects {
    group = "dev.wizardlauncher"
    version = property("launcherVersion") as String

    plugins.withType<JavaPlugin> {
        extensions.configure<JavaPluginExtension> {
            sourceCompatibility = JavaVersion.VERSION_17
            targetCompatibility = JavaVersion.VERSION_17
        }
        tasks.withType<JavaCompile>().configureEach {
            options.release.set(17)
            options.encoding = "UTF-8"
        }
        tasks.withType<Test>().configureEach { useJUnitPlatform() }
    }
    plugins.withId("org.jetbrains.kotlin.jvm") {
        extensions.configure<org.jetbrains.kotlin.gradle.dsl.KotlinJvmProjectExtension> {
            compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
        }
    }
}

tasks.register("makeIcons") {
    group = "distribution"
    val source = file("assets/floo-logo.png")
    val outputs = listOf(file("installer/app.ico"), file("installer/app.icns"), file("installer/linux/app.png"))
    inputs.file(source)
    outputs.forEach { this.outputs.file(it) }
    doLast {
        fun png(size: Int): ByteArray {
            val src = javax.imageio.ImageIO.read(source)
            val img = java.awt.image.BufferedImage(size, size, java.awt.image.BufferedImage.TYPE_INT_ARGB)
            img.createGraphics().apply {
                setRenderingHint(java.awt.RenderingHints.KEY_INTERPOLATION, java.awt.RenderingHints.VALUE_INTERPOLATION_BICUBIC)
                drawImage(src, 0, 0, size, size, null); dispose()
            }
            return java.io.ByteArrayOutputStream().also { javax.imageio.ImageIO.write(img, "png", it) }.toByteArray()
        }
        val ico = java.io.ByteArrayOutputStream()
        val le = { v: Int, n: Int -> ByteArray(n) { i -> (v shr (8 * i)).toByte() } }
        val big = png(256)
        ico.write(le(0, 2)); ico.write(le(1, 2)); ico.write(le(1, 2))
        ico.write(byteArrayOf(0, 0, 0, 0)); ico.write(le(1, 2)); ico.write(le(32, 2))
        ico.write(le(big.size, 4)); ico.write(le(22, 4)); ico.write(big)
        outputs[0].writeBytes(ico.toByteArray())

        val be = { v: Int -> java.nio.ByteBuffer.allocate(4).putInt(v).array() }
        val chunks = listOf("ic08" to png(256), "ic09" to png(512))
        val icns = java.io.ByteArrayOutputStream()
        icns.write("icns".toByteArray()); icns.write(be(8 + chunks.sumOf { 8 + it.second.size }))
        chunks.forEach { (type, data) -> icns.write(type.toByteArray()); icns.write(be(8 + data.size)); icns.write(data) }
        outputs[1].writeBytes(icns.toByteArray())
        outputs[2].parentFile.mkdirs()
        outputs[2].writeBytes(png(256))
    }
}

val bundledDir = layout.buildDirectory.dir("bundled")

data class PinnedDownload(val url: String, val path: String, val algorithm: String, val digest: String)

val pinnedDownloads = listOf(
    PinnedDownload(
        "https://piston-data.mojang.com/v1/objects/1b557e7b033b583cd9f66746b7a9ab1ec1673ced/server.jar",
        "servers/1.16.5/server.jar", "SHA-1", "1b557e7b033b583cd9f66746b7a9ab1ec1673ced",
    ),
    PinnedDownload(
        "https://github.com/ViaVersion/ViaProxy/releases/download/v3.4.12/ViaProxy-3.4.12.jar",
        "proxy/ViaProxy.jar", "SHA-256", "32ce9ad871aeb03286823c29da262ebd75992864e7857db283f103525c7fc0cb",
    ),
)

tasks.register("fetchGameJars") {
    group = "distribution"
    outputs.dir(bundledDir)
    doLast {
        fun digest(file: File, algorithm: String): String {
            val md = java.security.MessageDigest.getInstance(algorithm)
            file.inputStream().use { input ->
                val buffer = ByteArray(1 shl 16)
                while (true) { val n = input.read(buffer); if (n < 0) break; md.update(buffer, 0, n) }
            }
            return md.digest().joinToString("") { "%02x".format(it) }
        }
        for (pin in pinnedDownloads) {
            val target = bundledDir.get().file(pin.path).asFile
            if (target.isFile && digest(target, pin.algorithm) == pin.digest) continue
            target.parentFile.mkdirs()
            val part = File(target.path + ".part")
            java.net.URI(pin.url).toURL().openStream().use { input -> part.outputStream().use { input.copyTo(it) } }
            val actual = digest(part, pin.algorithm)
            if (actual != pin.digest) {
                part.delete()
                throw GradleException("${pin.url}: ${pin.algorithm} mismatch, expected ${pin.digest}, got $actual")
            }
            part.renameTo(target)
        }
    }
}
