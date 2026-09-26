package dev.wizardlauncher.core.install

import com.google.gson.JsonObject
import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.security.Hashes
import java.nio.file.Files
import java.nio.file.Path
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

object OfflineBundle {
    private const val MANIFEST = "wizard-bundle.json"
    private val INCLUDE = listOf("resources/client", "resources/copy", "resources/runtimes", "install_state.json")

    fun export(paths: AppPaths, target: Path, progress: Progress = Progress.NONE) {
        val files = INCLUDE.map { paths.root.resolve(it) }.filter(Files::exists).flatMap { root ->
            if (Files.isDirectory(root)) Files.walk(root).use { s -> s.filter(Files::isRegularFile).toList() } else listOf(root)
        }.filterNot { it.fileName.toString().endsWith(".part") }
        val manifest = JsonObject().apply {
            addProperty("format", 2)
            addProperty("client_version", Catalog.current.minecraft.clientVersion)
            add("instances", com.google.gson.JsonArray().apply {
                Catalog.current.instances.filter { Files.isDirectory(paths.gameDir(it.id)) }.forEach { add("${it.id}:${it.modpack.id}@${it.modpack.version}") }
            })
        }
        val hashes = JsonObject()
        val tmp = target.resolveSibling(target.fileName.toString() + ".tmp")
        ZipOutputStream(Files.newOutputStream(tmp)).use { zip ->
            files.forEachIndexed { i, file ->
                val rel = paths.root.relativize(file).toString().replace('\\', '/')
                zip.putNextEntry(ZipEntry(rel))
                val digest = java.security.MessageDigest.getInstance("SHA-256")
                Files.newInputStream(file).use { input ->
                    val buf = ByteArray(1 shl 16)
                    while (true) { val n = input.read(buf); if (n < 0) break; digest.update(buf, 0, n); zip.write(buf, 0, n) }
                }
                zip.closeEntry()
                hashes.addProperty(rel, digest.digest().joinToString("") { "%02x".format(it) })
                if (i % 100 == 0) progress.update(i.toDouble() / files.size, "Exporting  ·  $i of ${files.size} files")
            }
            manifest.add("files", hashes)
            zip.putNextEntry(ZipEntry(MANIFEST))
            zip.write(Json.gson.toJson(manifest).toByteArray())
            zip.closeEntry()
        }
        Files.move(tmp, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING)
        Log.info("Offline bundle written: $target (${files.size} files).")
    }

    fun import(paths: AppPaths, bundle: Path, progress: Progress = Progress.NONE) {
        ZipFile(bundle.toFile()).use { zip ->
            val manifest = zip.getEntry(MANIFEST)?.let { com.google.gson.JsonParser.parseString(zip.getInputStream(it).readBytes().toString(Charsets.UTF_8)).asJsonObject }
                ?: throw LauncherException("This is not a Wizard Launcher offline bundle.")
            if ((manifest.get("format")?.asInt ?: 1) < 2 && manifest.get("client_version")?.asString != Catalog.current.minecraft.clientVersion) {
                throw LauncherException("This bundle is for Minecraft ${manifest.get("client_version")?.asString}, not ${Catalog.current.minecraft.clientVersion}.")
            }
            val files = manifest.getAsJsonObject("files").entrySet()
            val staging = paths.root.resolve("import_staging")
            SafeZip.deleteTree(staging)
            val root = Files.createDirectories(staging).toRealPath()
            files.forEachIndexed { i, (rel, hash) ->
                if (INCLUDE.none { rel == it || rel.startsWith("$it/") }) throw LauncherException("The bundle contains an unexpected file ($rel) and was rejected.")
                val target = root.resolve(rel).normalize()
                if (!target.startsWith(root)) throw LauncherException("The bundle contains an unsafe path and was rejected.")
                val entry = zip.getEntry(rel) ?: throw LauncherException("The bundle is incomplete ($rel missing).")
                Files.createDirectories(target.parent)
                zip.getInputStream(entry).use { Files.copy(it, target) }
                if (!Hashes.matches(Hashes.of(target, "SHA-256"), hash.asString)) {
                    SafeZip.deleteTree(staging)
                    throw LauncherException("The bundle is damaged ($rel does not match its checksum). Nothing was changed.")
                }
                if (i % 100 == 0) progress.update(i.toDouble() / files.size, "Verifying bundle  ·  $i of ${files.size}")
            }

            progress.update(null, "Installing bundle...")
            SafeZip.copyTree(staging, paths.root)
            SafeZip.deleteTree(staging)
            paths.ensure()
            Log.info("Offline bundle installed (${files.size} files). You can play without internet now.")
        }
    }
}
