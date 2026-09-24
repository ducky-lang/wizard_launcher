package dev.wizardlauncher.core.game

import java.security.MessageDigest
import java.util.UUID

object OfflineUuid {
    fun of(name: String): UUID = UUID.nameUUIDFromBytes("OfflinePlayer:$name".toByteArray(Charsets.UTF_8))

    fun md5Hex(text: String): String = MessageDigest.getInstance("MD5").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }
}
