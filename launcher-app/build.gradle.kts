plugins {
    kotlin("jvm")
    application
}

dependencies {
    implementation(project(":launcher-core"))
    implementation(project(":pack-converter"))
    implementation("com.formdev:flatlaf:3.5.2")
}

application {
    mainClass.set("dev.wizardlauncher.app.MainKt")
    applicationName = "WizardLauncher"
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

distributions {
    main {
        contents {
            into("lib") { from(helperJars) }

            into("resources") {
                from(rootProject.file("resources"))
            }
        }
    }
}

tasks.named<JavaExec>("run") {
    dependsOn(":server-host:jar", ":client-boot:jar")
    systemProperty("wizard.resources", rootProject.file("resources").absolutePath)
    systemProperty("wizard.tools", layout.buildDirectory.dir("helper-jars").get().asFile.absolutePath)
    doFirst {
        copy { from(helperJars); into(layout.buildDirectory.dir("helper-jars")) }
    }
}

tasks.register<Exec>("jpackage") {
    group = "distribution"
    dependsOn("installDist", ":makeIcons")
    val type = (findProperty("jpackageType") ?: "app-image").toString()
    val input = layout.buildDirectory.dir("install/WizardLauncher/lib").get().asFile
    val out = layout.buildDirectory.dir("jpackage").get().asFile
    doFirst {
        delete(out)
        copy { from(rootProject.file("resources")); into(File(input, "resources")) }
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
        "--java-options", "-Xmx512m -XX:+UseSerialGC",
        if (icon.exists()) "--icon" else null, if (icon.exists()) icon.absolutePath else null,
    ))
}
