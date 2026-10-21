"""Haptic effects for the TactSuit X40 + Tactosy wrists.

Vest grid, confirmed on your suit (both faces, as worn):
    index = row * 4 + col      row 0 = shoulders ... row 4 = waist
                               col 0 = your LEFT ... col 3 = your RIGHT
Wrists (ForearmL / ForearmR) are driven as a whole unit.

Every effect is a list of steps scheduled on one timeline, so effects
overlap and mix instead of cutting each other off.
"""
from __future__ import annotations

import heapq
import itertools
import math
import random
import threading
import time

FRONT, BACK = "VestFront", "VestBack"
WRIST_L, WRIST_R = "ForearmL", "ForearmR"
COLS, ROWS = 4, 5
WRIST_MOTORS = 6


def idx(col: int, row: int) -> int:
    return max(0, min(ROWS - 1, row)) * COLS + max(0, min(COLS - 1, col))


def blob(col: float, row: float, radius: float, peak: float) -> dict[int, int]:
    """Soft round spot centred on (col,row) in grid units."""
    out = {}
    for r in range(ROWS):
        for c in range(COLS):
            d = math.hypot(c - col, r - row)
            if d <= radius:
                v = peak * (1.0 - (d / (radius + 0.6)) ** 2)
                if v >= 5:
                    out[idx(c, r)] = int(v)
    if not out:
        out[idx(round(col), round(row))] = int(peak)
    return out


def row_dots(row: int, v: float) -> dict[int, int]:
    return {idx(c, row): int(v) for c in range(COLS)}


def all_dots(v: float) -> dict[int, int]:
    return {i: int(v) for i in range(COLS * ROWS)}


def wrist(v: float) -> dict[int, int]:
    return {i: int(v) for i in range(WRIST_MOTORS)}


