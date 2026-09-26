package dev.wizardlauncher.core.net

import java.net.InetSocketAddress
import java.net.Socket

object Connectivity {
    @Volatile private var cached: Pair<Long, Boolean>? = null

    fun online(timeoutMs: Int = 1500): Boolean {
        cached?.let { (at, value) -> if (System.currentTimeMillis() - at < 30_000) return value }
        val result = listOf("login.microsoftonline.com", "piston-meta.mojang.com").any { host ->
            runCatching { Socket().use { it.connect(InetSocketAddress(host, 443), timeoutMs); true } }.getOrDefault(false)
        }
        cached = System.currentTimeMillis() to result
        return result
    }
}
