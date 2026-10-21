"""Shared pieces for the TF2VR sensory packages (bHaptics and Omara).

This file is identical in both repos (synced from shared/ by tools/sync_shared.py):
  * finding Titanfall 2 and its Northstar profiles (TF2VR VR mod, plain Northstar)
  * installing / removing the shared TF2VR.SensoryEvents game mod
  * the local HTTP receiver the game mod posts to
  * the Northstar log reader (current level + fallback event transport)
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FEED_MOD = "TF2VR.SensoryEvents"
FEED_VERSION = "1.0.0"
LEGACY_MODS = ("TF2VR.Haptics",)          # prototype names to clean up
HTTP_FLAG = "-allowlocalhttp"
LEVEL_TAG = "UICodeCallback_UpdateLoadingLevelName: "
DATA_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "TF2VR Sensory")
REGISTRY = os.path.join(DATA_ROOT, "packages.json")


# ======================================================================
# Paths and config
# ======================================================================
def resource_dir() -> str:
    """Folder holding bundled files (PyInstaller's temp dir when frozen)."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def app_dir(package: str) -> str:
    return os.path.join(DATA_ROOT, package)


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save_json(path: str, data, indent: int = 2):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)
        f.write("\n")
    os.replace(tmp, path)


def load_config(package: str, defaults: dict) -> dict:
    """Per-user config in %LOCALAPPDATA%\\TF2VR Sensory\\<package>\\config.json.

    Missing keys are filled from defaults and written back, so new options
    appear in the file after an update.
    """
    path = os.path.join(app_dir(package), "config.json")
    user = load_json(path, {})
    merged = _merge(defaults, user if isinstance(user, dict) else {})
    if merged != user:
        save_json(path, merged)
    return merged


def _merge(defaults: dict, user: dict) -> dict:
    out = dict(defaults)
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(defaults.get(k), dict):
            out[k] = _merge(defaults[k], v)
        else:
            out[k] = v
    return out


# ======================================================================
# Finding the game
# ======================================================================
def _steam_libraries() -> list[str]:
    roots = []
    try:
        import winreg
        for hive, key, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(winreg.QueryValueEx(k, name)[0])
            except OSError:
                pass
    except ImportError:
        pass
    libs = []
    for root in roots:
        root = os.path.normpath(root)
        libs.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
        except OSError:
            pass
    seen, out = set(), []
    for p in libs:
        key = os.path.normcase(os.path.normpath(p))
        if key not in seen:
            seen.add(key)
            out.append(os.path.normpath(p))
    return out


def find_games() -> list[str]:
    """Every Titanfall 2 install found (Steam libraries, EA app, common folders)."""
    candidates = [os.path.join(lib, "steamapps", "common", "Titanfall2") for lib in _steam_libraries()]
    try:
        import winreg
        for key in (r"SOFTWARE\Respawn\Titanfall2", r"SOFTWARE\WOW6432Node\Respawn\Titanfall2"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                    candidates.append(winreg.QueryValueEx(k, "Install Dir")[0])
            except OSError:
                pass
    except ImportError:
        pass
    for drive in "CDEFGH":
        candidates += [f"{drive}:\\Program Files\\EA Games\\Titanfall2",
                       f"{drive}:\\Program Files (x86)\\Origin Games\\Titanfall2"]
    found = []
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "Titanfall2.exe")) and \
                os.path.normcase(c) not in map(os.path.normcase, found):
            found.append(os.path.normpath(c))
    return found


class Profile:
    """One Northstar profile inside a game install (TF2VR or R2Northstar)."""

    def __init__(self, game: str, name: str):
        self.game, self.name = game, name
        self.root = os.path.join(game, name)
        self.mods = os.path.join(self.root, "mods")
        self.logs = os.path.join(self.root, "logs")
        self.enabled = os.path.join(self.root, "enabledmods.json")

    @property
    def label(self) -> str:
        return "Titanfall 2 VR (TF2VR)" if self.name == "TF2VR" else "Northstar (flat screen)"

    def exists(self) -> bool:
        return os.path.isdir(os.path.join(self.mods, "Northstar.Client"))


