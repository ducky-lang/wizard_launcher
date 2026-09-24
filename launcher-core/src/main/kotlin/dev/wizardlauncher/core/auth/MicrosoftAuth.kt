package dev.wizardlauncher.core.auth

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.LauncherException
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import javax.net.ssl.SSLParameters

/**
 * Microsoft -> Xbox Live -> XSTS -> Minecraft sign-in, using the OAuth 2.0
 * *device code* flow: the player types a code on Microsoft's own page, so
 * the launcher never sees, handles or stores a password. Only the refresh
 * token is kept (in [dev.wizardlauncher.core.security.SecretStore]).
 */
class MicrosoftAuth(private val clientId: String = BuildInfo.microsoftClientId) {
    data class DeviceCode(val userCode: String, val verificationUri: String, val deviceCode: String, val interval: Int, val expiresIn: Int)
    data class Session(val name: String, val uuid: String, val accessToken: String, val refreshToken: String, val xuid: String, val expiresAt: Long)

    private val http = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(10))
        .sslParameters(SSLParameters().apply { protocols = arrayOf("TLSv1.3", "TLSv1.2") })
        .followRedirects(HttpClient.Redirect.NEVER)
        .build()

    val configured get() = clientId.isNotBlank()

    fun requestDeviceCode(): DeviceCode {
        requireConfigured()
        val o = postForm(DEVICE_CODE_URL, mapOf("client_id" to clientId, "scope" to SCOPE))
        return DeviceCode(o.s("user_code"), o.s("verification_uri"), o.s("device_code"), o.get("interval")?.asInt ?: 5, o.get("expires_in")?.asInt ?: 900)
    }

    /** Blocks until the player finished on Microsoft's page, [cancelled] returns true, or the code expires. */
    fun awaitDeviceLogin(code: DeviceCode, cancelled: () -> Boolean): Session {
        var interval = code.interval
        val deadline = System.currentTimeMillis() + code.expiresIn * 1000L
        while (System.currentTimeMillis() < deadline) {
            if (cancelled()) throw LauncherException("Sign-in cancelled.")
            Thread.sleep(interval * 1000L)
            val o = postForm(TOKEN_URL, mapOf(
                "grant_type" to "urn:ietf:params:oauth:grant-type:device_code",
                "client_id" to clientId, "device_code" to code.deviceCode), allowError = true)
            when (o.get("error")?.asString) {
                null -> return completeChain(o.s("access_token"), o.s("refresh_token"))
                "authorization_pending" -> Unit
                "slow_down" -> interval += 5
                "expired_token" -> break
                "authorization_declined" -> throw LauncherException("Sign-in was declined on Microsoft's page.")
                else -> throw LauncherException("Microsoft sign-in failed: ${o.get("error_description")?.asString ?: o.get("error").asString}")
            }
        }
        throw LauncherException("The sign-in code expired. Please try again.")
    }

    fun refresh(refreshToken: String): Session {
        requireConfigured()
        val o = postForm(TOKEN_URL, mapOf("grant_type" to "refresh_token", "client_id" to clientId,
            "refresh_token" to refreshToken, "scope" to SCOPE))
        return completeChain(o.s("access_token"), o.get("refresh_token")?.asString ?: refreshToken)
    }

    private fun completeChain(msAccess: String, msRefresh: String): Session {
        val xbl = postJson(XBL_URL, JsonObject().apply {
            add("Properties", JsonObject().apply {
                addProperty("AuthMethod", "RPS"); addProperty("SiteName", "user.auth.xboxlive.com")
                addProperty("RpsTicket", "d=$msAccess")
            })
            addProperty("RelyingParty", "http://auth.xboxlive.com"); addProperty("TokenType", "JWT")
        })
        val userHash = xbl.getAsJsonObject("DisplayClaims").getAsJsonArray("xui")[0].asJsonObject.s("uhs")
        val xsts = postJson(XSTS_URL, JsonObject().apply {
            add("Properties", JsonObject().apply {
                addProperty("SandboxId", "RETAIL")
                add("UserTokens", com.google.gson.JsonArray().apply { add(xbl.s("Token")) })
            })
            addProperty("RelyingParty", "rp://api.minecraftservices.com/"); addProperty("TokenType", "JWT")
        }, xsts = true)
        val xuid = runCatching { xsts.getAsJsonObject("DisplayClaims").getAsJsonArray("xui")[0].asJsonObject.s("xid") }.getOrDefault("")
        val mc = postJson(MC_LOGIN_URL, JsonObject().apply { addProperty("identityToken", "XBL3.0 x=$userHash;${xsts.s("Token")}") })
        val mcToken = mc.s("access_token")
        val profile = get(MC_PROFILE_URL, mcToken)
            ?: throw LauncherException("This Microsoft account does not own Minecraft: Java Edition.\n\nYou can still play with an offline name.")
        return Session(profile.s("name"), profile.s("id"), mcToken, msRefresh, xuid,
            System.currentTimeMillis() + (mc.get("expires_in")?.asLong ?: 86400) * 1000)
    }

    // ------------------------------------------------------------------ http
    private fun requireConfigured() {
        if (!configured) throw LauncherException("Microsoft sign-in is not configured in this build. Use an offline name instead.")
    }

    private fun postForm(url: String, form: Map<String, String>, allowError: Boolean = false): JsonObject {
        val body = form.entries.joinToString("&") { "${it.key}=${URLEncoder.encode(it.value, Charsets.UTF_8)}" }
        return send(HttpRequest.newBuilder(URI.create(url)).header("Content-Type", "application/x-www-form-urlencoded")
            .POST(HttpRequest.BodyPublishers.ofString(body)), allowError)
    }

    private fun postJson(url: String, json: JsonObject, xsts: Boolean = false): JsonObject {
        val request = HttpRequest.newBuilder(URI.create(url)).header("Content-Type", "application/json").header("Accept", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json.toString()))
        return if (!xsts) send(request, false) else {
            val o = send(request, true)
            o.get("XErr")?.asLong?.let { err -> throw LauncherException(xstsMessage(err)) }
            o
        }
    }

    private fun get(url: String, bearer: String): JsonObject? {
        val response = http.send(HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(20))
            .header("Authorization", "Bearer $bearer").header("User-Agent", BuildInfo.userAgent).GET().build(),
            HttpResponse.BodyHandlers.ofString())
        if (response.statusCode() == 404) return null
        if (response.statusCode() !in 200..299) throw LauncherException("Minecraft services returned HTTP ${response.statusCode()}.")
        return JsonParser.parseString(response.body()).asJsonObject
    }

    private fun send(builder: HttpRequest.Builder, allowError: Boolean): JsonObject {
        val response = try {
            http.send(builder.timeout(Duration.ofSeconds(20)).header("User-Agent", BuildInfo.userAgent).build(),
                HttpResponse.BodyHandlers.ofString())
        } catch (e: java.io.IOException) {
            throw LauncherException("Could not reach Microsoft sign-in (${e.message}). Check your connection, or play offline.", e)
        }
        val body = runCatching { JsonParser.parseString(response.body()).asJsonObject }.getOrDefault(JsonObject())
        if (response.statusCode() !in 200..299 && !allowError) {
            throw LauncherException("Sign-in failed (HTTP ${response.statusCode()}): ${body.get("error_description")?.asString ?: body.get("errorMessage")?.asString ?: ""}")
        }
        return body
    }

    private fun JsonObject.s(key: String): String = get(key)?.asString ?: throw LauncherException("Unexpected sign-in response (no $key).")

    private fun xstsMessage(code: Long) = when (code) {
        2148916233 -> "This Microsoft account has no Xbox profile yet. Sign in once at minecraft.net, then try again."
        2148916235 -> "Xbox Live is not available in this account's country or region."
        2148916236, 2148916237 -> "This account needs adult verification on the Xbox website first."
        2148916238 -> "This is a child account: an adult must add it to a Microsoft family before it can sign in."
        else -> "Xbox Live refused the sign-in (code $code)."
    }

    companion object {
        const val SCOPE = "XboxLive.signin offline_access"
        const val DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
        const val TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
        const val XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
        const val XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
        const val MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
        const val MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
    }
}
