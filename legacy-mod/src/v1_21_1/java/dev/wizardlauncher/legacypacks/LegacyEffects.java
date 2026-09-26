package dev.wizardlauncher.legacypacks;

import java.net.InetSocketAddress;
import java.net.SocketAddress;

public final class LegacyEffects {
    private static final ThreadLocal<Integer> PENDING = new ThreadLocal<>();

    private LegacyEffects() {
    }

    public static void expect(SocketAddress server, int amplifier) {
        if (server instanceof InetSocketAddress inet && inet.getAddress() != null && inet.getAddress().isLoopbackAddress()) {
            PENDING.set(amplifier);
        } else {
            PENDING.remove();
        }
    }

    public static void clear() {
        PENDING.remove();
    }

    public static Integer take() {
        Integer amplifier = PENDING.get();
        PENDING.remove();
        return amplifier;
    }

    public static int signed(int amplifier) {
        return amplifier > Byte.MAX_VALUE && amplifier <= 0xFF ? (byte) amplifier : amplifier;
    }
}
