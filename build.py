"""Build the release for this package.

    python build.py

Produces:
  dist/<Bridge>.exe                 the bridge app
  dist/Install <Title>.exe          one-click installer (bridge + game mod inside)
  release/<Slug>-v<version>.zip     what goes on GitHub Releases / Nexus Mods

Needs: Python 3.10+ and PyInstaller (python -m pip install pyinstaller).
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
META = json.load(open(os.path.join(HERE, "package.json"), encoding="utf-8"))
SRC = os.path.join(HERE, "src")
DIST = os.path.join(HERE, "dist")
BUILD = os.path.join(HERE, "build")
RELEASE = os.path.join(HERE, "release")
SEP = ";" if os.name == "nt" else ":"


def pyinstaller(script, name, extra=()):
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
           "--name", name, "--paths", SRC, "--distpath", DIST, "--workpath", BUILD,
           "--specpath", BUILD, *extra, os.path.join(SRC, script)]
    print(">", " ".join(cmd[3:]))
    subprocess.run(cmd, check=True)


def main():
    title, version, bridge = META["title"], META["version"], META["bridge_exe"]
    for d in (DIST, BUILD):
        shutil.rmtree(d, ignore_errors=True)

    pyinstaller(META["bridge_script"], os.path.splitext(bridge)[0])
    pyinstaller(META["installer_script"], f"Install {title}", [
        "--add-data", f"{os.path.join(DIST, bridge)}{SEP}payload",
        "--add-data", f"{os.path.join(HERE, 'game-mod')}{SEP}game-mod",
    ])

    os.makedirs(RELEASE, exist_ok=True)
    zpath = os.path.join(RELEASE, f"{META['slug']}-v{version}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(DIST, f"Install {title}.exe"), f"Install {title}.exe")
        for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md"):
            z.write(os.path.join(HERE, name), name)
    print("\nRelease:", zpath, f"({os.path.getsize(zpath) // 1024} KB)")


if __name__ == "__main__":
    main()
