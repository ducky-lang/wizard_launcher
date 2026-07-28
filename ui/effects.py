"""The moving parts of the window.

The layers, drawn back to front:

``Starfield``
    Static. Seeded from a fixed value so the sky is the same every launch -
    a background that rearranges itself on every start reads as a glitch.

``title_halo``
    A still, warm bloom behind the wordmark. Replaces the old rotating rune
    ring, which drew the eye to an empty circle in the middle of the window
    and competed with the thing it was supposed to frame. A halo does the
    same job - stop the title floating on flat black - without moving.

``RuneDrift``
    Sparse glyphs rising slowly through the background. What is left of the
    rune ring's character, spread across the whole window instead of
    orbiting a hole in the middle of it, and an order of magnitude cheaper
    than the particle field because it moves at four frames a second.

``EmberField``
    The drifting flame particles. The most expensive thing here, so it is
    the one with an idle discipline: when the window is unfocused,
    minimised, or animations are switched off, it all but stops. The window
    still redraws on demand; it just stops burning a core to animate pixels
    nobody is looking at.

And the foreground details, which is where animation actually earns its
keep - on the control the player is about to use rather than behind it:

``Sheen``
    A light sweep across the Play button every few seconds, so the primary
    action reads as the live thing on the screen.
``BreathingGlow``
    The Play button's shadow swelling and settling. Two property writes per
    cycle; Flutter interpolates the rest.
``Pulse``
    The status dot, breathing in whatever colour the current state set.
``SpellBurst``
    A one-shot flourish fired when the player presses Play. Sparks fly
    outward from the button and fade. It runs for well under a second and
    then removes itself, so it never competes with the launch it is
    celebrating.

Everything animated runs on a daemon thread and mutates control properties
directly, then calls ``update()`` on the one control that owns them. That is
much cheaper than rebuilding controls per frame, and it is why the whole
background costs a single update call per frame rather than one per particle.

Every loop here checks ``settings["animations"]`` and goes quiet when it is
off. The one-shot flourishes additionally honour ``spell_effects``, because
somebody on a laptop may want the ambient motion without the fireworks.
"""

import math
import random
import threading
import time

import flet as ft

from . import theme


class Starfield:
    """A still sky. Built once, never updated."""

    def __init__(self, width=2200, height=1300, count=80, seed=7):
        rng = random.Random(seed)
        stars = []
        for _ in range(count):
            radius = rng.choice([1, 1, 1, 1.5, 2])
            stars.append(ft.Container(
                left=rng.randint(0, width), top=rng.randint(0, height),
                width=radius, height=radius, border_radius=radius,
                bgcolor=rng.choice(["#ffffff", "#ffffff", theme.GOLD, theme.SUCCESS,
                                    theme.ARCANE]),
                opacity=rng.uniform(0.25, 0.9),
            ))
        self.control = ft.Stack(stars, width=width, height=height)


def title_halo(size=560):
    """The still bloom behind the wordmark.

    A radial gradient rather than an animation on purpose. This sits directly
    behind the largest text on the screen, and anything that moves there
    fights the thing it is meant to support - which is exactly what the
    rotating rune ring it replaces did.

    Layout-neutral: it lives in the root Stack, so it costs the column
    nothing. The ring, being in the content column, reserved 430px of
    vertical space for a decoration and opened a hole between the wordmark
    and the subtitle.
    """
    return ft.Container(
        width=size, height=size, border_radius=size / 2,
        gradient=ft.RadialGradient(
            colors=[theme.GOLD_WASH, theme.BG_DEEP + "00", "#00000000"],
            stops=[0.0, 0.62, 1.0],
            radius=0.62,
        ),
        opacity=0.75,
    )


