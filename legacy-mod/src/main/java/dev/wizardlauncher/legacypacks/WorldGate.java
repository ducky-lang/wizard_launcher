package dev.wizardlauncher.legacypacks;

import java.net.InetSocketAddress;
import java.net.Socket;
import net.minecraft.client.multiplayer.resolver.ServerAddress;

public final class WorldGate {
    public static final String PROPERTY = "wizard.waitForWorld";
    private static volatile boolean released;

    private WorldGate() {
    }

    public static boolean shouldWait(String address) {
        return Boolean.getBoolean(PROPERTY) && !released && !isOpen(address);
    }

    static void release() {
        released = true;
    }

    static boolean isOpen(String address) {
        ServerAddress parsed = ServerAddress.parseString(address);
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress(parsed.getHost(), parsed.getPort()), 300);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
