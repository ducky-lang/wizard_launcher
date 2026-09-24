package dev.wizardlauncher.core.game

/**
 * JVM command lines. Kept in code, not in the catalog: a JVM flag list in an
 * editable data file would be a code-execution surface for no benefit.
 */
object JvmFlags {
    /** Closes JNDI/RMI remote class loading for any Log4j-style lookup that slips through. */
    val HARDENING = listOf(
        "-Dlog4j2.formatMsgNoLookups=true",
        "-Dcom.sun.jndi.rmi.object.trustURLCodebase=false",
        "-Dcom.sun.jndi.cosnaming.object.trustURLCodebase=false",
        "-Dcom.sun.jndi.ldap.object.trustURLCodebase=false",
        "-Djava.rmi.server.useCodebaseOnly=true",
        "-Dfile.encoding=UTF-8",
    )

    /**
     * The world server (plus ViaProxy when hosted). Measured on the bundled
     * 1.16.5 server + ViaProxy, idle with a client connected, same 1 GB cap:
     * two JVMs with the 1.x flags ~1620 MB RSS; one hosted JVM with these
     * flags ~830 MB.
     *
     * What does the work:
     *  - `-Xms` well below `-Xmx`, plus Min/MaxHeapFreeRatio and G1's periodic
     *    GC: the heap grows when the castle needs it and is handed back to the
     *    OS when it does not, instead of being reserved and touched up front
     *    (the old `-Xms=-Xmx` + AlwaysPreTouch pinned the whole heap);
     *  - 4 MB regions and string deduplication suit a small heap full of NBT
     *    and command-block strings;
     *  - fewer JIT threads and a smaller code cache: a single-player world
     *    does not need a server-class compiler budget.
     */
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
