package dev.wizardlauncher.host;

import java.lang.instrument.Instrumentation;

/**
 * Receives the JVM's {@link Instrumentation} so it can be handed to ViaProxy.
 *
 * <p>ViaProxy normally gets it by being launched with {@code java -jar} (its
 * manifest declares a Launcher-Agent-Class). Hosted inside this process it is
 * not the main jar, so the host is started with
 * {@code -javaagent:wizard-server-host.jar} instead and forwards the instance.
 */
public final class HostAgent {
    private static volatile Instrumentation instrumentation;

    private HostAgent() {}

    public static void premain(String args, Instrumentation inst) {
        instrumentation = inst;
    }

    public static void agentmain(String args, Instrumentation inst) {
        instrumentation = inst;
    }

    static Instrumentation instrumentation() {
        return instrumentation;
    }
}
