"""TF2VR bHaptics bridge - Titanfall 2 events -> bHaptics vest and wrists.

    TF2VR bHaptics.exe            run the bridge (start it before or after the game)
    TF2VR bHaptics.exe --test     play a short tour of every effect, no game needed
    TF2VR bHaptics.exe --test hit_front_left explosion_back ...   play selected effects

Needs bHaptics Player running. Settings: %LOCALAPPDATA%\\TF2VR Sensory\\bhaptics\\config.json
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time

import sensory_common as sc
from bhaptics_client import BHaptics
from effects import Engine, FRONT, BACK, COLS, ROWS

TITLE = "TF2VR bHaptics"
VERSION = "1.0.0"
PACKAGE = "bhaptics"

DEFAULTS = {
    "port": 47770,
    "master": 1.0,          # 0.0 - 1.0, scales every motor
    "floor": 60,            # every effect starts at least this strong (0-90)
    "wrists": True,         # Tactosy wrist units
    "gun_hand": "R",        # R or L: which shoulder / wrist gets recoil
    "log_events": False,    # write every event to events.log (for tuning)
}


def locate(ev: dict) -> tuple[str, float, float]:
    """World hit origin -> (face, col, row) on the vest grid."""
    o, p = ev.get("o"), ev.get("p")
    if not o or not p or (abs(o[0]) + abs(o[1]) + abs(o[2])) < 1:
        return FRONT, 1.5, 2.0
    dx, dy, dz = o[0] - p[0], o[1] - p[1], o[2] - p[2]
    horiz = math.hypot(dx, dy)
    # Source engine: yaw is measured from +x toward +y, and +y is to the left.
    rel = math.degrees(math.atan2(dy, dx)) - float(ev.get("yaw", 0.0))
    rel = (rel + 180.0) % 360.0 - 180.0          # 0 front, +90 left, -90 right
    face = FRONT if abs(rel) <= 90 else BACK
    col = (1.0 - math.sin(math.radians(rel))) * (COLS - 1) / 2.0
    if math.hypot(horiz, dz) < 80:
        row = (-dz - 10.0) / 12.0               # origin is on the body: height below the eyes
    else:
        elev = math.degrees(math.atan2(dz, max(horiz, 1.0)))
        row = 2.0 - elev / 20.0                  # origin is the attacker: aim by elevation
    return face, col, max(0.0, min(ROWS - 1.0, row))


class Bridge:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.bh = BHaptics().start()
        self.fx = Engine(self.bh, master=float(cfg["master"]), wrists=bool(cfg["wrists"]),
                         gun_hand=str(cfg["gun_hand"]).upper()[:1] or "R",
                         floor=float(cfg["floor"]))
        self.state: dict = {}
        self.state_at = 0.0
        self.counts: dict[str, int] = {}
        self.level = ""
        self.log = None
        if cfg.get("log_events"):
            self.log = open(os.path.join(sc.app_dir(PACKAGE), "events.log"), "a", encoding="utf-8")
        threading.Thread(target=self._tick, daemon=True, name="tick").start()

    # ---------------------------------------------------------- events
    def on_batch(self, text: str):
        for ev in sc.parse_batch(text):
            try:
                self.handle(ev)
            except Exception as exc:              # one bad event must not stop the bridge
                print(f"  ! error on {ev.get('e')}: {exc}")

    def on_level(self, name: str):
        if name != self.level:
            self.handle({"e": "map", "map": name})

    def handle(self, ev: dict):
        e = ev.get("e", "")
        self.counts[e] = self.counts.get(e, 0) + 1
        if self.log and e != "state":
            self.log.write(f"{time.strftime('%H:%M:%S')} {json.dumps(ev)}\n")
            self.log.flush()
        if e == "state":
            self.state, self.state_at = ev, time.monotonic()
            return
        if e not in ("fire", "reload"):
            detail = " ".join(f"{k}={v}" for k, v in ev.items()
                              if k in ("map", "dmg", "kind", "src", "spd", "doomed"))
            print(f"  {e:10s} {detail}", flush=True)
        fx = self.fx
        if e in ("hit", "thit"):
            face, col, row = locate(ev)
            fx.hit(face, col, row, float(ev.get("dmg", 10)), ev.get("kind", ""),
                   ev.get("src", ""), bool(ev.get("head")), e == "thit", bool(ev.get("shield")))
        elif e == "fire":
            fx.fire(ev.get("w", ""), int(ev.get("n", 1)), bool(ev.get("titan")))
        elif e == "reload":
            fx.reload()
        elif e == "jump":
            fx.jump()
        elif e == "djump":
            fx.double_jump()
        elif e == "walljump":
            fx.wall_jump()
        elif e == "land":
            fx.land(float(ev.get("spd", 300)), bool(ev.get("titan")))
        elif e == "tdash":
            v, yaw = ev.get("v") or [0, 0, 0], float(ev.get("yaw", 0))
            rel = (math.degrees(math.atan2(v[1], v[0])) - yaw + 180) % 360 - 180
            fx.titan_dash(0 if rel < 0 else 3)     # dashing right pushes in from the left
        elif e == "embark":
            fx.embark()
        elif e == "disembark":
            fx.disembark()
        elif e == "eject":
            fx.eject()
        elif e == "death":
            fx.death()
        elif e == "spawn":
            fx.spawn()
        elif e == "map":
            self.level = str(ev.get("map", ""))
            fx.clear()

    def _tick(self):
        """20 Hz loop for effects that follow continuous state."""
        last = {"wall": 0.0, "slide": 0.0, "heart": 0.0, "tstep": 0.0}
        step = 0
        while True:
            time.sleep(0.05)
            now = time.monotonic()
            s = self.state
            if not s or now - self.state_at > 0.5:
                continue          # paused, loading, or game closed
            hp, spd = float(s.get("hp", 1)), float(s.get("spd", 0))
            if s.get("wall") and now - last["wall"] > 0.16:
                last["wall"] = now
                step += 1
                self.fx.wall_step(step)
            if s.get("slide") and spd > 100 and now - last["slide"] > 0.1:
                last["slide"] = now
                self.fx.slide_tick()
            if 0 < hp < 0.4:
                if now - last["heart"] > 0.55 + 0.6 * (hp / 0.4):
                    last["heart"] = now
                    self.fx.heartbeat(1.0 - hp / 0.4)
            if s.get("titan") and s.get("ground") and spd > 60:
                if now - last["tstep"] > max(0.3, 0.9 - spd / 600.0):
                    last["tstep"] = now
                    step += 1
                    self.fx.titan_step(step)

    def status(self) -> dict:
        return {"app": TITLE, "version": VERSION, "level": self.level,
                "bhaptics": self.bh.positions, "counts": self.counts}


# ---------------------------------------------------------------- test tour
def test_tour(bridge: Bridge, only: list[str]):
    fx = bridge.fx
    p = [0.0, 0.0, 60.0]

    def at(angle, dz=-30.0, dist=40.0):
        a = math.radians(angle)
        return {"o": [dist * math.cos(a), dist * math.sin(a), 60.0 + dz], "p": p, "yaw": 0.0}

    def hit(ang, **kw):
        bridge.handle({"e": "hit", "dmg": 20, "kind": "bullet", **at(ang), **kw})

    def repeat(fn, n, gap):
        for _ in range(n):
            fn()
            time.sleep(gap)

    steps = [
        ("hit_front_left",  "Bullet - front, your left chest",  lambda: hit(60, dz=-20)),
        ("hit_front_right", "Bullet - front, your right belly", lambda: hit(-40, dz=-50)),
        ("hit_back_left",   "Bullet - back, your left",         lambda: hit(150)),
        ("hit_back_right",  "Bullet - back, your right",        lambda: hit(-150)),
        ("headshot",        "Headshot",                         lambda: hit(0, head=True)),
        ("shotgun",         "Shotgun spread",                   lambda: hit(10, kind="shotgun", dmg=60)),
        ("melee",           "Melee punch",                      lambda: hit(-30, kind="melee", dmg=60)),
        ("explosion_back",  "Explosion behind you",             lambda: hit(180, kind="explosion", dmg=60)),
        ("electric",        "Electric (arc) hit",               lambda: hit(0, kind="electric", dmg=30)),
        ("burn",            "Thermite burn",                    lambda: hit(0, kind="other", src="mp_weapon_thermite_grenade")),
        ("fall",            "Fall damage",                      lambda: hit(0, kind="other", src="fall")),
        ("fire_pistol",     "Recoil: pistol",                   lambda: repeat(lambda: fx.fire("mp_weapon_semipistol"), 3, 0.25)),
        ("fire_smg",        "Recoil: R-97 burst",               lambda: repeat(lambda: fx.fire("mp_weapon_r97"), 12, 0.07)),
        ("fire_shotgun",    "Recoil: EVA-8",                    lambda: repeat(lambda: fx.fire("mp_weapon_shotgun"), 2, 0.5)),
        ("fire_kraber",     "Recoil: Kraber",                   lambda: fx.fire("mp_weapon_sniper")),
        ("reload",          "Reload",                           lambda: repeat(fx.reload, 2, 0.3)),
        ("jump",            "Jump + double jump",               lambda: (fx.jump(), time.sleep(0.35), fx.double_jump())),
        ("land_hard",       "Hard landing",                     lambda: fx.land(800)),
        ("wallrun",         "Wallrun",                          lambda: [fx.wall_step(i) or time.sleep(0.16) for i in range(12)]),
        ("slide",           "Slide",                            lambda: repeat(fx.slide_tick, 15, 0.1)),
        ("heartbeat",       "Low-health heartbeat",             lambda: repeat(lambda: fx.heartbeat(0.8), 5, 0.6)),
        ("embark",          "Embark into your titan",           fx.embark),
        ("titan_walk",      "Titan footsteps",                  lambda: [fx.titan_step(i) or time.sleep(0.45) for i in range(6)]),
        ("titan_fire",      "Titan 40mm",                       lambda: repeat(lambda: fx.fire("mp_titanweapon_sticky_40mm", titan=True), 3, 0.4)),
        ("titan_hit",       "Titan hull hit",                   lambda: bridge.handle({"e": "thit", "dmg": 400, "kind": "bullet", **at(45, dz=0, dist=800)})),
        ("titan_shield",    "Titan shield hit",                 lambda: bridge.handle({"e": "thit", "dmg": 200, "kind": "bullet", "shield": True, **at(0, dz=0, dist=800)})),
        ("titan_dash",      "Titan dash",                       lambda: fx.titan_dash(0)),
        ("eject",           "Eject",                            fx.eject),
        ("death",           "Death",                            fx.death),
    ]
    if only:
        steps = [s for s in steps if s[0] in only]
    for name, label, fn in steps:
        print(f"  >>> {label}  ({name})", flush=True)
        time.sleep(0.8)
        fn()
        time.sleep(2.0)
    time.sleep(1.0)


def startup_checks():
    games = sc.find_games()
    if not games:
        print("  ! Titanfall 2 not found - run the installer, or ignore if it's in an unusual place.")
    for g in games:
        for p in sc.profiles(g):
            print(f"  {p.label}: game mod {sc.feed_status(p)}")


def main():
    os.system("")                                  # enable ANSI on Windows consoles
    try:
        os.system(f"title {TITLE}")
    except Exception:
        pass
    print(f"{TITLE} {VERSION}\n")
    cfg = sc.load_config(PACKAGE, DEFAULTS)
    bridge = Bridge(cfg)
    if not bridge.bh.wait_connected(6):
        print("  ! bHaptics Player is not reachable - start bHaptics Player and this will connect on its own.")
    else:
        print("  bHaptics Player:", ", ".join(bridge.bh.positions) or "connected (no devices reported yet)")

    if "--test" in sys.argv:
        print("\nPlaying the effect tour...\n")
        test_tour(bridge, [a for a in sys.argv[1:] if not a.startswith("--")])
        bridge.bh.close()
        print("\nDone.")
        if sys.stdin and sys.stdin.isatty():
            input("Press Enter to close...")
        return

    startup_checks()
    threading.Thread(target=sc.tail_logs, args=(sc.log_folders(), bridge.on_level, bridge.on_batch),
                     daemon=True).start()
    print(f"\nReady on port {cfg['port']}. Play Titanfall 2 - leave this window open. Ctrl+C to quit.\n")
    try:
        sc.serve_events(int(cfg["port"]), bridge.on_batch, bridge.status)
    except OSError as e:
        print(f"  ! Could not listen on port {cfg['port']}: {e}\n    Is another copy of {TITLE} already running?")
        if sys.stdin and sys.stdin.isatty():
            input("Press Enter to close...")
    except KeyboardInterrupt:
        bridge.fx.clear()


if __name__ == "__main__":
    main()