class RuneDrift:
    """Sparse glyphs rising slowly through the background.

    Keeps the rune motif the ring carried without parking it in a circle in
    the middle of the screen. Twelve glyphs at four frames a second is a
    rounding error next to the ember field, which is why this can run
    alongside it rather than instead of it.
    """

    FRAME = 0.25

    def __init__(self, width, height, settings, count=None, seed=11):
        count = count or theme.RUNE_DRIFT_COUNT
        self.width = width or 1400
        self.height = height or 880
        self.settings = settings
        self._running = False

        rng = random.Random(seed)
        runes = theme.RUNES or ["*"]
        self.glyphs = []
        controls = []
        for index in range(count):
            size = rng.uniform(13, 26)
            control = ft.Container(
                content=ft.Text(runes[index % len(runes)], size=size,
                                color=theme.GOLD, font_family=theme.FONT_DISPLAY),
                left=rng.uniform(0, self.width), top=rng.uniform(0, self.height),
                width=size * 1.6, height=size * 1.6,
                alignment=ft.alignment.center,
                opacity=rng.uniform(0.05, 0.14),
                rotate=ft.Rotate(rng.uniform(-0.4, 0.4)),
            )
            controls.append(control)
            self.glyphs.append({
                "c": control,
                "x": control.left, "y": control.top,
                "speed": rng.uniform(1.6, 4.2),
                "sway": rng.uniform(-0.5, 0.5),
                "peak": rng.uniform(0.05, 0.14),
            })
        self.control = ft.Stack(controls, expand=True)

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="rune-drift").start()

    def stop(self):
        self._running = False

    def resize(self, width, height):
        if width:
            self.width = width
        if height:
            self.height = height

    def _loop(self):
        while self._running:
            if not self.settings.get("animations"):
                time.sleep(1.0)
                continue
            time.sleep(self.FRAME)
            try:
                self._step()
                self.control.update()
            except Exception:
                # A dropped frame is not worth a traceback, and the control
                # may legitimately be gone if the window is closing.
                pass

    def _step(self):
        for glyph in self.glyphs:
            glyph["y"] -= glyph["speed"]
            glyph["x"] += glyph["sway"]
            control = glyph["c"]
            control.top = glyph["y"]
            control.left = glyph["x"]
            # Fade in from the bottom and out at the top, so a glyph never
            # pops into or out of existence at the window edge.
            travel = max(0.0, min(1.0, glyph["y"] / max(1.0, self.height)))
            control.opacity = round(glyph["peak"] * math.sin(travel * math.pi), 3)

            if glyph["y"] < -40:
                glyph["y"] = self.height + random.uniform(0, 120)
                glyph["x"] = random.uniform(0, self.width)
                glyph["speed"] = random.uniform(1.6, 4.2)
                glyph["sway"] = random.uniform(-0.5, 0.5)


class BreathingGlow:
    """A control's shadow swelling and settling, forever.

    Two property writes per cycle - Flutter interpolates between them - so a
    three-second breath costs about forty updates an hour, not forty a
    second. Used on the Play button so the primary action reads as alive
    while the launcher sits idle.
    """

    def __init__(self, control, color, low=18, high=46, period=2.6):
        self.control = control
        self.color = color
        self.low = low
        self.high = high
        self.period = period
        self._up = True
        self._running = False
        control.animate = ft.Animation(int(period * 1000), ft.AnimationCurve.EASE_IN_OUT)

    def start(self, schedule_ui, keep_running, settings=None):
        if self._running:
            return
        self._running = True

        def _loop():
            while keep_running():
                time.sleep(self.period)
                if settings is not None and not settings.get("animations"):
                    continue
                schedule_ui(self._breathe)
            self._running = False

        threading.Thread(target=_loop, daemon=True, name="glow").start()

    def _breathe(self):
        blur = self.high if self._up else self.low
        self._up = not self._up
        try:
            self.control.shadow = ft.BoxShadow(
                blur_radius=blur, spread_radius=1, color=self.color)
            self.control.update()
        except Exception:
            pass


