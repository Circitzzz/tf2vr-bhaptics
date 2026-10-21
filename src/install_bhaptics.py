"""Installer for TF2VR bHaptics (built into "Install TF2VR bHaptics.exe")."""
import installer_core as core

PACKAGE = core.Package(
    key="bhaptics",
    title="TF2VR bHaptics",
    version="1.0.0",
    bridge_exe="TF2VR bHaptics.exe",
    shortcuts=[
        ("TF2VR bHaptics", "", "Titanfall 2 -> bHaptics vest and wrists"),
        ("TF2VR bHaptics - Test Suit", "--test", "Play every effect once, no game needed"),
    ],
    notes=[
        "",
        "How to play:",
        "  1. Start bHaptics Player and turn on your vest (and Tactosy wrists).",
        "  2. Start 'TF2VR bHaptics' (Desktop or Start Menu) and leave its window open.",
        "  3. Launch Titanfall 2 VR (or Northstar) as usual.",
        "",
        "Try 'TF2VR bHaptics - Test Suit' first to feel every effect.",
        "Run this installer again after a Northstar or TF2VR update (choose Install / repair).",
    ],
)

if __name__ == "__main__":
    core.main(PACKAGE)