def profiles(game: str) -> list[Profile]:
    return [p for p in (Profile(game, "TF2VR"), Profile(game, "R2Northstar")) if p.exists()]


# ======================================================================
# Installing the shared game mod
# ======================================================================
_HOOKS = (
    ("ServerCallback_PilotTookDamage",
     "TFSE_PilotDamage( damage, damageOrigin, damageType, damageSourceId )"),
    ("ServerCallback_TitanTookDamage",
     "TFSE_TitanDamage( damage, damageOrigin, damageType, damageSourceId, doomedNow )"),
)
_HOOK_MARK = "// TF2VR.SensoryEvents"
_DMG_REL = os.path.join("mod", "scripts", "vscripts", "client", "cl_damage_indicator.gnut")


def build_damage_hook(source_text: str) -> str:
    """Add one call per damage callback, right after the damageOrigin line."""
    text = source_text
    for func, call in _HOOKS:
        start = text.find("function " + func + "(")
        if start < 0:
            raise RuntimeError(f"{func} not found in cl_damage_indicator.gnut")
        m = re.compile(r"^([ \t]*)vector damageOrigin = < x, y, z >[ \t]*\r?\n", re.M).search(text, start)
        if not m:
            raise RuntimeError(f"hook point not found in {func}")
        nl = "\r\n" if text[m.end() - 2:m.end()] == "\r\n" else "\n"
        text = text[:m.end()] + f"{m.group(1)}{call} {_HOOK_MARK}{nl}" + text[m.end():]
    return text


def install_feed(p: Profile, log=print):
    src = os.path.join(resource_dir(), "game-mod", FEED_MOD)
    dest = os.path.join(p.mods, FEED_MOD)
    for legacy in LEGACY_MODS:
        old = os.path.join(p.mods, legacy)
        if os.path.isdir(old):
            shutil.rmtree(old)
            log(f"  removed old prototype mod {legacy}")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

    # The damage hook is generated from this profile's own Northstar copy so it
    # always matches the installed Northstar version.
    nsc = os.path.join(p.mods, "Northstar.Client", _DMG_REL)
    with open(nsc, encoding="utf-8", newline="") as f:
        hooked = build_damage_hook(f.read())
    os.makedirs(os.path.dirname(os.path.join(dest, _DMG_REL)), exist_ok=True)
    with open(os.path.join(dest, _DMG_REL), "w", encoding="utf-8", newline="") as f:
        f.write(hooked)

    _backup(p.enabled)
    enabled = load_json(p.enabled, {"Version": 1})
    for legacy in LEGACY_MODS:
        enabled.pop(legacy, None)
    enabled[FEED_MOD] = {FEED_VERSION: True}
    save_json(p.enabled, enabled, indent=4)
    set_http_flag(p, True)
    log(f"  {p.label}: installed {FEED_MOD} {FEED_VERSION} and enabled it")


def remove_feed(p: Profile, log=print):
    dest = os.path.join(p.mods, FEED_MOD)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    enabled = load_json(p.enabled, None)
    if isinstance(enabled, dict) and FEED_MOD in enabled:
        enabled.pop(FEED_MOD)
        save_json(p.enabled, enabled, indent=4)
    set_http_flag(p, False)
    log(f"  {p.label}: removed {FEED_MOD}")


def feed_status(p: Profile) -> str:
    dmg = os.path.join(p.mods, FEED_MOD, _DMG_REL)
    if not os.path.isfile(dmg):
        return "not installed"
    with open(dmg, encoding="utf-8", errors="replace") as f:
        hooks = f.read().count(_HOOK_MARK)
    flag = "on" if http_flag(p) else "MISSING"
    return f"installed (hooks {hooks}/2, {HTTP_FLAG} {flag})"


def _backup(path: str):
    b = path + ".sensory-backup"
    if os.path.isfile(path) and not os.path.exists(b):
        shutil.copy2(path, b)


# -allowlocalhttp: the VR launcher builds its command line from tools\launch.json;
# plain Northstar reads ns_startup_args.txt from the game folder.
def _launch_json(p: Profile) -> str:
    return os.path.join(p.root, "tools", "launch.json")