class Sheen:
    """A band of light sweeping across a control.

    Lives inside the control's own Stack and relies on the parent clipping
    it, so the band appears out of one edge and disappears into the other
    rather than floating over the window. One offset write starts a sweep;
    the animation carries it the rest of the way.
    """

    def __init__(self, width, height, period=5.0, duration=0.9):
        self.period = period
        self.duration = duration
        self._running = False
        self.control = ft.Container(
            width=width * 0.35, height=height * 2,
            rotate=ft.Rotate(0.35),
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                colors=["#00ffffff", "#38ffffff", "#00ffffff"],
                stops=[0.0, 0.5, 1.0],
            ),
            offset=ft.Offset(-3.2, 0),
            animate_offset=ft.Animation(int(duration * 1000),
                                        ft.AnimationCurve.EASE_IN_OUT),
            opacity=0,
            animate_opacity=ft.Animation(200, ft.AnimationCurve.LINEAR),
        )

    def start(self, schedule_ui, keep_running, settings=None):
        if self._running:
            return
        self._running = True

        def _loop():
            while keep_running():
                time.sleep(self.period)
                if settings is not None and not settings.get("animations"):
                    continue
                schedule_ui(self._sweep)
                time.sleep(self.duration + 0.1)
                schedule_ui(self._reset)
            self._running = False

        threading.Thread(target=_loop, daemon=True, name="sheen").start()

    def _sweep(self):
        try:
            self.control.opacity = 1
            self.control.offset = ft.Offset(3.2, 0)
            self.control.update()
        except Exception:
            pass

    def _reset(self):
        """Snap back with the animation disabled, so the return is invisible."""
        try:
            self.control.opacity = 0
            self.control.animate_offset = None
            self.control.offset = ft.Offset(-3.2, 0)
            self.control.update()
            self.control.animate_offset = ft.Animation(
                int(self.duration * 1000), ft.AnimationCurve.EASE_IN_OUT)
        except Exception:
            pass


class Pulse:
    """A control quietly breathing in scale and opacity.

    Used on the status dot, where a still dot next to changing text reads as
    a stuck indicator rather than a live one.
    """

    def __init__(self, control, period=1.5, small=0.72, large=1.0):
        self.control = control
        self.period = period
        self.small = small
        self.large = large
        self._big = True
        self._running = False
        control.animate_scale = ft.Animation(int(period * 1000),
                                             ft.AnimationCurve.EASE_IN_OUT)
        control.animate_opacity = ft.Animation(int(period * 1000),
                                               ft.AnimationCurve.EASE_IN_OUT)

    def start(self, schedule_ui, keep_running, settings=None):
        if self._running:
            return
        self._running = True

        def _loop():
            while keep_running():
                time.sleep(self.period)
                if settings is not None and not settings.get("animations"):
                    continue
                schedule_ui(self._beat)
            self._running = False

        threading.Thread(target=_loop, daemon=True, name="pulse").start()

    def _beat(self):
        scale = self.small if self._big else self.large
        self._big = not self._big
        try:
            self.control.scale = ft.Scale(scale)
            self.control.opacity = 0.55 if scale == self.small else 1.0
            self.control.update()
        except Exception:
            pass


class EmberField:
    """Drifting embers, with an idle budget."""

    def __init__(self, width, height, settings, is_minimised, count=None):
        self.width = width or 1100
        self.height = height or 720
        self.settings = settings
        self._is_minimised = is_minimised
        self._focused = True
        self._running = False

        self.particles = []
        containers = []
        for index in range(count or theme.PARTICLE_COUNT):
            size = random.uniform(3, 8)
            color = random.choice(theme.FLAME_COLORS)
            control = ft.Container(
                left=random.uniform(0, self.width), top=random.uniform(0, self.height),
                width=size, height=size, border_radius=size, bgcolor=color,
                opacity=random.uniform(0.35, 0.85),
                # Only the first few get a glow: a BoxShadow is the expensive
                # part of drawing one of these, and eight is enough to read as
                # "these are embers" rather than "these are dots".
                shadow=ft.BoxShadow(blur_radius=size * 2.2, spread_radius=0.5,
                                    color=color) if index < 8 else None,
            )
            containers.append(control)
            self.particles.append({
                "c": control, "x": control.left, "y": control.top,
                "speed": random.uniform(0.5, 1.6),
                "drift": random.uniform(-0.35, 0.35),
            })
        self.control = ft.Stack(containers, expand=True)

    # -- lifecycle -----------------------------------------------------
    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="embers").start()

    def stop(self):
        self._running = False

    def set_focused(self, focused):
        self._focused = focused

    @property
    def running(self):
        return self._running

    def resize(self, width, height):
        if width:
            self.width = width
        if height:
            self.height = height

    # -- animation -----------------------------------------------------
    def _loop(self):
        while self._running:
            if not self.settings.get("animations"):
                time.sleep(1.0)
                continue
            if self._is_minimised():
                time.sleep(1.0)
                continue
            if not self._focused:
                time.sleep(theme.FRAME_INTERVAL_BACKGROUND)
                continue

            time.sleep(theme.FRAME_INTERVAL_FOCUSED)
            try:
                self._step()
                self.control.update()
            except Exception:
                # A dropped frame is not worth a traceback, and the control
                # may legitimately be gone if the window is closing.
                pass

    def _step(self):
        width, height = self.width, self.height
        for particle in self.particles:
            particle["y"] -= particle["speed"]
            particle["x"] += particle["drift"] + (0.12 * (0.5 - random.random()))
            control = particle["c"]
            control.top = particle["y"]
            control.left = particle["x"]
            fade = max(0.0, min(1.0, particle["y"] / height))
            control.opacity = round(0.15 + fade * 0.7, 2)

            if particle["y"] < -10 or particle["x"] < -10 or particle["x"] > width + 10:
                particle["y"] = height + random.uniform(0, 60)
                particle["x"] = random.uniform(0, width)
                particle["speed"] = random.uniform(0.5, 1.6)
                particle["drift"] = random.uniform(-0.35, 0.35)
                control.top = particle["y"]
                control.left = particle["x"]


