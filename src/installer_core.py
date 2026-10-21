"""Console installer shared by the TF2VR bHaptics and TF2VR Omara packages.

    Install <Package>.exe              interactive: install / repair / uninstall / status
    Install <Package>.exe --install    install without questions
    Install <Package>.exe --uninstall  remove this package
    Install <Package>.exe --game PATH  use this Titanfall 2 folder
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import sensory_common as sc


class Package:
    def __init__(self, key: str, title: str, version: str, bridge_exe: str,
                 shortcuts: list[tuple[str, str, str]], notes: list[str]):
        self.key = key                  # "bhaptics" / "omara"
        self.title = title              # "TF2VR bHaptics"
        self.version = version
        self.bridge_exe = bridge_exe    # file name inside payload/
        self.shortcuts = shortcuts      # (name, args, description) for the bridge exe
        self.notes = notes              # printed after a successful install


def _pause():
    if sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass


def _pick_game(argv: list[str]) -> str | None:
    if "--game" in argv:
        i = argv.index("--game")
        if i + 1 < len(argv):
            return argv[i + 1]
    games = sc.find_games()
    if len(games) == 1:
        return games[0]
    if games:
        print("Several Titanfall 2 installs were found:")
        for n, g in enumerate(games, 1):
            print(f"  {n}. {g}")
        try:
            choice = input(f"Which one? [1-{len(games)}] ").strip() or "1"
            return games[int(choice) - 1]
        except (ValueError, IndexError, EOFError):
            return games[0]
    print("Titanfall 2 was not found automatically.")
    try:
        path = input("Paste the Titanfall 2 folder (the one with Titanfall2.exe): ").strip().strip('"')
    except EOFError:
        return None
    return path or None


def _stop_bridge(pkg: Package):
    subprocess.run(["taskkill", "/IM", pkg.bridge_exe, "/F"], capture_output=True)
    time.sleep(0.5)


def install(pkg: Package, argv: list[str]) -> bool:
    print(f"== Installing {pkg.title} {pkg.version} ==\n")
    game = _pick_game(argv)
    if not game or not os.path.isfile(os.path.join(game, "Titanfall2.exe")):
        print("No valid Titanfall 2 folder - nothing was changed.")
        return False
    print("Titanfall 2:", game)
    profs = sc.profiles(game)
    if not profs:
        print("\nNorthstar was not found in this install.\n"
              "Install the Titanfall 2 VR mod (TF2VR) or Northstar first, start the game once, then run this again.")
        return False

    print("\nGame mod:")
    for p in profs:
        try:
            sc.install_feed(p)
        except Exception as e:
            print(f"  {p.label}: FAILED - {e}")
            return False

    print("\nApp:")
    dest = sc.app_dir(pkg.key)
    os.makedirs(dest, exist_ok=True)
    _stop_bridge(pkg)
    bridge = os.path.join(dest, pkg.bridge_exe)
    shutil.copy2(os.path.join(sc.resource_dir(), "payload", pkg.bridge_exe), bridge)
    uninstaller = os.path.join(dest, f"Uninstall {pkg.title}.exe")
    if getattr(sys, "frozen", False):
        if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(uninstaller):
            shutil.copy2(sys.executable, uninstaller)
    print("  installed to", dest)

    menu = sc.start_menu_dir()
    for name, args, desc in pkg.shortcuts:
        sc.make_shortcut(os.path.join(menu, name + ".lnk"), bridge, args, dest, desc)
    if os.path.isfile(uninstaller):
        sc.make_shortcut(os.path.join(menu, f"Uninstall {pkg.title}.lnk"), uninstaller,
                         "--uninstall", dest, f"Remove {pkg.title}")
    main_name, main_args, main_desc = pkg.shortcuts[0]
    sc.make_shortcut(os.path.join(sc.desktop_dir(), main_name + ".lnk"), bridge, main_args, dest, main_desc)
    print("  shortcuts: Start Menu > TF2VR Sensory, and Desktop")

    sc.register(pkg.key, {"version": pkg.version, "game": game, "app": dest,
                          "profiles": [p.name for p in profs]})
    print(f"\n{pkg.title} is installed.")
    for line in pkg.notes:
        print(line)
    return True


def uninstall(pkg: Package) -> bool:
    print(f"== Removing {pkg.title} ==\n")
    info = sc.registry().get(pkg.key, {})
    _stop_bridge(pkg)
    menu = sc.start_menu_dir()
    for name, _, _ in pkg.shortcuts:
        for folder in (menu, sc.desktop_dir()):
            try:
                os.remove(os.path.join(folder, name + ".lnk"))
            except OSError:
                pass
    try:
        os.remove(os.path.join(menu, f"Uninstall {pkg.title}.lnk"))
    except OSError:
        pass
    if os.path.isdir(menu) and not os.listdir(menu):
        os.rmdir(menu)

    remaining = sc.unregister(pkg.key)
    if remaining:
        print(f"  keeping the {sc.FEED_MOD} game mod - still used by: {', '.join(remaining)}")
    else:
        games = [info["game"]] if info.get("game") else sc.find_games()
        for g in games:
            for p in sc.profiles(g):
                sc.remove_feed(p)

    dest = sc.app_dir(pkg.key)
    running_from_dest = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable))) == os.path.normcase(dest)
    if os.path.isdir(dest):
        if running_from_dest:
            # Can't delete our own exe while it runs: clean up the rest now and
            # leave a one-shot command to remove the folder after we exit.
            for name in os.listdir(dest):
                path = os.path.join(dest, name)
                if os.path.normcase(path) != os.path.normcase(os.path.abspath(sys.executable)):
                    shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else _try_remove(path)
            subprocess.Popen(f'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "{dest}"',
                             creationflags=0x08000000, close_fds=True)
        else:
            shutil.rmtree(dest, ignore_errors=True)
    print(f"\n{pkg.title} was removed.")
    return True


def _try_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def status(pkg: Package):
    print(f"== {pkg.title} status ==\n")
    reg = sc.registry()
    print("Installed packages:", ", ".join(f"{k} {v.get('version')}" for k, v in reg.items()) or "none")
    for g in sc.find_games():
        print("\nTitanfall 2:", g)
        for p in sc.profiles(g):
            print(f"  {p.label}: {sc.feed_status(p)}")


def main(pkg: Package, argv: list[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if "--uninstall" in argv:
            ok = uninstall(pkg)
        elif "--install" in argv:
            ok = install(pkg, argv)
        elif "--status" in argv:
            status(pkg)
            ok = True
        else:
            print(f"{pkg.title} {pkg.version}\n")
            print("  1. Install / repair (also use this after a game or mod update)")
            print("  2. Uninstall")
            print("  3. Status")
            try:
                choice = input("\nChoose [1]: ").strip() or "1"
            except EOFError:
                choice = "1"
            print()
            if choice == "2":
                ok = uninstall(pkg)
            elif choice == "3":
                status(pkg)
                ok = True
            else:
                ok = install(pkg, argv)
    except Exception as e:
        print(f"\nSomething went wrong: {e}")
        ok = False
    _pause()
    sys.exit(0 if ok else 1)
