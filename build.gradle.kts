import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("jvm") version "2.0.21" apply false
}

// Everything targets Java 17: it is the runtime Minecraft 1.20.1 needs, so
// one bundled JRE runs the launcher, the client and the world server alike -
// nothing has to be downloaded to play.
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

/**
 * installer/app.ico, installer/app.icns and installer/linux/app.png from
 * assets/floo-logo.png. Both ICO (Vista+) and ICNS ('ic08'/'ic09') may embed
 * PNG data directly, so no image library is needed - only ImageIO.
 */
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
