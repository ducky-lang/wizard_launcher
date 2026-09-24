package dev.wizardlauncher.core.game

import java.security.MessageDigest
import java.util.UUID

/**
 * The UUID an offline-mode server assigns to a name:
 * `UUID.nameUUIDFromBytes("OfflinePlayer:" + name)`. ops.json and
 * whitelist.json entries must use this one, not the player's real Mojang
 * UUID, or they silently match nobody.
 */
object OfflineUuid {
    fun of(name: String): UUID = UUID.nameUUIDFromBytes("OfflinePlayer:$name".toByteArray(Charsets.UTF_8))

    /** Stable pseudo-random UUID for offline (non-Microsoft) client profiles. */
    fun md5Hex(text: String): String = MessageDigest.getInstance("MD5").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }
}
