package dev.wizardlauncher.boot;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

/**
 * Starts Minecraft without putting the session token on the command line.
 *
 * <p>A vanilla launch passes {@code --accessToken <jwt>} as a program
 * argument, and program arguments are readable by every process of the same
 * user (Task Manager's "Command line" column, {@code ps -ef},
 * {@code /proc/<pid>/cmdline}) and end up in crash reports. Here the launcher
 * starts this class instead and writes the real main class and the game
 * arguments to its stdin; they are read into memory and handed straight to
 * the game's {@code main}. The visible command line carries no secret.
 *
 * <p>Protocol (UTF-8 lines): {@code WIZARD-BOOT/1}, base64(main class),
 * argument count, then one base64 argument per line.
 */
public final class SecureBoot {
    private static final String MAGIC = "WIZARD-BOOT/1";

    private SecureBoot() {}

    public static void main(String[] ignored) throws Throwable {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        if (!MAGIC.equals(in.readLine())) {
            throw new IOException("SecureBoot: missing launch header on stdin");
        }
        Base64.Decoder b64 = Base64.getDecoder();
        String mainClass = new String(b64.decode(in.readLine()), StandardCharsets.UTF_8);
        int count = Integer.parseInt(in.readLine().trim());
        if (count < 0 || count > 4096) {
            throw new IOException("SecureBoot: implausible argument count " + count);
        }
        String[] args = new String[count];
        for (int i = 0; i < count; i++) {
            args[i] = new String(b64.decode(in.readLine()), StandardCharsets.UTF_8);
        }
        // Do not close System.in: nothing else uses it, and closing a pipe the
        // launcher still holds would surface as an error there.

        Class<?> target = Class.forName(mainClass, false, SecureBoot.class.getClassLoader());
        Method main = target.getMethod("main", String[].class);
        try {
            main.invoke(null, (Object) args);
        } catch (java.lang.reflect.InvocationTargetException e) {
            throw e.getCause();
        }
    }
}