class SpellBurst:
    """A short outward spark burst, fired once from a point on screen.

    Lives in its own Stack so it can be emptied without touching the ember
    field, and self-clears when the animation ends - a burst left in the tree
    is a handful of controls Flutter keeps compositing forever.
    """

    DURATION = 0.75
    FRAME = 0.03

    def __init__(self, schedule_ui):
        self._schedule_ui = schedule_ui
        self.control = ft.Stack([], expand=True)
        self._busy = threading.Lock()

    def fire(self, origin_x, origin_y, count=None, enabled=True):
        if not enabled or not self._busy.acquire(blocking=False):
            return
        threading.Thread(
            target=self._run, args=(origin_x, origin_y, count or theme.SPARK_COUNT),
            daemon=True, name="spell-burst",
        ).start()

    def _run(self, origin_x, origin_y, count):
        try:
            sparks = []
            controls = []
            for _ in range(count):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(2.5, 7.5)
                size = random.uniform(2.5, 5.5)
                color = random.choice(theme.SPARK_COLORS)
                control = ft.Container(
                    left=origin_x, top=origin_y, width=size, height=size,
                    border_radius=size, bgcolor=color, opacity=1.0,
                    shadow=ft.BoxShadow(blur_radius=size * 3, spread_radius=0.5,
                                        color=color),
                )
                controls.append(control)
                sparks.append({
                    "c": control, "x": origin_x, "y": origin_y,
                    "vx": math.cos(angle) * speed,
                    "vy": math.sin(angle) * speed,
                })

            self._schedule_ui(self._install, controls)

            frames = max(1, int(self.DURATION / self.FRAME))
            for frame in range(frames):
                time.sleep(self.FRAME)
                progress = (frame + 1) / frames
                for spark in sparks:
                    spark["x"] += spark["vx"]
                    spark["y"] += spark["vy"]
                    # Gravity plus drag: sparks arc and slow rather than
                    # flying off in straight lines, which is the difference
                    # between "magic" and "screensaver".
                    spark["vy"] += 0.22
                    spark["vx"] *= 0.96
                    spark["vy"] *= 0.98
                    spark["c"].left = spark["x"]
                    spark["c"].top = spark["y"]
                    spark["c"].opacity = round(max(0.0, 1.0 - progress ** 1.6), 2)
                self._schedule_ui(self._paint)

            self._schedule_ui(self._clear)
        finally:
            self._busy.release()

    # -- UI-thread halves ----------------------------------------------
    def _install(self, controls):
        self.control.controls = controls
        self._safe_update()

    def _paint(self):
        self._safe_update()

    def _clear(self):
        self.control.controls = []
        self._safe_update()

    def _safe_update(self):
        try:
            self.control.update()
        except Exception:
            pass


def background_layer():
    """The static gradient wash that sits under everything else."""
    return ft.Container(expand=True, gradient=theme.background_gradient())
