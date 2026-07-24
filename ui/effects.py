"""The moving parts of the background.

Three layers, drawn back to front:

``Starfield``
    Static. Seeded from a fixed value so the sky is the same every launch -
    a background that rearranges itself on every start reads as a glitch.

``EmberField``
    The drifting flame particles. This is the only thing here that costs
    CPU, so it is also the only thing with an idle discipline: when the
    window is unfocused, minimised, or animations are switched off, it all
    but stops. The window still redraws on demand; it just stops burning a
    core to animate pixels nobody is looking at.

``SpellBurst``
    A one-shot flourish fired when the player presses Play. Sparks fly
    outward from the button and fade. It runs for well under a second and
    then removes itself, so it never competes with the launch it is
    celebrating.

Everything runs on a daemon thread and mutates control properties directly,
then calls ``update()`` on the one Stack that owns them. That is much
cheaper than rebuilding controls per frame, and it is why the whole
background costs a single update call per frame rather than one per particle.
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


class RuneRing:
    """A slowly turning circle of runes, floating behind the title.

    Belongs in a *background* layer rather than in the content column: it is
    decoration, and a decoration that reserves its own height in the layout
    punches a hole in whatever it was meant to sit behind.

    Deliberately cheap. Flutter interpolates the rotation, so keeping it
    turning costs one property write and one update every
    :data:`theme.RUNE_ROTATION_SECONDS` - roughly two updates a minute,
    against the ember field's eighteen a second.
    """

    def __init__(self, size=420, glyph_count=8):
        glyphs = []
        runes = theme.RUNES or ["*"]
        radius = size / 2 - 18
        for index in range(glyph_count):
            angle = (2 * math.pi / glyph_count) * index
            glyphs.append(ft.Container(
                content=ft.Text(runes[index % len(runes)], size=15,
                                color=theme.GOLD, font_family=theme.FONT_DISPLAY),
                left=size / 2 + radius * math.cos(angle) - 10,
                top=size / 2 + radius * math.sin(angle) - 10,
                width=20, height=20, alignment=ft.alignment.center,
                opacity=0.16, rotate=ft.Rotate(angle + math.pi / 2),
            ))

        ring = ft.Container(
            width=size, height=size, border_radius=size / 2,
            border=ft.border.all(0.8, theme.GOLD_DIM), opacity=0.35,
        )
        self.control = ft.Container(
            content=ft.Stack([ring] + glyphs, width=size, height=size),
            width=size, height=size,
            rotate=ft.Rotate(0),
            animate_rotation=ft.Animation(
                int(theme.RUNE_ROTATION_SECONDS * 1000), ft.AnimationCurve.LINEAR),
        )
        self._angle = 0.0
        self._running = False

    def start(self, schedule_ui, keep_running, settings=None):
        """Keep the ring turning.

        A half-turn is scheduled every ``RUNE_ROTATION_SECONDS``, which is
        exactly the animation duration - so each new target is set as the
        previous one lands and the motion reads as continuous rather than as
        a series of sweeps.
        """
        if self._running:
            return
        self._running = True

        def _loop():
            while keep_running():
                time.sleep(theme.RUNE_ROTATION_SECONDS)
                if settings is not None and not settings.get("animations"):
                    continue
                schedule_ui(self._turn)
            self._running = False

        threading.Thread(target=_loop, daemon=True, name="rune-ring").start()

    def _turn(self):
        self._angle += math.pi
        self.control.rotate = ft.Rotate(self._angle)
        try:
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
