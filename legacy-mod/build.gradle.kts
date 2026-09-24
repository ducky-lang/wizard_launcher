plugins {
    id("fabric-loom") version "1.7-SNAPSHOT"
    java
}

version = "2.0.0"
group = "dev.wizardlauncher"

base { archivesName.set("wizard-legacy-packs") }

repositories {
    mavenCentral()
}

dependencies {
    minecraft("com.mojang:minecraft:1.20.1")
    mappings(loom.officialMojangMappings())
    modImplementation("net.fabricmc:fabric-loader:0.15.11")
}

sourceSets {
    main {
        java.srcDir("../pack-legacy/src/main/java")
        resources.srcDir("../pack-legacy/src/main/resources")
    }
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

tasks.withType<JavaCompile>().configureEach {
    options.release.set(17)
    options.encoding = "UTF-8"
}

tasks.processResources {
    inputs.property("version", project.version)
    filesMatching("fabric.mod.json") { expand("version" to project.version) }
}

tasks.remapJar {
    archiveFileName.set("wizard-legacy-packs.jar")
}
