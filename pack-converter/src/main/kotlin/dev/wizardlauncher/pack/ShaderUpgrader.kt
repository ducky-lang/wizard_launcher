package dev.wizardlauncher.pack

/**
 * Upgrades 1.16-era post-processing shaders (GLSL 110/120) to the GLSL 150
 * core profile that 1.17+ requires. Without this a pack's custom post effect
 * does not just look wrong - the shader fails to compile and the effect is
 * dropped with an error in the log.
 *
 * The rewrite is mechanical and covers what vanilla-style post shaders
 * actually use: `attribute`/`varying` qualifiers, `gl_FragColor`,
 * `texture2D`/`texture2DLod`. Anything using fixed-function built-ins
 * (`gl_ModelViewMatrix`, `gl_Vertex`, `ftransform()`) cannot be translated
 * blindly and is reported instead of being silently broken further.
 */
object ShaderUpgrader {
    private val VERSION = Regex("^\\s*#version\\s+(\\d+).*$", RegexOption.MULTILINE)
    private val FIXED_FUNCTION = Regex("\\b(gl_ModelViewMatrix|gl_ProjectionMatrix|gl_ModelViewProjectionMatrix|gl_Vertex|gl_MultiTexCoord\\d|gl_TexCoord|ftransform)\\b")

    data class Result(val source: String, val changed: Boolean, val problem: String?)

    fun upgrade(source: String, fragment: Boolean): Result {
        val version = VERSION.find(source)?.groupValues?.get(1)?.toIntOrNull() ?: 110
        if (version >= 150) return Result(source, false, null)
        FIXED_FUNCTION.find(source)?.let {
            return Result(source, false, "uses fixed-function '${it.value}', which GLSL 150 core does not have")
        }
        var s = source
        s = if (VERSION.containsMatchIn(s)) VERSION.replaceFirst(s, "#version 150") else "#version 150\n$s"
        s = s.replace(Regex("\\battribute\\b"), "in")
        s = s.replace(Regex("\\bvarying\\b"), if (fragment) "in" else "out")
        s = s.replace(Regex("\\btexture2DLod\\b"), "textureLod")
        s = s.replace(Regex("\\btexture2D\\b"), "texture")
        if (fragment && Regex("\\bgl_FragColor\\b").containsMatchIn(s)) {
            s = s.replace(Regex("\\bgl_FragColor\\b"), "fragColor")
            // Declared right after #version so it precedes any use.
            s = s.replaceFirst("#version 150", "#version 150\n\nout vec4 fragColor;")
        }
        return Result(s, true, null)
    }
}
