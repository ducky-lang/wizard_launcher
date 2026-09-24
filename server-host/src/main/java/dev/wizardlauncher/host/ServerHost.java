package dev.wizardlauncher.host;

import java.io.BufferedReader;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;
import java.io.PrintStream;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Runs the 1.16.5 world server and the ViaProxy version bridge in ONE JVM.
 *
 * <p>The previous launcher started two JVMs: the server, and ViaProxy next to
 * it so the 1.20.1 client could talk to it. Each JVM carries its own fixed
 * cost - JIT code cache, metaspace, GC bookkeeping, thread stacks, a second
 * heap sized for the worst case - so on an 8 GB laptop the bridge alone cost
 * several hundred megabytes that the game could have used.
 *
 * <p>Here the two share a heap and a runtime but not their classes. They
 * cannot share classes: the 1.16.5 server was built against Netty 4.1.25 and
 * Log4j 2.8, ViaProxy against current releases of both. So:
 * <ul>
 *   <li>ViaProxy lives on the system class path, where its own bootstrap
 *       (instrumentation + injection class loader) expects to be;</li>
 *   <li>the server is loaded by an isolated {@link URLClassLoader} whose
 *       parent is the platform loader, so it sees none of ViaProxy's classes
 *       and ViaProxy sees none of its.</li>
 * </ul>
 *
 * <h2>Control channel</h2>
 * The process's real stdin belongs to this host, one command per line:
 * <pre>
 *   stop          save the world and exit
 *   watch PID     stop (and save) as soon as process PID exits
 *   cmd TEXT      run TEXT on the server console
 * </pre>
 * {@code stop} works through {@link System#exit}: the vanilla server
 * registers a "Server Shutdown Thread" hook that halts it and saves every
 * loaded chunk, so exiting is a clean shutdown on every OS - including
 * Windows, where killing a process never runs shutdown hooks.
 */
public final class ServerHost {
    private static final PrintStream OUT = System.out;
    private static final AtomicBoolean STOPPING = new AtomicBoolean();

    private ServerHost() {}

    public static void main(String[] argv) throws Exception {
        Map<String, String> args = parse(argv);
        Path serverJar = Path.of(require(args, "server-jar")).toAbsolutePath();
        int serverPort = Integer.parseInt(args.getOrDefault("server-port", "25565"));
        String proxyBind = args.get("proxy-bind");
        String targetVersion = args.getOrDefault("target-version", "1.16.5");

        InputStream control = new FileInputStream(FileDescriptor.in);
        PipedOutputStream consoleFeed = new PipedOutputStream();
        PipedInputStream consoleIn = new PipedInputStream(consoleFeed, 8192);
        System.setIn(consoleIn);

        Path log4j = writeLog4jConfig();
        System.setProperty("log4j.configurationFile", log4j.toString());
        // Also makes the (pre-2.10) Log4j inside the 1.16.5 server skip the
        // lookup machinery entirely; the config's %msg{nolookups} is what
        // actually closes CVE-2021-44228 for that version.
        System.setProperty("log4j2.formatMsgNoLookups", "true");

        startControlThread(control, consoleFeed);
        if (args.containsKey("watch-pid")) {
            watch(Long.parseLong(args.get("watch-pid")));
        }

        log("Starting world server (" + serverJar.getFileName() + ")...");
        Thread server = startServer(serverJar, args.getOrDefault("server-args", "nogui"));

        if (!waitForPort(serverPort, server, 600_000)) {
            log("World server did not open port " + serverPort + "; giving up.");
            System.exit(2);
        }
        log("World server listening on " + serverPort + ".");

        // The server's Log4j has read its config by now. ViaProxy ships its
        // own Log4j with its own setup, so it must not pick up the server's.
        System.clearProperty("log4j.configurationFile");

        if (proxyBind != null && !proxyBind.isBlank()) {
            startProxy(proxyBind, "127.0.0.1:" + serverPort, targetVersion, args.get("proxy-args"));
        }

        startLivenessMonitor(serverPort, server);
        log("READY");
    }

    // ------------------------------------------------------------------
    // Server
    // ------------------------------------------------------------------
    private static Thread startServer(Path serverJar, String serverArgs) throws Exception {
        URLClassLoader loader = new URLClassLoader(
                "wizard-server", new URL[]{serverJar.toUri().toURL()}, ClassLoader.getPlatformClassLoader());
        Class<?> main = Class.forName("net.minecraft.server.Main", false, loader);
        Method entry = main.getMethod("main", String[].class);
        String[] passArgs = serverArgs.isBlank() ? new String[0] : serverArgs.trim().split("\\s+");

        Thread thread = new Thread(() -> {
            try {
                entry.invoke(null, (Object) passArgs);
            } catch (Throwable t) {
                log("World server failed to start: " + t);
                t.printStackTrace(OUT);
                System.exit(3);
            }
        }, "Wizard-Server-Bootstrap");
        thread.setContextClassLoader(loader);
        thread.start();
        return thread;
    }

    // ------------------------------------------------------------------
    // Proxy
    // ------------------------------------------------------------------
    private static void startProxy(String bind, String target, String version, String extra) throws Exception {
        // ViaProxy's console reader would otherwise compete with the
        // server's for stdin; a stream whose available() throws is how
        // ViaProxy is told "no console here".
        System.setIn(new NoConsole());
        System.setProperty("skipUpdateCheck", "true");
        System.setProperty("java.awt.headless", "true");

        Class<?> viaProxy = Class.forName("net.raphimc.viaproxy.ViaProxy");
        if (HostAgent.instrumentation() != null) {
            viaProxy.getMethod("agentmain", String.class, java.lang.instrument.Instrumentation.class)
                    .invoke(null, "", HostAgent.instrumentation());
        }
        List<String> proxyArgs = new ArrayList<>(List.of(
                "cli",
                "--bind-address", bind,
                "--target-address", target,
                "--target-version", version,
                "--fake-accept-resource-packs", "true"));
        if (extra != null && !extra.isBlank()) {
            proxyArgs.addAll(List.of(extra.trim().split("\\s+")));
        }
        Method main = viaProxy.getMethod("main", String[].class);
        Thread thread = new Thread(() -> {
            try {
                main.invoke(null, (Object) proxyArgs.toArray(new String[0]));
            } catch (Throwable t) {
                log("Version bridge failed: " + t);
                t.printStackTrace(OUT);
                System.exit(4);
            }
        }, "Wizard-Proxy-Bootstrap");
        thread.start();

        int port = Integer.parseInt(bind.substring(bind.lastIndexOf(':') + 1));
        if (!waitForPort(port, thread, 120_000) && !thread.isAlive()) {
            log("Version bridge did not open port " + port + ".");
            System.exit(4);
        }
        log("Version bridge listening on " + bind + ".");
    }

    // ------------------------------------------------------------------
    // Control & lifecycle
    // ------------------------------------------------------------------
    private static void startControlThread(InputStream control, OutputStream consoleFeed) {
        Thread thread = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(control, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (line.equals("stop")) {
                        shutdown("stop requested");
                    } else if (line.startsWith("watch ")) {
                        watch(Long.parseLong(line.substring(6).trim()));
                    } else if (line.startsWith("cmd ")) {
                        consoleFeed.write((line.substring(4) + "\n").getBytes(StandardCharsets.UTF_8));
                        consoleFeed.flush();
                    }
                }
            } catch (IOException | NumberFormatException e) {
                log("Control channel error: " + e.getMessage());
            }
            // The launcher's end of the pipe closed: the launcher is gone.
            // Keep running if a client is being watched - that is the whole
            // point of "close the launcher while playing" - otherwise stop.
            if (!WATCHING.get()) {
                shutdown("control channel closed");
            }
        }, "Wizard-Control");
        thread.setDaemon(true);
        thread.start();
    }

    private static final AtomicBoolean WATCHING = new AtomicBoolean();

    /** Replaces the old detached watchdog process: the server stops itself. */
    private static void watch(long pid) {
        ProcessHandle.of(pid).ifPresentOrElse(handle -> {
            WATCHING.set(true);
            log("Watching game process " + pid + ".");
            handle.onExit().thenRun(() -> shutdown("game process " + pid + " exited"));
        }, () -> shutdown("game process " + pid + " is not running"));
    }

    private static void startLivenessMonitor(int serverPort, Thread serverBootstrap) {
        Thread thread = new Thread(() -> {
            int misses = 0;
            while (true) {
                sleep(3000);
                if (portOpen(serverPort)) {
                    misses = 0;
                } else if (++misses >= 5) {
                    // The server stopped by itself (for example /stop in game)
                    // but ViaProxy's threads would keep the JVM alive forever.
                    shutdown("world server is no longer listening");
                    return;
                }
            }
        }, "Wizard-Liveness");
        thread.setDaemon(true);
        thread.start();
    }

    private static void shutdown(String reason) {
        if (!STOPPING.compareAndSet(false, true)) {
            return;
        }
        log("Shutting down: " + reason + ". Saving the world...");
        // System.exit blocks until shutdown hooks - including the server's
        // save-and-halt - have finished. Run it off the calling thread so a
        // hook can never deadlock against the thread that triggered it.
        Thread exit = new Thread(() -> System.exit(0), "Wizard-Exit");
        exit.start();
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------
    static boolean waitForPort(int port, Thread owner, long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < deadline) {
            if (portOpen(port)) {
                return true;
            }
            if (owner != null && !owner.isAlive() && !STOPPING.get()) {
                // Bootstrap threads return once the server thread is running,
                // so a dead bootstrap alone is not failure - keep polling.
                owner = null;
            }
            sleep(250);
        }
        return false;
    }

    static boolean portOpen(int port) {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", port), 500);
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    private static Path writeLog4jConfig() throws IOException {
        Path target = Path.of("wizard-log4j2.xml").toAbsolutePath();
        try (InputStream in = ServerHost.class.getResourceAsStream("/wizard-log4j2.xml")) {
            if (in == null) {
                throw new IOException("bundled log4j config missing");
            }
            Files.copy(in, target, StandardCopyOption.REPLACE_EXISTING);
        }
        return target;
    }

    static Map<String, String> parse(String[] argv) {
        Map<String, String> out = new HashMap<>();
        for (int i = 0; i < argv.length; i++) {
            String key = argv[i];
            if (!key.startsWith("--")) {
                throw new IllegalArgumentException("Unexpected argument: " + key);
            }
            String value = i + 1 < argv.length && !argv[i + 1].startsWith("--") ? argv[++i] : "true";
            out.put(key.substring(2), value);
        }
        return out;
    }

    private static String require(Map<String, String> args, String key) {
        String value = args.get(key);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("Missing --" + key);
        }
        return value;
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    static void log(String message) {
        OUT.println("[WizardHost] " + message);
        OUT.flush();
    }

    /** An stdin replacement that reports "no console available". */
    private static final class NoConsole extends InputStream {
        @Override
        public int read() throws IOException {
            return -1;
        }

        @Override
        public int available() throws IOException {
            throw new IOException("no console");
        }
    }
}
