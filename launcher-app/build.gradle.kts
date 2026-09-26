plugins {
    kotlin("jvm")
    application
}

val jcefNatives: String = run {
    val os = org.gradle.internal.os.OperatingSystem.current()
    val arch = if (System.getProperty("os.arch").lowercase() in setOf("aarch64", "arm64")) "arm64" else "amd64"
    when {
        os.isWindows -> "windows-$arch"
        os.isMacOsX -> "macosx-$arch"
        else -> "linux-$arch"
    }
}

dependencies {
    implementation(project(":launcher-core"))
    implementation(project(":pack-legacy"))
    implementation("com.google.code.gson:gson:2.11.0")
    implementation("com.formdev:flatlaf:3.5.2")
    implementation("me.friwi:jcefmaven:146.0.10")
    runtimeOnly("me.friwi:jcef-natives-$jcefNatives:jcef-d3de827+cef-146.0.10+g8219561+chromium-146.0.7680.179")
}

val uiJvmArgs = listOf(
    "--add-opens=java.desktop/sun.awt=ALL-UNNAMED",
    "--add-opens=java.desktop/java.awt.peer=ALL-UNNAMED",
    "--add-opens=java.desktop/sun.lwawt=ALL-UNNAMED",
    "--add-opens=java.desktop/sun.lwawt.macosx=ALL-UNNAMED",
    "--add-exports=java.desktop/sun.awt=ALL-UNNAMED",
)

application {
    mainClass.set("dev.wizardlauncher.app.MainKt")
    applicationName = "WizardLauncher"
    applicationDefaultJvmArgs = listOf("-Xmx512m") + uiJvmArgs
}

tasks.jar {
    archiveFileName.set("wizard-launcher.jar")
    manifest { attributes("Main-Class" to "dev.wizardlauncher.app.MainKt", "Implementation-Version" to project.version) }
}

evaluationDependsOn(":server-host")
evaluationDependsOn(":client-boot")

val helperJars = files(
    project(":server-host").tasks.named("jar"),
    project(":client-boot").tasks.named("jar"),
)

val legacyModJars = rootProject.fileTree("legacy-mod/build/libs") { include("wizard-legacy-packs-*.jar") }

val stageResources = tasks.register<Sync>("stageResources") {
    mustRunAfter(":fetchGameJars")
    from(rootProject.file("resources"))
    from(rootProject.layout.buildDirectory.dir("bundled"))
    from(legacyModJars) { into("mods") }
    into(layout.buildDirectory.dir("staged-resources"))
}

distributions {
    main {
        contents {
            into("lib") { from(helperJars) }
            into("resources") { from(stageResources) }
        }
    }
}

tasks.processResources {
    from(rootProject.file("assets/floo-logo.png")) {
        into("dev/wizardlauncher/app")
        rename { "logo.png" }
    }
}

tasks.named<JavaExec>("run") {
    dependsOn(":server-host:jar", ":client-boot:jar", stageResources)
    systemProperty("wizard.resources", layout.buildDirectory.dir("staged-resources").get().asFile.absolutePath)
    systemProperty("wizard.tools", layout.buildDirectory.dir("helper-jars").get().asFile.absolutePath)
    doFirst {
        copy { from(helperJars); into(layout.buildDirectory.dir("helper-jars")) }
    }
}

tasks.register<Exec>("jpackage") {
    group = "distribution"
    dependsOn("installDist", ":makeIcons", stageResources)
    val type = (findProperty("jpackageType") ?: "app-image").toString()
    val input = layout.buildDirectory.dir("install/WizardLauncher/lib").get().asFile
    val out = layout.buildDirectory.dir("jpackage").get().asFile
    doFirst {
        delete(out)
        copy { from(layout.buildDirectory.dir("staged-resources")); into(File(input, "resources")) }
    }
    val os = org.gradle.internal.os.OperatingSystem.current()
    val icon = when {
        os.isWindows -> rootProject.file("installer/app.ico")
        os.isMacOsX -> rootProject.file("installer/app.icns")
        else -> rootProject.file("assets/floo-logo.png")
    }
    val version = project.version.toString().substringBefore('+').substringBefore('-')
    commandLine(listOfNotNull(
        "${System.getProperty("java.home")}/bin/jpackage",
        "--type", type,
        "--name", "WizardLauncher",
        "--app-version", version,
        "--vendor", "Foxy",
        "--input", input.absolutePath,
        "--main-jar", "wizard-launcher.jar",
        "--main-class", "dev.wizardlauncher.app.MainKt",
        "--dest", out.absolutePath,

        "--add-modules", "java.se,jdk.unsupported,jdk.crypto.ec,jdk.zipfs,jdk.management,jdk.charsets,jdk.localedata,jdk.net,jdk.naming.dns,jdk.accessibility,jdk.httpserver,jdk.jfr",
        "--jlink-options", "--strip-debug --no-man-pages --no-header-files",
        if (icon.exists()) "--icon" else null, if (icon.exists()) icon.absolutePath else null,
    ) + (listOf("-Xmx512m", "-XX:+UseSerialGC") + uiJvmArgs).flatMap { listOf("--java-options", it) })
}