def http_flag(p: Profile) -> bool:
    if p.name == "TF2VR":
        return HTTP_FLAG in load_json(_launch_json(p), {}).get("arguments", [])
    path = os.path.join(p.game, "ns_startup_args.txt")
    try:
        with open(path, encoding="utf-8-sig") as f:
            return HTTP_FLAG in f.read().split()
    except OSError:
        return False


def set_http_flag(p: Profile, on: bool):
    if p.name == "TF2VR":
        path = _launch_json(p)
        data = load_json(path, None)
        if not isinstance(data, dict):
            return
        args = [a for a in data.get("arguments", []) if a != HTTP_FLAG]
        if on:
            args.insert(0, HTTP_FLAG)
        if args != data.get("arguments"):
            _backup(path)
            data["arguments"] = args
            save_json(path, data, indent=2)
        return
    path = os.path.join(p.game, "ns_startup_args.txt")
    try:
        with open(path, encoding="utf-8-sig") as f:
            parts = f.read().split()
    except OSError:
        parts = []
    new = [a for a in parts if a != HTTP_FLAG] + ([HTTP_FLAG] if on else [])
    if new != parts:
        _backup(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(" ".join(new))


# ======================================================================
# Which packages use the shared feed
# ======================================================================
def register(package: str, info: dict):
    reg = load_json(REGISTRY, {})
    reg[package] = info
    save_json(REGISTRY, reg)


def unregister(package: str) -> dict:
    reg = load_json(REGISTRY, {})
    reg.pop(package, None)
    save_json(REGISTRY, reg)
    return reg


def registry() -> dict:
    return load_json(REGISTRY, {})


# ======================================================================
# Shortcuts
# ======================================================================
def make_shortcut(link: str, target: str, args: str = "", workdir: str = "", description: str = ""):
    import subprocess
    os.makedirs(os.path.dirname(link), exist_ok=True)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:L);"
          "$s.TargetPath=$env:T;$s.Arguments=$env:A;$s.WorkingDirectory=$env:W;"
          "$s.Description=$env:D;$s.IconLocation=$env:T+',0';$s.Save()")
    env = dict(os.environ, L=link, T=target, A=args, W=workdir or os.path.dirname(target), D=description)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   env=env, check=True, capture_output=True)


def start_menu_dir() -> str:
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "TF2VR Sensory")


def desktop_dir() -> str:
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)   # CSIDL_DESKTOPDIRECTORY
        if buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


# ======================================================================
# Receiving events
# ======================================================================
def serve_events(port: int, on_batch, status=lambda: {}):
    """Blocking HTTP server for POST /e (JSON array of events)."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode("utf-8", "replace")
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            on_batch(body)

        def do_GET(self):
            payload = json.dumps({"ok": True, **status()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def parse_batch(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def tail_logs(folders: list[str], on_level, on_batch, stop: threading.Event | None = None):
    """Follow the newest nslog*.txt across the given folders.

    on_level(name) - when a level loads (and once at start for the current one)
    on_batch(text) - "[TFSE] [...]" fallback lines, used when HTTP is unavailable
    """
    current, f = None, None
    while not (stop and stop.is_set()):
        files = []
        for d in folders:
            files += glob.glob(os.path.join(d, "nslog*.txt"))
        newest = max(files, key=os.path.getmtime) if files else None
        if newest and newest != current:
            current = newest
            f = open(current, encoding="utf-8", errors="replace")
            last = None
            for line in f:
                j = line.find(LEVEL_TAG)
                if j >= 0:
                    last = line[j + len(LEVEL_TAG):].strip()
            if last:
                on_level(last)
        line = f.readline() if f else ""
        if not line:
            time.sleep(0.05 if f else 2.0)
            continue
        j = line.find(LEVEL_TAG)
        if j >= 0:
            on_level(line[j + len(LEVEL_TAG):].strip())
            continue
        i = line.find("[TFSE] [")
        if i >= 0:
            on_batch(line[i + 7:].strip())


def log_folders() -> list[str]:
    out = []
    for g in find_games():
        out += [p.logs for p in profiles(g)]
    return out
