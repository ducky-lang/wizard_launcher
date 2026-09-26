package dev.wizardlauncher.boot;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

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

        Class<?> target = Class.forName(mainClass, false, SecureBoot.class.getClassLoader());
        Method main = target.getMethod("main", String[].class);
        try {
            main.invoke(null, (Object) args);
        } catch (java.lang.reflect.InvocationTargetException e) {
            throw e.getCause();
        }
    }
}
