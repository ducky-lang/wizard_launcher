package dev.wizardlauncher.legacypacks;

import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.util.concurrent.atomic.AtomicBoolean;

public final class LegacyEffects {
    private static final AtomicBoolean REPORTED = new AtomicBoolean();

    private LegacyEffects() {
    }

    public static boolean fromLocalWorld(SocketAddress server) {
        return server instanceof InetSocketAddress inet && inet.getAddress() != null && inet.getAddress().isLoopbackAddress();
    }

    public static int signed(int amplifier) {
        return amplifier > Byte.MAX_VALUE && amplifier <= 0xFF ? (byte) amplifier : amplifier;
    }

    public static void restored(String effect, int legacy, int modern) {
        if (REPORTED.compareAndSet(false, true)) {
            LegacyPacks.LOGGER.info("Keeping 1.16.5 effect strengths: {} arrived as {}, which this version would read as {}", effect, legacy, modern);
        }
    }
}