class Engine:
    """Timeline + output scaling. `out` is a BHaptics client."""

    def __init__(self, out, master: float = 1.0, wrists: bool = True,
                 gun_hand: str = "R", floor: float = 60.0):
        self.out = out
        self.master = master
        self.wrists = wrists
        self.gun_hand = gun_hand
        self.floor = max(0.0, min(90.0, float(floor)))
        self._heap: list = []
        self._seq = itertools.count()
        self._keys = itertools.count()
        self._cv = threading.Condition()
        threading.Thread(target=self._run, daemon=True, name="timeline").start()

    # ------------------------------------------------------------ timeline
    def at(self, delay: float, position: str, dots: dict[int, int],
           millis: int, key: str):
        with self._cv:
            heapq.heappush(self._heap, (time.monotonic() + delay,
                                        next(self._seq),
                                        (position, dots, millis, key)))
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while not self._heap:
                    self._cv.wait()
                due = self._heap[0][0]
                now = time.monotonic()
                if due > now:
                    self._cv.wait(due - now)
                    continue
                _, _, (pos, dots, ms, key) = heapq.heappop(self._heap)
            if pos in (WRIST_L, WRIST_R) and not self.wrists:
                continue
            scaled = {i: self.curve(v) for i, v in dots.items() if v > 0}
            self.out.dots(pos, scaled, ms, key)

    def curve(self, v: float) -> int:
        """Perceptual floor: the vest barely registers below ~45%."""
        v = max(0.0, min(100.0, v))
        return int(round((self.floor + v * (100 - self.floor) / 100) * self.master))

    def key(self, name: str) -> str:
        # A rotating suffix lets repeats of one effect overlap instead of
        # replacing each other on the Player.
        return f"{name}-{next(self._keys) % 6}"

    def clear(self):
        with self._cv:
            self._heap.clear()
        self.out.stop_all()

    # ------------------------------------------------------------ helpers
    def gun_wrist(self):
        return WRIST_R if self.gun_hand == "R" else WRIST_L

    def gun_cols(self):
        return (2, 3) if self.gun_hand == "R" else (1, 0)

    # =================================================================
    # Damage
    # =================================================================
    def hit(self, face: str, col: float, row: float, dmg: float,
            kind: str = "bullet", src: str = "", head: bool = False,
            titan: bool = False, shield: bool = False):
        k = self.key("hit")
        p = max(60.0, min(100.0, (55 + dmg * 1.2) if not titan else (55 + dmg * 0.1)))
        other = BACK if face == FRONT else FRONT
        s = src.lower()

        if titan and shield:
            # Shield fizz: light, crackly, spread.
            for n in range(4):
                d = blob(col + random.uniform(-1, 1), row + random.uniform(-1, 1), 1.0, 35)
                self.at(n * 0.04, face, d, 50, k)
            return

        if kind == "melee":
            self.at(0, face, blob(col, row, 1.8, 100), 260, k)
            self.at(0.26, face, blob(col, row, 1.2, 60), 180, k)
            return

        if kind == "explosion" or "grenade" in s or "rocket" in s:
            # Shockwave: blast side first, bleeds through to the other face.
            self.at(0, face, all_dots(100), 220, k)
            self.at(0.10, other, all_dots(75), 220, k)
            self.at(0.30, face, all_dots(55), 250, k)
            self.at(0.30, other, all_dots(40), 250, k)
            if self.wrists:
                self.at(0.04, WRIST_L, wrist(80), 250, k)
                self.at(0.04, WRIST_R, wrist(80), 250, k)
            return

        if kind == "electric" or "arc" in s or "emp" in s or "electric" in s:
            for n in range(10):
                pts = {}
                for _ in range(3):
                    pts.update(blob(col + random.uniform(-1.5, 1.5),
                                    row + random.uniform(-1.5, 1.5), 0.4, 70))
                self.at(n * 0.05, face, pts, 70, k)
            if self.wrists:
                for n in range(5):
                    self.at(n * 0.1, random.choice((WRIST_L, WRIST_R)), wrist(70), 80, k)
            return

        if "thermite" in s or "burn" in s or "fire" in s or "flame" in s:
            for n, r in enumerate((4, 3, 2, 1)):
                self.at(n * 0.15, face, row_dots(r, 70), 260, k)
            return

        if "fall" in s:
            self.at(0, FRONT, row_dots(4, 100), 220, k)
            self.at(0, BACK, row_dots(4, 100), 220, k)
            self.at(0.06, FRONT, row_dots(3, 75), 180, k)
            self.at(0.06, BACK, row_dots(3, 75), 180, k)
            return

        if kind == "shotgun":
            for _ in range(5):
                self.at(random.uniform(0, 0.04), face,
                        blob(col + random.uniform(-1.3, 1.3),
                             row + random.uniform(-1.3, 1.3), 0.5, p), 90, k)
            return

        if head:
            self.at(0, face, row_dots(0, 100), 70, k)
            self.at(0.11, face, row_dots(0, 80), 70, k)
            return

        # Plain bullet: tight, sharp. Titans get a bigger, duller thud.
        if titan:
            self.at(0, face, blob(col, row, 1.4, p), 140, k)
        else:
            self.at(0, face, blob(col, row, 1.0, p), 130, k)

    def health_segment_lost(self):
        k = self.key("seg")
        self.at(0, FRONT, all_dots(100), 200, k)
        self.at(0, BACK, all_dots(100), 200, k)

    # =================================================================
    # Life / titan transitions
    # =================================================================
    def death(self):
        self.clear()
        k = "death"
        for n, v in enumerate((100, 80, 60, 40, 25, 12)):
            self.at(n * 0.25, FRONT, all_dots(v), 260, k)
            self.at(n * 0.25, BACK, all_dots(v), 260, k)
            if self.wrists:
                self.at(n * 0.25, WRIST_L, wrist(v * 0.7), 260, k)
                self.at(n * 0.25, WRIST_R, wrist(v * 0.7), 260, k)

    def spawn(self):
        k = self.key("spawn")
        for n, r in enumerate((4, 3, 2, 1, 0)):
            self.at(n * 0.07, FRONT, row_dots(r, 30), 90, k)
            self.at(n * 0.07, BACK, row_dots(r, 30), 90, k)

    def embark(self):
        k = self.key("embark")
        # Lifted up into the cockpit...
        for n, r in enumerate((4, 3, 2, 1, 0)):
            self.at(n * 0.1, FRONT, row_dots(r, 50), 130, k)
            self.at(n * 0.1, BACK, row_dots(r, 50), 130, k)
        # ...hatch slams shut...
        self.at(0.75, FRONT, all_dots(85), 160, k)
        self.at(0.75, BACK, all_dots(60), 160, k)
        # ...systems come online: a slow scan down the back.
        for n, r in enumerate((0, 1, 2, 3, 4)):
            self.at(1.4 + n * 0.12, BACK, row_dots(r, 35), 150, k)

    def disembark(self):
        k = self.key("disembark")
        for n, r in enumerate((0, 1, 2, 3, 4)):
            self.at(n * 0.06, FRONT, row_dots(r, 40), 80, k)

    def eject(self):
        k = self.key("eject")
        for n, r in enumerate((4, 3, 2, 1, 0)):
            self.at(n * 0.05, BACK, row_dots(r, 100), 110, k)
            self.at(n * 0.05, FRONT, row_dots(r, 80), 110, k)
        if self.wrists:
            self.at(0.1, WRIST_L, wrist(80), 250, k)
            self.at(0.1, WRIST_R, wrist(80), 250, k)

    # =================================================================
    # Weapons
    # =================================================================
    WEAPON_CLASSES = [
        # (substring, class)
        ("titanweapon", "titan"),
        ("shotgun_pistol", "pistol_heavy"),   # Mozambique
        ("mastiff", "shotgun"), ("shotgun", "shotgun"),
        ("sniper", "sniper"), ("dmr", "sniper"), ("doubletake", "sniper"),
        ("rocket", "launcher"), ("softball", "launcher"), ("epg", "launcher"),
        ("arc_launcher", "launcher"), ("smr", "launcher"), ("defender", "sniper"),
        ("wingman", "pistol_heavy"),
        ("pistol", "pistol"), ("autopistol", "pistol"), ("semipistol", "pistol"),
        ("lmg", "lmg"), ("esaw", "lmg"), ("lstar", "lmg"),
        ("hemlok", "rifle"), ("rspn101", "rifle"), ("g2", "rifle"), ("vinson", "rifle"),
        ("r97", "smg"), ("car", "smg"), ("alternator", "smg"), ("hemlock", "rifle"),
    ]
    RECOIL = {
        # class: (vest peak, vest ms, wrist peak, radius)
        "pistol":       (35, 50, 45, 0.6),
        "pistol_heavy": (60, 70, 70, 0.9),
        "smg":          (28, 35, 35, 0.5),
        "rifle":        (38, 45, 45, 0.7),
        "lmg":          (42, 45, 45, 0.8),
        "shotgun":      (80, 90, 85, 1.3),
        "sniper":       (85, 100, 90, 1.2),
        "launcher":     (75, 120, 70, 1.5),
        "titan":        (70, 90, 0, 1.6),
        "other":        (35, 50, 40, 0.6),
    }

    def classify(self, weapon: str) -> str:
        w = weapon.lower()
        if "melee" in w or "grenade" in w or "ability" in w:
            return "none"
        for sub, cls in self.WEAPON_CLASSES:
            if sub in w:
                return cls
        return "other"

    def fire(self, weapon: str, shots: int = 1, titan: bool = False):
        cls = "titan" if titan else self.classify(weapon)
        if cls == "none":
            return
        peak, ms, wpeak, rad = self.RECOIL[cls]
        k = self.key("fire")
        if cls == "titan":
            heavy = any(s in weapon for s in ("sticky_40mm", "leadwall", "laser",
                                              "rocketeer", "meteor", "salvo"))
            v = peak + (20 if heavy else 0)
            self.at(0, FRONT, blob(1.5, 0.6, rad, v), ms, k)
            self.at(0, BACK, blob(1.5, 0.6, rad, v * 0.4), ms, k)
            return
        c0, c1 = self.gun_cols()
        self.at(0, FRONT, blob((c0 + c1) / 2, 0.4, rad, peak), ms, k)
        if cls in ("shotgun", "sniper", "launcher", "pistol_heavy"):
            self.at(0.02, BACK, blob((c0 + c1) / 2, 0.4, rad * 0.8, peak * 0.4), ms, k)
        if self.wrists and wpeak:
            self.at(0, self.gun_wrist(), wrist(wpeak), ms, k)

    def reload(self):
        k = self.key("reload")
        c0, _ = self.gun_cols()
        self.at(0, FRONT, {idx(c0, 3): 30}, 40, k)
        if self.wrists:
            self.at(0, WRIST_L if self.gun_hand == "R" else WRIST_R, wrist(30), 40, k)

    # =================================================================
    # Movement
    # =================================================================
    def jump(self):
        k = self.key("jump")
        self.at(0, FRONT, row_dots(4, 30), 60, k)
        self.at(0, BACK, row_dots(4, 30), 60, k)

    def double_jump(self):
        # Jump kit thrust: a burst low on the back.
        k = self.key("djump")
        self.at(0, BACK, {**row_dots(4, 75), **row_dots(3, 55)}, 140, k)
        self.at(0.1, BACK, row_dots(2, 30), 80, k)

    def wall_jump(self):
        k = self.key("wjump")
        self.at(0, BACK, {idx(c, r): 55 for c in (1, 2) for r in (2, 3)}, 100, k)

    def land(self, speed: float, titan: bool = False):
        k = self.key("land")
        v = max(25.0, min(100.0, (speed - 200) / 6))
        if titan:
            v = 100
        self.at(0, FRONT, row_dots(4, v), 110, k)
        self.at(0, BACK, row_dots(4, v), 110, k)
        if v > 55:
            self.at(0.05, FRONT, row_dots(3, v * 0.5), 90, k)
            self.at(0.05, BACK, row_dots(3, v * 0.5), 90, k)

    def titan_dash(self, side_col: int):
        k = self.key("tdash")
        cols = (0, 1) if side_col <= 1 else (2, 3)
        d = {idx(c, r): 85 for c in cols for r in range(ROWS)}
        self.at(0, FRONT, d, 160, k)
        self.at(0, BACK, d, 160, k)

    # ---------- continuous (called from the bridge's 20 Hz tick) -------
    def wall_step(self, step: int):
        k = "wallrun"
        c = 0 if step % 2 == 0 else 3
        self.at(0, FRONT, {idx(c, 3): 28, idx(c, 2): 18}, 60, k)

    def slide_tick(self):
        k = "slide"
        d = {idx(random.randrange(COLS), 4): random.randint(18, 32) for _ in range(3)}
        self.at(0, FRONT, d, 60, k)

    def heartbeat(self, strength: float):
        k = "heart"
        v = 30 + 50 * strength
        spot = {idx(1, 1): v, idx(1, 2): v * 0.7, idx(0, 1): v * 0.6}
        self.at(0, FRONT, spot, 70, k)
        self.at(0.16, FRONT, {i: x * 0.6 for i, x in spot.items()}, 70, k)

    def titan_step(self, step: int):
        k = "tstep"
        c = (0, 1) if step % 2 == 0 else (2, 3)
        d = {idx(x, 4): 45 for x in c}
        self.at(0, FRONT, d, 90, k)
        self.at(0, BACK, d, 90, k)
