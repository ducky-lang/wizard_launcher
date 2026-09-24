package dev.wizardlauncher.core.game

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.InetSocketAddress
import java.net.Socket

object StatusPing {
    const val PROTOCOL_1_20_1 = 763

    fun ping(port: Int, protocol: Int = PROTOCOL_1_20_1, host: String = "127.0.0.1"): JsonObject {
        Socket().use { socket ->
            socket.connect(InetSocketAddress(host, port), 3000)
            socket.soTimeout = 5000
            val out = DataOutputStream(socket.getOutputStream())
            val handshake = ByteArrayOutputStream().also { b ->
                val d = DataOutputStream(b)
                varInt(d, 0); varInt(d, protocol)
                val h = host.toByteArray(); varInt(d, h.size); d.write(h)
                d.writeShort(port); varInt(d, 1)
            }.toByteArray()
            varInt(out, handshake.size); out.write(handshake)
            varInt(out, 1); varInt(out, 0)
            out.flush()
            val input = DataInputStream(socket.getInputStream())
            readVarInt(input); readVarInt(input)
            val data = ByteArray(readVarInt(input)).also(input::readFully)
            return JsonParser.parseString(String(data, Charsets.UTF_8)).asJsonObject
        }
    }

    private fun varInt(out: DataOutputStream, value: Int) {
        var v = value
        while (true) {
            if (v and 0x7F.inv() == 0) { out.writeByte(v); return }
            out.writeByte((v and 0x7F) or 0x80)
            v = v ushr 7
        }
    }

    private fun readVarInt(input: DataInputStream): Int {
        var value = 0; var shift = 0
        while (true) {
            val b = input.readUnsignedByte()
            value = value or ((b and 0x7F) shl shift)
            if (b and 0x80 == 0) return value
            shift += 7
            require(shift < 35) { "VarInt too long" }
        }
    }
}
