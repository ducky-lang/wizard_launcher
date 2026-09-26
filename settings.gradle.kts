rootProject.name = "wizard-launcher"

include("launcher-core", "pack-legacy", "server-host", "client-boot", "launcher-app")

dependencyResolutionManagement {
    repositories { mavenCentral() }
}
