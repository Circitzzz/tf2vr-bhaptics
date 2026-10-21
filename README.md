# TF2VR bHaptics

Full-body bHaptics support for **Titanfall 2 VR** (the CircuitLord TF2VR mod) and **Northstar**.
Feel every hit from the direction it came, weapon recoil, wallruns, double jumps, embarking into BT, and ejecting.

Works with the **TactSuit X40** vest and **Tactosy** wrist units through **bHaptics Player**.

> Unofficial fan mod. Not affiliated with or endorsed by bHaptics, Respawn, EA, or the TF2VR / Northstar authors.

## Install

1. Install and launch **Titanfall 2 VR (TF2VR)** or **Northstar** at least once.
2. Download `TF2VR-bHaptics-vX.Y.Z.zip` from [Releases](../../releases), unzip it, and run **`Install TF2VR bHaptics.exe`**.
3. Choose **1. Install / repair**. The installer:
   - finds Titanfall 2 in your Steam libraries (or asks for the folder),
   - installs the `TF2VR.SensoryEvents` game mod into every Northstar profile it finds and enables it,
   - adds the `-allowlocalhttp` launch option the mod needs,
   - installs the bridge app to `%LOCALAPPDATA%\TF2VR Sensory\bhaptics` with Start Menu + Desktop shortcuts.

Run the installer again after a TF2VR or Northstar update (Install / repair). It regenerates the damage hook from your current Northstar files.

## Play

1. Start **bHaptics Player** and turn on your vest and wrists. Turn the vest's strength up in the Player (we use 200%).
2. Start **TF2VR bHaptics** and leave its window open. It shows up in bHaptics Player as *Titanfall 2*.
3. Launch the game as usual.

Run **TF2VR bHaptics - Test Suit** first to feel every effect without the game.

## Effects

| Category | Effects |
|---|---|
| Damage | Directional bullet hits (front and back, left and right, by height), headshot, shotgun spread, melee, explosions (shockwave bleeds through to the other side, plus wrists), electric/arc, thermite burn, fall damage |
| Weapons | Recoil per class (pistol, heavy pistol, SMG, rifle, LMG, shotgun, sniper/Kraber, launchers) on the gun shoulder and gun wrist; reload ticks |
| Movement | Jump, double jump (jump-kit thrust on the lower back), wall jump, landing (scaled by speed), wallrun patter, slide rumble |
| Health | Low-health heartbeat that speeds up as health drops; respawn sweep; death fade |
| Titan | Embark sequence (lift, hatch slam, systems scan), footsteps, titan weapons, hull hits, shield fizz, dash shove, disembark, **eject** |

## Settings

`%LOCALAPPDATA%\TF2VR Sensory\bhaptics\config.json` (created on first run):

| Key | Default | Meaning |
|---|---|---|
| `master` | `1.0` | Overall strength, 0.0 to 1.0 |
| `floor` | `60` | Minimum strength of any effect (vests barely register below about 45) |
| `wrists` | `true` | Use the Tactosy wrist units |
| `gun_hand` | `"R"` | `R` or `L`: recoil shoulder and wrist |
| `log_events` | `false` | Write game events to `events.log` for tuning |
| `port` | `47770` | Local port the game mod sends to |

## Works with TF2VR Omara

The same game mod also feeds [TF2VR Omara](https://github.com/Circitzzz/tf2vr-omara) (scent). Install both, run both bridges, and they work side by side.

## How it works

```
Titanfall 2 + Northstar
  TF2VR.SensoryEvents (client script, read-only)
    hits, weapons, movement, titan state -> HTTP POST 127.0.0.1:47770
          |
TF2VR bHaptics bridge -> bHaptics Player (ws://127.0.0.1:15881) -> vest + wrists
```

The mod only reads game state and never changes gameplay. Directional hits come from a two-line hook in Northstar's `cl_damage_indicator.gnut`. The installer generates it from your own copy of that file.

## Troubleshooting

- **No haptics in game:** the bridge window should list *game mod installed (hooks 2/2, -allowlocalhttp on)*. If not, run the installer again.
- **"bHaptics Player is not reachable":** start bHaptics Player; the bridge reconnects on its own.
- **Too weak or too strong:** raise the vest strength in bHaptics Player, or change `master` / `floor`.

### "Windows protected your PC" or an antivirus warning

The installer and bridge are unsigned .exe files built with PyInstaller, so Windows SmartScreen may warn the first time.
Click **More info > Run anyway**. Some antivirus tools also flag PyInstaller apps by mistake. The full source is in this repo,
and you can build the files yourself (see *Build from source*).

## Build from source

```bash
python -m pip install pyinstaller
python build.py
```
The release zip ends up in `release/`. `src/sensory_common.py`, `src/installer_core.py` and `game-mod/` are shared with TF2VR Omara.

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
