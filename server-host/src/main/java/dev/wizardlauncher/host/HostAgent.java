package dev.wizardlauncher.host;

import java.lang.instrument.Instrumentation;

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
