package dev.wizardlauncher.core.game

object JvmFlags {
    val HARDENING = listOf(
        "-Dlog4j2.formatMsgNoLookups=true",
        "-Dcom.sun.jndi.rmi.object.trustURLCodebase=false",
        "-Dcom.sun.jndi.cosnaming.object.trustURLCodebase=false",
        "-Dcom.sun.jndi.ldap.object.trustURLCodebase=false",
        "-Djava.rmi.server.useCodebaseOnly=true",
        "-Dfile.encoding=UTF-8",
    )

    fun server(heapMb: Int): List<String> = listOf(
        "-Xms${minOf(512, heapMb)}M",
        "-Xmx${heapMb}M",
        "-XX:+UseG1GC",
        "-XX:MaxGCPauseMillis=100",
        "-XX:G1HeapRegionSize=4M",
        "-XX:G1PeriodicGCInterval=15000",
        "-XX:MinHeapFreeRatio=10",
        "-XX:MaxHeapFreeRatio=30",
        "-XX:+UseStringDeduplication",
        "-XX:+ParallelRefProcEnabled",
        "-XX:+DisableExplicitGC",
        "-XX:ReservedCodeCacheSize=128M",
        "-XX:CICompilerCount=2",
        "-XX:MaxDirectMemorySize=256M",
        "-Xss512K",
        "-XX:+PerfDisableSharedMem",
        "-Djava.awt.headless=true",
    ) + HARDENING

    fun client(heapMb: Int): List<String> = listOf(
        "-Xms${minOf(heapMb, 1024)}M",
        "-Xmx${heapMb}M",
        "-XX:+UseG1GC",
        "-XX:MaxGCPauseMillis=50",
        "-XX:G1HeapRegionSize=16M",
        "-XX:+ParallelRefProcEnabled",
        "-XX:+DisableExplicitGC",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:G1NewSizePercent=20",
        "-XX:G1ReservePercent=20",
        "-XX:+UseStringDeduplication",
    ) + HARDENING
}
