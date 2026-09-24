package dev.wizardlauncher.core.auth

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Settings
import dev.wizardlauncher.core.game.OfflineUuid
import dev.wizardlauncher.core.net.Connectivity
import dev.wizardlauncher.core.security.SecretStore
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.ConcurrentHashMap

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

data class StoredAccount(val id: String, val type: String, val name: String, val uuid: String) {
    val isMicrosoft get() = type == "msa"
}

class AccountManager(private val file: Path, private val secrets: SecretStore, private val settings: Settings) {
    private val auth = MicrosoftAuth()
    private val sessions = ConcurrentHashMap<String, MicrosoftAuth.Session>()
    private val accounts = ArrayList<StoredAccount>()
    @Volatile var selectedId: String? = null
        private set

    val microsoftAvailable get() = auth.configured

    init {
        load()
    }

    @Synchronized
    private fun load() {
        accounts.clear()
        val root = Json.read(file)?.takeIf { it.isJsonObject }?.asJsonObject
        if (root != null && root.has("accounts")) {
            root.getAsJsonArray("accounts").forEach { e ->
                val o = e.asJsonObject
                val type = o.get("type")?.asString ?: return@forEach
                val name = o.get("name")?.asString ?: return@forEach
                val uuid = o.get("uuid")?.asString ?: return@forEach
                if (type == "legacy" && !Settings.validName(name)) return@forEach
                accounts += StoredAccount(o.get("id")?.asString ?: "$type:$uuid", type, name, uuid)
            }
            selectedId = root.get("selected")?.asString
        } else if (root != null && root.has("name") && root.has("uuid")) {
            val uuid = root.get("uuid").asString
            accounts += StoredAccount("msa:$uuid", "msa", root.get("name").asString, uuid)
            secrets.get(LEGACY_REFRESH)?.let { secrets.put(refreshKey(uuid), it); secrets.delete(LEGACY_REFRESH) }
            selectedId = "msa:$uuid"
        }
        if (settings.offlineName.isNotBlank() && accounts.none { it.type == "legacy" && it.name == settings.offlineName }) {
            val a = Account.offline(settings.offlineName)
            accounts += StoredAccount("legacy:${a.name.lowercase()}", "legacy", a.name, a.uuid)
            if (selectedId == null) selectedId = "legacy:${a.name.lowercase()}"
        }
        if (selectedId != null && accounts.none { it.id == selectedId }) selectedId = accounts.firstOrNull()?.id
        save()
    }

    @Synchronized
    private fun save() {
        val list = JsonArray()
        accounts.forEach { a ->
            list.add(JsonObject().apply {
                addProperty("id", a.id); addProperty("type", a.type); addProperty("name", a.name); addProperty("uuid", a.uuid)
            })
        }
        Json.write(file, JsonObject().apply {
            add("accounts", list)
            selectedId?.let { addProperty("selected", it) }
        })
    }

    @Synchronized fun list(): List<StoredAccount> = accounts.toList()

    @Synchronized fun selected(): StoredAccount? = accounts.firstOrNull { it.id == selectedId }

    @Synchronized
    fun select(id: String) {
        if (accounts.none { it.id == id }) throw LauncherException("That account no longer exists.")
        selectedId = id
        save()
    }

    @Synchronized
    fun addOffline(name: String): StoredAccount {
        val a = Account.offline(name.trim())
        val stored = StoredAccount("legacy:${a.name.lowercase()}", "legacy", a.name, a.uuid)
        accounts.removeIf { it.id == stored.id }
        accounts += stored
        selectedId = stored.id
        save()
        return stored
    }

    @Synchronized
    fun remove(id: String) {
        val a = accounts.firstOrNull { it.id == id } ?: return
        accounts.remove(a)
        if (a.isMicrosoft) {
            secrets.delete(refreshKey(a.uuid))
            sessions.remove(a.uuid)
        }
        if (a.type == "legacy" && a.name == settings.offlineName) {
            settings.offlineName = ""
            settings.save()
        }
        if (selectedId == id) selectedId = accounts.firstOrNull()?.id
        save()
    }

    fun beginMicrosoftLogin(): MicrosoftAuth.DeviceCode = auth.requestDeviceCode()

    fun finishMicrosoftLogin(code: MicrosoftAuth.DeviceCode, cancelled: () -> Boolean): StoredAccount {
        val s = auth.awaitDeviceLogin(code, cancelled)
        return remember(s)
    }

    @Synchronized
    private fun remember(s: MicrosoftAuth.Session): StoredAccount {
        sessions[s.uuid] = s
        secrets.put(refreshKey(s.uuid), s.refreshToken)
        val stored = StoredAccount("msa:${s.uuid}", "msa", s.name, s.uuid)
        accounts.removeIf { it.id == stored.id }
        accounts.add(0, stored)
        selectedId = stored.id
        save()
        return stored
    }

    fun current(): Account? {
        val a = selected() ?: return null
        if (!a.isMicrosoft) return Account.offline(a.name)
        sessions[a.uuid]?.takeIf { it.expiresAt > System.currentTimeMillis() + 60_000 }?.let { return toAccount(it) }
        val refresh = secrets.get(refreshKey(a.uuid))
        if (refresh != null && !settings.offlineOnly && Connectivity.online()) {
            try {
                return toAccount(remember(auth.refresh(refresh)).let { sessions.getValue(it.uuid) })
            } catch (e: Exception) {
                Log.info("Could not refresh the Microsoft sign-in (${e.message}); playing with the saved profile.")
            }
        }
        return Account(a.name, a.uuid, "0", "msa")
    }

    private fun toAccount(s: MicrosoftAuth.Session) = Account(s.name, s.uuid, s.accessToken, "msa", s.xuid, BuildInfo.microsoftClientId)

    fun signOut() {
        selectedId?.let(::remove)
    }

    fun cachedProfile(): Pair<String, String>? = selected()?.takeIf { it.isMicrosoft }?.let { it.name to it.uuid }

    private fun refreshKey(uuid: String) = "ms-refresh-$uuid"

    companion object {
        const val LEGACY_REFRESH = "ms-refresh-token"

        fun exists(file: Path) = Files.isRegularFile(file)
    }
}
