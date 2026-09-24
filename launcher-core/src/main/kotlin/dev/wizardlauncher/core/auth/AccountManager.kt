package dev.wizardlauncher.core.auth

import com.google.gson.JsonObject
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Settings
import dev.wizardlauncher.core.game.OfflineUuid
import dev.wizardlauncher.core.net.Connectivity
import dev.wizardlauncher.core.security.SecretStore
import java.nio.file.Path

data class Account(
    val name: String,
    val uuid: String,
    val accessToken: String,
    val userType: String,
    val xuid: String = "",
    val clientId: String = "",
) {
    val isMicrosoft get() = userType == "msa"

    companion object {
        fun offline(name: String): Account {
            if (!Settings.validName(name)) throw LauncherException("A player name is 3-16 letters, digits or underscores.")
            return Account(name, OfflineUuid.of(name).toString().replace("-", ""), "0", "legacy")
        }
    }
}

class AccountManager(private val profileFile: Path, private val secrets: SecretStore, private val settings: Settings) {
    private val auth = MicrosoftAuth()
    @Volatile private var session: MicrosoftAuth.Session? = null

    val microsoftAvailable get() = auth.configured

    fun cachedProfile(): Pair<String, String>? = Json.read(profileFile)?.takeIf { it.isJsonObject }?.asJsonObject?.let {
        (it.get("name")?.asString ?: return null) to (it.get("uuid")?.asString ?: return null)
    }

    fun beginMicrosoftLogin(): MicrosoftAuth.DeviceCode = auth.requestDeviceCode()

    fun finishMicrosoftLogin(code: MicrosoftAuth.DeviceCode, cancelled: () -> Boolean): Account {
        val s = auth.awaitDeviceLogin(code, cancelled)
        remember(s)
        return toAccount(s)
    }

    fun signOut() {
        session = null
        secrets.delete(REFRESH)
        java.nio.file.Files.deleteIfExists(profileFile)
    }

    fun current(): Account? {
        val profile = cachedProfile()
        if (profile == null) return settings.offlineName.takeIf { it.isNotBlank() }?.let(Account::offline)
        session?.takeIf { it.expiresAt > System.currentTimeMillis() + 60_000 }?.let { return toAccount(it) }
        val refresh = secrets.get(REFRESH)
        if (refresh != null && !settings.offlineOnly && Connectivity.online()) {
            try {
                val s = auth.refresh(refresh)
                remember(s)
                return toAccount(s)
            } catch (e: Exception) {
                Log.info("Could not refresh the Microsoft sign-in (${e.message}); playing with the saved profile.")
            }
        }
        val (name, uuid) = profile
        return Account(name, uuid, "0", "msa")
    }

    private fun remember(s: MicrosoftAuth.Session) {
        session = s
        secrets.put(REFRESH, s.refreshToken)
        Json.write(profileFile, JsonObject().apply { addProperty("name", s.name); addProperty("uuid", s.uuid) })
    }

    private fun toAccount(s: MicrosoftAuth.Session) =
        Account(s.name, s.uuid, s.accessToken, "msa", s.xuid, dev.wizardlauncher.core.BuildInfo.microsoftClientId)

    companion object { const val REFRESH = "ms-refresh-token" }
}
