import logging
import os
import sys
import json
from pathlib import Path

from src.scanner.mod_scanner import scan_mods
from src.scanner.xml_analyzer import analyze_xml
from src.logic.classifier import classify_mod
from src.logic.conflict_detector import detect_conflicts
from src.logic.redundancy_detector import detect_redundancy

# Import version info for logging and metadata
try:
    from __version__ import (
        __version__,
        __title__,
        __description__,
        __company__,
    )
except ImportError:
    # Fallback if __version__ is not available
    __version__ = "1.0.0"
    __title__ = "7d2d-mod-manager"
    __description__ = "Mod manager and load-order optimizer for 7 Days to Die"
    __company__ = "7d2d-mod-tools"

# Default mods path (can be overridden by config.json or user directory selection)
MODS_PATH = None

# Use logging so output can be silenced when running the GUI or packaged
logger = logging.getLogger(__name__)
# Default to WARNING so informational reports don't appear in GUI/quiet runs
logging.basicConfig(level=logging.WARNING)

# Log startup info including version and company
logger.debug(f"{__title__} v{__version__} by {__company__}")


def _load_mods_path_from_config() -> str | None:
    """Load mods path from config.json in the app directory."""
    try:
        cfg_path = Path(__file__).with_name("config.json")
        if cfg_path.exists():
            with cfg_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
                if isinstance(cfg, dict) and "mods_path" in cfg:
                    return str(cfg["mods_path"])
    except Exception:
        pass
    return None


def cli_main(mods_path: str | None = None) -> int:
    """Run CLI analysis of mods in the specified directory.

    Args:
        mods_path: Path to mods directory. If None, attempts to load from config.json

    Returns:
        Exit code (0 for success)
    """
    if not mods_path:
        mods_path = _load_mods_path_from_config()

    if not mods_path:
        logger.error("No mods path specified. Set mods_path in config.json or provide as argument.")
        return 1
    mods = scan_mods(mods_path)

    # Analyze each mod
    for mod in mods:
        analyze_xml(mod)
        classify_mod(mod)

    # Cross-mod analysis
    detect_conflicts(mods)
    detect_redundancy(mods)

    # Build and emit report via logging (info-level so it can be silenced)
    for mod in mods:
        logger.info(f"\n=== {mod.name} ===")

        if mod.is_overhaul:
            logger.info("Type: OVERHAUL")

        if mod.systems:
            logger.info("Systems: %s", ", ".join(sorted(mod.systems)))

        for c in mod.conflicts:
            if isinstance(c, dict):
                other = c.get("with") or c.get("mod") or c.get("name")
                logger.info(f"⚠ Conflict with: {other}")
                if c.get("file"):
                    logger.info("   File: %s", c.get("file"))
                if c.get("reason"):
                    logger.info("   Reason: %s", c.get("reason"))
                if c.get("suggestion"):
                    logger.info("   Suggestion: %s", c.get("suggestion"))
            else:
                logger.info("⚠ Conflict: %s", c)

        if mod.redundant_reason:
            logger.info("♻ Redundant: %s", mod.redundant_reason)

    return 0


def gui_main() -> int:
    """Launch the Python (Tkinter) GUI."""
    try:
        import tkinter as tk
        from gui.app import ModAnalyzerApp
    except Exception as ex:
        logger.error("Failed to launch Tkinter GUI: %s", ex)
        return 1

    root = tk.Tk()
    ModAnalyzerApp(root)
    root.mainloop()
    return 0


def main(argv=None) -> int:
    """Launch the Python (Tkinter) GUI or run CLI analysis.

    The WinForms integration has been removed to ensure:
    - No unnecessary file system access outside user-selected directories
    - Transparent operation with no external process spawning
    - No dependency on dotnet or other external tools
    - Reduced antivirus false positives
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    # Keep the old console pipeline available, but default to GUI.
    if "--cli" in argv:
        argv = [a for a in argv if a != "--cli"]
        mods_path = argv[0] if argv else None
        return cli_main(mods_path)
    else:
        return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
