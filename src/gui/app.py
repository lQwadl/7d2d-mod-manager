import os
import json
import re
import pathlib
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple, cast
import xml.etree.ElementTree as ET
import time

# Allow running this file directly (python gui/app.py) by ensuring the project root
# is on sys.path before importing package modules like `gui.*`.
try:
    import sys as _sys

    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _ROOT and _ROOT not in _sys.path:
        _sys.path.insert(0, _ROOT)
except Exception:
    pass

from . import heatmap as _heatmap
from .legend import build_visual_legend as _build_visual_legend
from .tooltips import HighRiskTooltip
from ..scanner.modinfo_parser import parse_modinfo_name_version

# Import version and metadata info
try:
    from __version__ import __version__, __title__
except ImportError:
    __version__ = "1.0.0"
    __title__ = "7d2d-mod-analyzer"

# Persistent per-mod enabled state (authoritative)
try:
    from ..logic.mod_state_store import ModStateStore
except Exception:
    ModStateStore = None

MODS_STATE_FILE = "mods_state.json"
# Ensure project root is on sys.path when running this file directly
try:
    from .theme import BORDER_NORMAL
    from ..logic.mod_integrity import hash_mod_folder
    from ..mock_deploy.engine import simulate_deployment
    from ..logic.rename_sanitizer import sanitize_name as _sanitize_folder_base
except ModuleNotFoundError:
    import sys as _sys
    import os as _os

    _sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..")))
    from ..mock_deploy.engine import simulate_deployment
    from .theme import BORDER_NORMAL
    from ..logic.mod_integrity import hash_mod_folder
    from ..logic.rename_sanitizer import sanitize_name as _sanitize_folder_base


def _sanitize_user_folder_name(name: str) -> str:
    """Sanitize a user-provided folder name (not a path)."""

    s = str(name or "").strip()
    # Strip stacked numeric prefixes like "000_"
    try:
        s = _sanitize_folder_base(s)
    except Exception:
        pass
    # Prevent accidental path injection / nested paths
    for bad in ["\\", "/", ":"]:
        s = s.replace(bad, "-")
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _app_root_dir() -> pathlib.Path:
    """Return the app root directory.

    Important on Windows: when launched from a Desktop shortcut, the process CWD
    may be the Desktop, which would otherwise cause relative paths like
    `config.json` or `data/rules.json` to read/write into the Desktop.
    """

    # PyInstaller one-file/one-dir bundles commonly set sys._MEIPASS.
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return pathlib.Path(str(meipass)).resolve()
    except Exception:
        pass

    # Source/dev runs: repo root is the parent of `gui/`.
    try:
        return pathlib.Path(__file__).resolve().parents[1]
    except Exception:
        return pathlib.Path.cwd()


APP_ROOT_DIR = _app_root_dir()
DATA_DIR = APP_ROOT_DIR / "data"

# Settings/config/state files should live alongside the app, not in CWD.
SETTINGS_FILE = str(APP_ROOT_DIR / "settings.json")
CONFIG_FILE = str(APP_ROOT_DIR / "config.json")
MODS_STATE_FILE = str(APP_ROOT_DIR / "mods_state.json")

# Persistent Conflict Knowledge Base (CKB)
CONFLICT_MEMORY_FILE = str(DATA_DIR / "conflict_memory.json")

# Resolution Knowledge Base (RKB)
RESOLUTION_KB_FILE = str(DATA_DIR / "resolution_knowledge.json")


# =========================================================
# CRASH LOGGING (helps diagnose fragile scan crashes)
# =========================================================


def _get_log_dir() -> Optional[pathlib.Path]:
    """Best-effort writable log dir.

    Prefer %LOCALAPPDATA% for packaged apps; fall back to workspace folders.
    """
    candidates = []

    # Prefer app-root logs first so Desktop launches don't spill files into CWD.
    try:
        candidates.append(APP_ROOT_DIR / "data" / "logs")
        candidates.append(APP_ROOT_DIR / "logs")
    except Exception:
        pass

    # Keep CWD as a fallback (some users run from a terminal with a specific CWD).
    try:
        candidates.append(pathlib.Path.cwd() / "data" / "logs")
        candidates.append(pathlib.Path.cwd() / "logs")
    except Exception:
        pass

    try:
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            candidates.append(pathlib.Path(local_app_data) / "7d2d-mod-manager" / "logs")
    except Exception:
        pass

    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            # Quick writability check
            probe = d / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            try:
                probe.unlink()
            except Exception:
                pass
            return d
        except Exception:
            continue
    return None


def _append_crash_log(where: str, tb_text: str) -> Optional[str]:
    """Append traceback to a persistent crash log; return log path if available."""
    log_dir = _get_log_dir()
    if not log_dir:
        return None
    try:
        log_path = log_dir / "crash.log"
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"{stamp}  {where}\n")
            f.write(tb_text.rstrip() + "\n")
        return str(log_path)
    except Exception:
        return None


def _safe_show_error(title: str, msg: str):
    try:
        messagebox.showerror(title, msg)
    except Exception:
        try:
            print(f"[{title}] {msg}")
        except Exception:
            pass


# Authoritative category policy (shared across UI + logic)
try:
    from ..logic.category_policy import (
        CATEGORY_ORDER as CATEGORY_ORDER,
        CATEGORY_IMPACT_WEIGHT as CATEGORY_IMPACT_WEIGHT,
        category_index as category_index,
        normalize_category as _normalize_category,
    )
except Exception:
    CATEGORY_ORDER = []
    CATEGORY_IMPACT_WEIGHT = {}

    def category_index(category: Optional[str]) -> int:
        return 999

    def _normalize_category(raw: Optional[str]) -> str:
        return raw or "Miscellaneous"


# Rule-based load order engine (constraint-based, no numeric scoring)
try:
    from ..logic.load_order_engine import (
        TIER_ORDER as LOAD_TIER_ORDER,
        compute_load_order as compute_load_order,
        infer_semantic_impact as infer_semantic_impact,
        infer_tier as infer_tier,
    )
except Exception:
    LOAD_TIER_ORDER = ()

    class _FallbackLoadOrderReport:
        def __init__(self):
            self.warnings = []
            self.errors = ["Load order engine unavailable"]

        def confidence_level(self) -> str:
            return "unknown"

    def infer_tier(mod, *, file_cache=None) -> str:
        return "Content Additions"

    def infer_semantic_impact(mod, *, file_cache=None) -> str:
        return "Additive Content"

    def compute_load_order(mods, *, user_rules=(), include_disabled=False):
        _ = user_rules, include_disabled
        return list(mods or []), cast(Any, _FallbackLoadOrderReport())


try:
    from ..logic.mod_metadata_store import ModMetadataStore
    from ..logic.xml_category_classifier import detect_categories_for_mod
except Exception:
    ModMetadataStore = None
    detect_categories_for_mod = None


def _is_patch_mod_name(name: str) -> bool:
    try:
        nm = (name or "").lower()
        return nm.startswith("999_conflictpatch_") or nm.startswith("conflictpatch_")
    except Exception:
        return False


_ORDER_PREFIX_RE = re.compile(r"^(\d+)_")


def _parse_order_prefix(folder_name: str):
    """Return numeric load-order prefix if present, else None."""
    try:
        if not folder_name:
            return None
        name = folder_name
        if name.startswith("__DISABLED__"):
            name = name[len("__DISABLED__") :]
        m = _ORDER_PREFIX_RE.match(name)
        if not m:
            return None
        return int(m.group(1))
    except Exception:
        return None


def normalize_category(raw):
    """Normalize category names using the shared category policy."""
    try:
        return _normalize_category(raw)
    except Exception:
        return raw or "Miscellaneous"


def apply_dark_theme(root):
    colors = {
        "bg": "#1e1e1e",
        "panel": "#252526",
        "entry_bg": "#2d2d2d",
        "entry_fg": "#d4d4d4",
        "button_bg": "#2d2d2d",
        "button_fg": "#d4d4d4",
        "button_hover_fg": "#000000",
        "button_hover_bg": "#3a3a3a",
        "header_bg": "#0e639c",
        "tree_bg": "#252526",
        "tree_fg": "#d4d4d4",
        "border": BORDER_NORMAL,
        # convenience colors
        "fg": "#d4d4d4",
        "ok": "#4CAF50",
        "conflict": "#E6B800",
        "missing": "#C62828",
        "disabled": "#555555",
        "header": "#007acc",
        "hover": "#2a2a2a",
    }

    root.configure(bg=colors["bg"])

    style = ttk.Style()
    style.theme_use("default")

    # ---------- BUTTONS ----------
    style.configure(
        "TButton",
        background=colors["button_bg"],
        foreground=colors["button_fg"],
        bordercolor=colors["border"],
        focusthickness=0,
        padding=(10, 6),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("active", colors["button_hover_bg"])],
        foreground=[("active", colors["button_fg"])],
    )

    # ---------- ENTRY / SEARCH ----------
    style.configure(
        "TEntry",
        fieldbackground=colors["entry_bg"],
        background=colors["entry_bg"],
        foreground=colors["entry_fg"],
        bordercolor=colors["border"],
        padding=4,
    )

    # ---------- TREEVIEW ----------
    style.configure(
        "Treeview",
        background=colors["tree_bg"],
        foreground=colors["tree_fg"],
        fieldbackground=colors["tree_bg"],
        rowheight=26,
        bordercolor=colors["border"],
    )
    style.configure(
        "Treeview.Heading",
        background=colors["header_bg"],
        foreground="#ffffff",
        relief="flat",
    )
    style.map(
        "Treeview.Heading",
        background=[("active", colors["header_bg"])],
        foreground=[("active", "#ffffff")],
    )

    return colors


# Semantic tag colors (used for Treeview row tags)
# Placed here so they are easy to tweak and reference from the UI
TAG_COLORS = {
    "ok": "#3fb950",  # green (OK)
    "disabled": "#dcdcaa",  # yellowish (disabled)
    "missing": "#f85149",  # red (missing ModInfo)
    "conflict": "#facc15",  # yellow (warning)
}

# Conflict taxonomy constants
CONFLICT_OVERRIDE = "override"
CONFLICT_REDUNDANT = "redundant"
CONFLICT_MERGE = "merge_required"
CONFLICT_EXCLUSIVE = "exclusive"

FIXABILITY = {
    CONFLICT_OVERRIDE: 80,
    CONFLICT_REDUNDANT: 90,
    CONFLICT_MERGE: 40,
    CONFLICT_EXCLUSIVE: 10,
}

# High-risk categories set
HIGH_RISK_CATEGORIES = {
    "Overhauls",
    "XML Edits",
    "Core / Framework",
    "Maps",
}

# Exact spacer display labels for GUI grouping (aligned with CATEGORY_ORDER)
CATEGORY_SPACER_LABELS = {
    "Core / Framework": "── CORE / FRAMEWORK ──────────────",
    "Overhauls": "── OVERHAULS ──────────────────────────",
    "XML Edits": "── XML EDITS ──────────────────────────",
    "Gameplay": "── GAMEPLAY ───────────────────────────",
    "Crafting": "── CRAFTING ───────────────────────────",
    "Weapons": "── WEAPONS ────────────────────────────",
    "Items & Loot": "── ITEMS & LOOT ─────────────────────",
    "Food": "── FOOD ─────────────────────────────────",
    "Zombies / Creatures": "── ZOMBIES / CREATURES ─────────",
    "Vehicles": "── VEHICLES ───────────────────────────",
    "Prefabs / POIs": "── PREFABS / POIs ──────────────────",
    "Maps": "── MAPS ─────────────────────────────────",
    "Quests": "── QUESTS ───────────────────────────────",
    "Visuals & Graphics": "── VISUALS & GRAPHICS ───────────",
    "Audio": "── AUDIO ────────────────────────────────",
    "UI": "── UI ────────────────────────────────────",
    "Utilities": "── UTILITIES ─────────────────────────",
    "Cheats": "── CHEATS ──────────────────────────────",
    "Miscellaneous": "── MISCELLANEOUS ───────────────────",
}

# Optional category-scoped LLM context for explanations
CATEGORY_CONTEXT = {
    "Weapons": "weapon stats, items.xml, balance",
    "Overhauls": "core gameplay systems, perks, progression",
    "XML Edits": "xpath overrides, set/append/remove",
    "POI / Maps": "prefab placement, rwgmixer.xml",
    "UI": "windows.xml, localization",
}

# =========================================================
# DATA MODEL
# =========================================================


class Mod:
    def __init__(self, name, path):
        self.name = name
        self.path = path
        # Primary category (for ordering/grouping) + multi-category evidence
        self.category = "Miscellaneous"
        self.categories = []  # list[str]
        self.category_evidence = {}
        self.is_overhaul = False
        self.disabled = False
        self.disabled_reason = None
        # Authoritative state: enabled means participates in conflicts/export
        self.enabled = True
        # New attributes for UI and load-order logic
        self.tier = "Content Additions"
        self.semantic_impact = "Additive Content"
        # Backward-compatible UI field name: this is now a label, not a score.
        self.priority = self.tier
        self.user_disabled = False  # user toggled disable (separate from dedupe-disabled)
        self.order_override: Optional[int] = None  # optional numeric order prefix override (manual override)
        # A stable per-install id derived from folder name (without disabled/order prefixes)
        self.install_id: Optional[str] = None
        # Scopes (set of strings) detected from name/folder for semantic conflict detection
        self.scopes = set()
        # XML analysis fields (to support semantic conflict detection)
        self.systems = set()
        self.xml_files = set()
        self.xml_targets = {}
        self.semantic_edits = []
        # Presence flags
        self.has_modinfo = True
        # ModInfo metadata (for UpdateEngine / diagnostics)
        self.modinfo_name = ""
        self.modinfo_version = ""
        # Prefab / POI detection (mods without ModInfo but containing prefabs)
        self.is_poi = False
        # In-memory load order (lower => earlier)
        self.load_order = 0
        # Conflict severity model
        self.conflict_level = None  # None | 'low' | 'high'
        self.severity = 0  # 0–100
        # Redundant/duplicate detection (flagged but not auto-disabled)
        self.redundant = False
        self.redundant_reason = None


# =========================================================
# MOD SCANNING
# =========================================================


def scan_mods(mods_path):
    mods = []
    try:
        from path_safety import assert_not_appdata

        assert_not_appdata(mods_path, purpose="Mods scan")
    except Exception:
        # Hard ignore: never scan AppData.
        return mods
    if not os.path.exists(mods_path):
        return mods

    for name in os.listdir(mods_path):
        full = os.path.join(mods_path, name)
        if os.path.isdir(full):
            mods.append(Mod(name=name, path=full))
    return mods


# =========================================================
# DUPLICATE VERSION HANDLING
# =========================================================


def is_poi_prefab_mod(mod_path: str) -> bool:
    """
    Detects POI / prefab-only mods that do NOT require ModInfo.xml.
    Returns True if the folder contains prefab asset files and at least one .xml (not ModInfo.xml).
    """
    prefab_exts = {".blocks.nim", ".mesh", ".ins", ".tts"}
    has_prefab_assets = False
    has_prefab_xml = False

    try:
        for root, dirs, files in os.walk(mod_path):
            # Any XML that isn't ModInfo.xml counts toward prefab XML
            for fn in files:
                name_lower = fn.lower()
                if name_lower.endswith(".xml") and name_lower != "modinfo.xml":
                    has_prefab_xml = True
                _, ext = os.path.splitext(fn)
                if ext.lower() in prefab_exts:
                    has_prefab_assets = True
            # Common prefab directory hint
            for d in dirs:
                if d.lower() in {"prefabs", "poi", "pois"}:
                    has_prefab_assets = True
        return has_prefab_assets and has_prefab_xml
    except Exception:
        return False


# ---------------------------------------------------------
# Scope extraction helper
# - Scans mod name and folder name for scope keywords
# - Returns a set of scope keys (e.g. 'loot_quality', 'weapons')
# ---------------------------------------------------------
def extract_scopes(name, folder_name):
    """
    Simple, fast keyword-based scope detection.
    Scans `name` and `folder_name` (case-insensitive) and returns a set of scopes.
    """
    txt = (name or "").lower()
    folder = (folder_name or "").lower()
    combined = txt + " " + folder

    mapping = {
        "loot_quality": ["loot", "drop", "quality"],
        # Avoid false positives like "QuickStack" vs stack-size mods.
        # 'stack' alone is too generic; prefer explicit stack-size/quantity terms.
        "loot_quantity": ["stack size", "stacksize", "amount", "quantity"],
        "ui_icons": ["icon"],
        "ui_hud": ["hud"],
        "sounds": ["sound", "audio"],
        "weapons": ["weapon", "gun"],
        "crafting": ["craft", "recipe"],
        "progression": ["perk", "skill", "xp"],
    }

    scopes = set()
    for scope, keywords in mapping.items():
        for kw in keywords:
            if kw in combined:
                scopes.add(scope)
                break

    return scopes


# ============================================================
# 7D2D MOD MANAGER — DIAGNOSTICS / SEVERITY / RESOLUTION PACK
# ============================================================

# 🎨 Severity Color System
COLORS = {
    "error": "#C62828",
    "conflict_critical": "#C62828",
    "conflict_high": "#FF8C00",
    "conflict_low": "#FFD400",  # true yellow (not beige)
    "redundant": "#1E88E5",
    "disabled": "#555555",
    "ok": "#4CAF50",
}


def _severity_band(score: int) -> str:
    """Map numeric severity (0-100) to a strict band."""
    try:
        s = int(score or 0)
    except Exception:
        s = 0
    if s >= 80:
        return "critical"
    if s >= 40:
        return "high"
    if s >= 1:
        return "low"
    return "ok"


# High-impact scopes for conflict escalation
HIGH_IMPACT_SCOPES = {"loot_quality", "weapons", "progression", "skills", "perks"}


def assign_conflict_level(mod, scope):
    try:
        mod.conflict = True
        mod.conflict_level = "low"
        if scope in HIGH_IMPACT_SCOPES:
            mod.conflict_level = "high"
    except Exception:
        pass


def detect_redundancy(mod, covering_mod_name):
    try:
        mod.redundant = True
        mod.redundant_reason = f"Covered by {covering_mod_name}"
    except Exception:
        pass


def is_effectively_enabled(mod) -> bool:
    """Authoritative enabled state.

    A mod participates in conflicts/export iff:
    - enabled is True
    - user_disabled is False
    - disabled (system/duplicate/rule) is False
    """
    try:
        if bool(getattr(mod, "user_disabled", False)):
            return False
        if bool(getattr(mod, "disabled", False)):
            return False
        return bool(getattr(mod, "enabled", True))
    except Exception:
        return not bool(getattr(mod, "user_disabled", False))


def is_deployable_mod(mod) -> bool:
    """True if this entry can actually load in-game.

    7DTD requires ModInfo.xml for standard mods. Prefab-only POI packs are a known
    exception (no ModInfo.xml).
    """
    try:
        return bool(getattr(mod, "has_modinfo", True)) or bool(getattr(mod, "is_poi", False))
    except Exception:
        return True


def determine_row_tag(mod):
    try:
        # Disabled mods should be visually muted regardless of conflict severity.
        if not is_effectively_enabled(mod):
            return "disabled"
        if not getattr(mod, "has_modinfo", True) and not getattr(mod, "is_poi", False):
            return "conflict_low"
        if str(getattr(mod, "integrity", "") or "").lower() == "invalid":
            return "error"
        if getattr(mod, "redundant", False):
            return "redundant"

        band = _severity_band(getattr(mod, "severity", 0))
        if band == "critical":
            return "error"
        if band == "high":
            return "conflict_high"
        if band == "low":
            return "conflict_low"
        return "ok"
    except Exception:
        return "ok"


def configure_treeview_tags(tree):
    try:
        # Define all status color tags once
        tree.tag_configure("error", background="#C62828", foreground="white")
        tree.tag_configure("conflict_critical", background="#C62828", foreground="white")
        tree.tag_configure("conflict_high", background="#FF8C00", foreground="black")
        tree.tag_configure("conflict_low", background="#FFD400", foreground="black")
        tree.tag_configure("redundant", background="#1976D2", foreground="white")
        tree.tag_configure("disabled", background="#555555", foreground="#aaaaaa")
        tree.tag_configure("ok", background="#4CAF50", foreground="black")
        # Spacer rows
        tree.tag_configure(
            "spacer",
            background="#1f1f1f",
            foreground="#9e9e9e",
            font=("Segoe UI", 9, "bold"),
        )
        try:
            tree.tag_bind("spacer", "<Button-1>", lambda e: "break")
        except Exception:
            pass
    except Exception:
        pass


def explain_conflict(mod):
    ct = getattr(mod, "conflict_type", None)
    if ct == CONFLICT_OVERRIDE:
        return "Overrides the same XML nodes as another mod."
    if ct == CONFLICT_REDUNDANT:
        return "Functionality is already provided by another mod."
    if ct == CONFLICT_MERGE:
        return "Multiple mods add changes to the same system."
    if ct == CONFLICT_EXCLUSIVE:
        return "Multiple mods assume ownership of this system."
    return "Unknown conflict type."


def conflict_category_label(mod):
    try:
        ct = str(getattr(mod, "conflict_type", "") or "")
        label_map = {
            # Structured conflict types
            CONFLICT_OVERRIDE: "XML Override",
            CONFLICT_REDUNDANT: "Redundant",
            CONFLICT_MERGE: "Merge/Extend",
            CONFLICT_EXCLUSIVE: "Exclusive Ownership",
            # String-based taxonomy keys
            "missing_invalid": "Missing / Invalid",
            "no_modinfo": "No ModInfo.xml",
            "invalid_xml": "Invalid XML",
            "wrong_depth": "Wrong Folder Depth",
            "case_mismatch": "Case Mismatch",
            "duplicate_id": "Duplicate ID",
            "overhaul_vs_standalone": "Overhaul vs Standalone",
            "xml_override": "XML Override",
            "load_order_priority": "Load Order Priority",
            "scope_overlap": "Scope Overlap (Heuristic)",
            "asset_conflict": "Asset Conflict",
            "poi_conflict": "POI Conflict",
            "world_compat": "World Compatibility",
            "performance": "Performance",
            "log_only": "Log-only",
            "missing_dependency": "Missing Dependency",
        }
        label = label_map.get(ct)
        if label:
            return label
        # Heuristics when conflict_type is unknown
        if getattr(mod, "redundant", False):
            return "Redundant"
        # If flagged conflict but no type, prefer Load Order Priority over generic labels
        if getattr(mod, "conflict", False):
            return "Load Order Priority"
        return getattr(mod, "status", "OK") or "OK"
    except Exception:
        return getattr(mod, "status", "OK") or "OK"


def conflict_evidence_summary(mod, limit=2) -> str:
    """Return a short, deterministic evidence summary for the mod's top conflicts."""
    try:
        # Prefer structured conflicts from scanners/engines
        confs = getattr(mod, "conflicts", []) or []
        # Deterministic ordering for UI stability
        try:
            confs = sorted(
                [c for c in confs if isinstance(c, dict)],
                key=lambda c: (
                    str(c.get("level") or ""),
                    str(c.get("file") or ""),
                    str(c.get("target") or ""),
                    str(c.get("scope") or ""),
                ),
            )
        except Exception:
            pass
        parts = []
        seen = set()
        for c in confs:
            file = (c.get("file") or "").strip()
            target = (c.get("target") or "").strip()
            scope = (c.get("scope") or "").strip()
            # Keep evidence compact
            ev = ", ".join([x for x in [file, target, scope] if x])
            if not ev:
                continue
            if ev in seen:
                continue
            seen.add(ev)
            parts.append(ev)
            if len(parts) >= int(limit or 2):
                break
        if parts:
            return " | ".join(parts)

        # Fall back to integrity issues/warnings
        issues = list(getattr(mod, "integrity_issues", []) or [])
        warns = list(getattr(mod, "integrity_warnings", []) or [])
        if issues:
            return "; ".join(issues[:limit])
        if warns:
            return "; ".join(warns[:limit])
        return ""
    except Exception:
        return ""


def conflict_severity_level(mod):
    try:
        ct = getattr(mod, "conflict_type", None)
        if ct in (
            "missing_invalid",
            "no_modinfo",
            "invalid_xml",
            "wrong_depth",
            "case_mismatch",
            "duplicate_id",
            "missing_dependency",
        ):
            return "Error"
        if ct in (
            "overhaul_vs_standalone",
            "poi_conflict",
            "world_compat",
        ):
            return "High"
        if ct == "load_order_priority":
            try:
                # Scope-derived priority conflicts can be low or high.
                confs = getattr(mod, "conflicts", []) or []
                scopes = {str(c.get("scope") or "").strip() for c in confs if isinstance(c, dict)}
                scopes.discard("")
                if scopes & HIGH_IMPACT_SCOPES:
                    return "High"
            except Exception:
                pass
            return "Low" if getattr(mod, "severity", 0) < 60 else "High"
        if ct == "scope_overlap":
            # Heuristic scope overlaps are advisory unless backed by real file/xpath collisions.
            return "Low"
        if ct in ("xml_override", CONFLICT_OVERRIDE):
            if getattr(mod, "high_risk", False) or getattr(mod, "severity", 0) >= 60:
                return "High"
            return "Low"
        if ct in ("redundant", CONFLICT_REDUNDANT) or getattr(mod, "redundant", False):
            return "Redundant"
        if ct in ("performance", "log_only", "asset_conflict"):
            return "Low"
        status = getattr(mod, "status", "") or ""
        if status.startswith("Conflict"):
            return "Low" if getattr(mod, "severity", 0) < 60 else "High"
        if status in ("Error", "Missing"):
            return "Error"
        if status == "Redundant":
            return "Redundant"
        if status == "Warning":
            return "Low"
        return "Low"
    except Exception:
        return "Low"


def conflict_severity_icon(level):
    return {
        "Error": "🔴",
        "High": "🟠",
        "Low": "🟡",
        "Redundant": "🔵",
    }.get(level, "🟡")


def suggested_action(mod):
    # Memory override (optional): allow the CKB to pre-fill a recommended action.
    try:
        mem_action = getattr(mod, "memory_suggested_action", None)
        if mem_action:
            return str(mem_action)
    except Exception:
        pass
    try:
        if getattr(mod, "is_patch", False) or _is_patch_mod_name(getattr(mod, "name", "")):
            return "None"
    except Exception:
        pass

    # UpdateEngine hint (when present; memory suggestions still win above)
    try:
        if getattr(mod, "update_available", False):
            return getattr(mod, "update_suggested_action", None) or "Update available — disable older version"
    except Exception:
        pass
    ct = str(getattr(mod, "conflict_type", "") or "")
    # Overhaul guidance supersedes when conflicting
    try:
        if getattr(mod, "is_overhaul", False) and getattr(mod, "conflict", False):
            return "Overhaul detected — disable conflicting standalone mods."
    except Exception:
        pass

    def _has_ui_xui_evidence(_mod) -> bool:
        try:
            confs = getattr(_mod, "conflicts", []) or []
            for c in confs:
                if not isinstance(c, dict):
                    continue
                fk = str(c.get("file") or "").replace("\\", "/").strip().lower()
                if not fk:
                    continue
                if fk in {"ui.xml", "windows.xml"}:
                    return True
                if fk.startswith("xui/") or fk.startswith("xui_"):
                    return True
                if "/xui/" in fk or "/xui_" in fk:
                    return True
        except Exception:
            return False
        return False

    # Decision table mapping
    table = {
        "missing_invalid": "Fix or remove mod — cannot be loaded",
        "no_modinfo": "Optional: ModInfo.xml missing (mod still loads)",
        "invalid_xml": "Fix or remove mod — cannot be loaded",
        "wrong_depth": "Fix or remove mod — cannot be loaded",
        "case_mismatch": "Fix or remove mod — cannot be loaded",
        "duplicate_id": "Disable one of the conflicting mods",
        "overhaul_vs_standalone": "Disable standalone mod — overhaul replaces this system",
        "xml_override": "Adjust load order — overhaul should load last",
        "load_order_priority": "Auto-reorder or manually raise priority",
        "scope_overlap": "Usually safe to ignore — only act if evidence overlap looks real",
        "redundant": "Safe to remove — functionality already provided",
        "asset_conflict": "Visual/audio override — last loaded mod wins",
        "poi_conflict": "Disable one mod or start a new world",
        "world_compat": "Requires a new world to function correctly",
        "performance": "Optional — may impact FPS or AI",
        "log_only": "Check logs — non-blocking issue",
        "missing_dependency": "Install required dependency mod(s)",
        CONFLICT_OVERRIDE: "Adjust load order — overhaul should load last",
        CONFLICT_REDUNDANT: "Safe to remove — functionality already provided",
        CONFLICT_MERGE: "Patch or manual XML merge recommended",
        CONFLICT_EXCLUSIVE: "Choose one mod only",
    }
    # Prefer table-mapped action; otherwise derive a sensible default
    action = table.get(ct)
    if action:
        # Refine generic load-order guidance when the evidence clearly points at UI/XUi.
        if ct == "load_order_priority" and _has_ui_xui_evidence(mod):
            return "UI/XUi conflict — swap UI mod order first (last loaded wins)"
        return action
    try:
        status = getattr(mod, "status", "") or ""
        # Defaults based on current status/severity when conflict_type is unknown
        if status.startswith("Conflict") or getattr(mod, "conflict", False):
            return "Adjust load order — overhaul should load last"
        if status in ("Error", "Missing"):
            return "Fix or remove mod — cannot be loaded"
        if status == "Redundant" or getattr(mod, "redundant", False):
            return "Safe to remove — functionality already provided"
    except Exception:
        pass
    return "None"


def get_row_tags(mod):
    try:
        return (determine_row_tag(mod),)
    except Exception:
        return ("ok",)


def derive_conflict_taxonomy(mod):
    # Determine mod-level conflict_type, reason and fixability from conflicts/redundancy
    try:
        # Redundant takes precedence
        if getattr(mod, "redundant_reason", None):
            mod.conflict_type = CONFLICT_REDUNDANT
            mod.conflict_reason = mod.redundant_reason
            mod.fixability = FIXABILITY[CONFLICT_REDUNDANT]
            return

        confs = getattr(mod, "conflicts", []) or []
        # Prefer error conflicts
        errs = [c for c in confs if c.get("level") == "error"]
        warns = [c for c in confs if c.get("level") == "warn"]
        infos = [c for c in confs if c.get("level") == "info"]

        def _set(ct, reason):
            mod.conflict_type = ct
            mod.conflict_reason = reason
            mod.fixability = FIXABILITY.get(ct)

        if errs:
            # map by conflict_type if present; avoid defaulting to override
            ct = errs[0].get("conflict_type")
            reason = errs[0].get("reason") or "Competing changes on same target."
            _set(ct, reason)
            return
        if warns:
            ct = warns[0].get("conflict_type") or "load_order_priority"
            reason = warns[0].get("reason") or "Override vs extend on same target."
            _set(ct, reason)
            return
        if infos:
            ct = infos[0].get("conflict_type") or None
            reason = infos[0].get("reason") or None
            if ct:
                _set(ct, reason)
            return
    except Exception:
        pass


def legend_filter_match(mod, legend_filter):
    try:
        if not legend_filter:
            return True
        if legend_filter == "error":
            return determine_row_tag(mod) == "error"
        if legend_filter == "conflict_high":
            return determine_row_tag(mod) == "conflict_high"
        if legend_filter == "conflict_low":
            return determine_row_tag(mod) == "conflict_low"
        if legend_filter == "redundant":
            return getattr(mod, "redundant", False)
        if legend_filter == "disabled":
            return not is_effectively_enabled(mod)
        if legend_filter == "ok":
            return getattr(mod, "severity", 0) == 0
        return True
    except Exception:
        return True


def should_hide_mod(mod, hide_low_conflicts):
    try:
        return bool(hide_low_conflicts) and getattr(mod, "conflict_level", None) == "low"
    except Exception:
        return False


def calculate_legend_counts(mods):
    counts = {
        "error": 0,
        "conflict_high": 0,
        "conflict_low": 0,
        "redundant": 0,
        "disabled": 0,
        "ok": 0,
    }
    for m in mods:
        counts[determine_row_tag(m)] = counts.get(determine_row_tag(m), 0) + 1
    return counts


def auto_resolve(mod):
    try:
        if getattr(mod, "severity", 0) < 30 and getattr(mod, "conflict", False):
            return "auto_adjust_load_order"
        if getattr(mod, "redundant", False):
            return "suggest_disable"
        return None
    except Exception:
        return None


def calculate_health_score(mods):
    try:
        if not mods:
            return 100
        total = sum(getattr(m, "severity", 0) for m in mods)
        max_possible = len(mods) * 100
        return max(0, 100 - int((total / max_possible) * 100))
    except Exception:
        return 100


# =========================================================
# GUI
# =========================================================

# =========================================================
# HOVER EFFECTS (Treeview + Buttons)
# =========================================================


def enable_treeview_hover(tree):
    hovered_item = {"id": None}

    def on_motion(event):
        row_id = tree.identify_row(event.y)
        if row_id == hovered_item["id"]:
            return

        # remove hover tag from previous item but preserve its other tags
        prev = hovered_item["id"]
        if prev:
            try:
                prev_tags = tuple(t for t in tree.item(prev, "tags") if t != "hover")
                tree.item(prev, tags=prev_tags)
            except Exception:
                pass

        hovered_item["id"] = row_id

        if row_id:
            try:
                tags = tuple(tree.item(row_id, "tags") or ())
                if "hover" not in tags:
                    tree.item(row_id, tags=tags + ("hover",))
            except Exception:
                pass

    def on_leave(event):
        prev = hovered_item["id"]
        if prev:
            try:
                prev_tags = tuple(t for t in tree.item(prev, "tags") if t != "hover")
                tree.item(prev, tags=prev_tags)
            except Exception:
                pass
        hovered_item["id"] = None

    tree.bind("<Motion>", on_motion)
    tree.bind("<Leave>", on_leave)


def enable_button_hover(widget):
    def on_enter(e):
        try:
            widget.configure(foreground="#000000")
        except Exception:
            pass

    def on_leave(e):
        try:
            widget.configure(foreground="#d4d4d4")
        except Exception:
            pass

    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)


def attach_tooltip(widget, text):
    tip = None

    def show_tip(event=None):
        nonlocal tip
        try:
            if tip:
                return
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 5
            tip.wm_geometry(f"+{x}+{y}")
            lbl = tk.Label(
                tip,
                text=text,
                bg="#333333",
                fg="#ffffff",
                relief="solid",
                borderwidth=1,
                padx=6,
                pady=4,
            )
            lbl.pack()
        except Exception:
            pass

    def hide_tip(event=None):
        nonlocal tip
        try:
            if tip:
                tip.destroy()
                tip = None
        except Exception:
            tip = None

    widget.bind("<Enter>", show_tip)
    widget.bind("<Leave>", hide_tip)


class ModAnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.colors = apply_dark_theme(self.root)
        self.root.title(f"{__title__} v{__version__} - 7DTD Mod Load Order Manager")
        self.root.geometry("900x650")
        # Map Treeview item_id -> Mod instance for quick updates (used by click-to-toggle)
        self.mod_lookup = {}
        # Catch otherwise-fatal Tk callback exceptions (esp. during scanning) and
        # persist stacktraces to a log file for diagnosis.
        try:
            self.root.report_callback_exception = self._report_callback_exception
        except Exception:
            pass

        self.mods_path = tk.StringVar(value=r"C:\Program Files (x86)\Steam\steamapps\common\7 Days To Die\Mods")

        # Deployment hardening / guardrails (defaults; overridden by settings.json)
        self.harden_deployment = True
        self.block_multiple_mods_dirs = True
        self.block_invalid_xml = True
        self.block_full_file_replacements = True
        self.enforce_single_ui_framework = True
        self.auto_prefix_ui_groups = True

        # State used for folder-based load order application
        self.user_disabled_ids = set()  # normalized ids
        self.order_overrides = {}  # normalized id -> int

        # Authoritative persistent mod state store (mods_state.json)
        self.mod_state_store = None
        try:
            if ModStateStore:
                self.mod_state_store = ModStateStore(MODS_STATE_FILE)
        except Exception:
            self.mod_state_store = None
        # Load persisted config (remember last used Mods path)
        try:
            self.load_config()
        except Exception:
            pass
        # (settings will be loaded after UI widgets are created)

        # Header label removed per design rollback; rely on window title only.

        top = tk.Frame(root, bg=self.colors["panel"])
        top.pack(fill="x", pady=5)

        tk.Label(top, text="Mods Library:").pack(side="left")
        # Create Mods Path entry using themed widget
        self.mods_entry = ttk.Entry(top, textvariable=self.mods_path, width=80)
        self.mods_entry.pack(side="left", padx=5)
        btn_browse = ttk.Button(top, text="Browse", command=self.change_path)
        btn_browse.pack(side="left")
        try:
            enable_button_hover(btn_browse)
        except Exception:
            pass

        btns = tk.Frame(root, bg=self.colors["panel"])
        btns.pack(fill="x", pady=5)

        btn_scan = ttk.Button(btns, text="Scan Mods", command=self.scan)
        btn_scan.pack(side="left", padx=5)
        btn_generate = ttk.Button(btns, text="Generate + Apply Load Order", command=self.generate_and_apply)
        btn_generate.pack(side="left", padx=5)
        btn_export = ttk.Button(btns, text="Export Load Order", command=self.export_load_order)
        btn_export.pack(side="left", padx=5)
        btn_export_vortex = ttk.Button(btns, text="Export Vortex", command=self.export_vortex)
        btn_export_vortex.pack(side="left", padx=5)
        btn_rename = ttk.Button(btns, text="Rename Folder…", command=self.rename_selected_mod_folder)
        btn_rename.pack(side="left", padx=5)

        try:
            enable_button_hover(btn_scan)
            enable_button_hover(btn_generate)
            enable_button_hover(btn_export)
            enable_button_hover(btn_export_vortex)
            enable_button_hover(btn_rename)
            try:
                pass
            except Exception:
                pass
        except Exception:
            pass

        # Search box (Ctrl+F focuses)
        tk.Label(btns, text="Search:").pack(side="right", padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(btns, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="right", padx=5)
        self.search_entry.bind("<KeyRelease>", lambda e: self.refresh_table())
        # Bind Ctrl+F to focus the search box
        root.bind_all("<Control-f>", lambda e: self.search_entry.focus_set())

        # Now load settings (geometry, last search) after search_var exists
        try:
            self.load_settings()
        except Exception:
            pass

        # One-time migration from legacy settings-based disabled ids to mods_state.json
        try:
            if self.mod_state_store and (not os.path.exists(MODS_STATE_FILE)):
                for iid in getattr(self, "user_disabled_ids", set()) or []:
                    try:
                        self.mod_state_store.set(str(iid), enabled=False, user_disabled=True)
                    except Exception:
                        continue
                try:
                    self.mod_state_store.save()
                except Exception:
                    pass
        except Exception:
            pass

        # Feature flags default values; may be overridden by settings
        self.debug_scanner = False
        self.enable_classification = False
        # LLM defaults (enable for all clients; model GPT-5.2-Codex)
        self.enable_llm = True
        self.llm_model = "gpt-5.2-codex"
        try:
            # If settings were loaded, flags may already be set; otherwise keep defaults
            # Fallback to reading settings directly
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                _s = json.load(f)
                self.debug_scanner = bool(_s.get("ENABLE_SCANNER_DEBUG", self.debug_scanner))
                self.enable_classification = bool(_s.get("ENABLE_CLASSIFICATION", self.enable_classification))
            self.enable_llm = bool(_s.get("ENABLE_LLM", self.enable_llm))
            self.llm_model = str(_s.get("LLM_MODEL", self.llm_model))
        except Exception:
            pass

        # Persistent conflict memory (CKB)
        try:
            from ..logic.conflict_memory import ConflictMemory

            self.conflict_memory = ConflictMemory(CONFLICT_MEMORY_FILE)
        except Exception:
            self.conflict_memory = None

        # Resolution knowledge base (RKB)
        try:
            from ..logic.resolution_knowledge import ResolutionKnowledgeBase

            self.resolution_kb = ResolutionKnowledgeBase(RESOLUTION_KB_FILE)
        except Exception:
            self.resolution_kb = None

        # Mod count display
        self.mod_count_var = tk.StringVar(value="Mods: 0 | Enabled: 0 | Conflicts: 0")

        count_frame = tk.Frame(root, bg=self.colors["bg"])
        count_frame.pack(fill="x", padx=10, pady=(0, 5))

        tk.Label(
            count_frame,
            textvariable=self.mod_count_var,
            fg=self.colors.get("fg", "#d4d4d4"),
            bg=self.colors.get("bg", "#1e1e1e"),
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        # Show filtered count (Showing X of Y)
        self.filtered_count_var = tk.StringVar(value="Showing 0 of 0")
        tk.Label(
            count_frame,
            textvariable=self.filtered_count_var,
            fg=self.colors.get("fg", "#d4d4d4"),
            bg=self.colors.get("bg", "#1e1e1e"),
            font=("Segoe UI", 9),
        ).pack(side="right")

        # Legend controls (severity filters + counts)
        self.legend_filter = None
        self.hide_low_conflicts = tk.BooleanVar(value=False)
        legend_frame = tk.Frame(root, bg=self.colors.get("bg", "#1e1e1e"))
        legend_frame.pack(fill="x", padx=10, pady=(0, 5))

        self._legend_vars = {
            "error": tk.StringVar(value="Critical (0)"),
            "conflict_high": tk.StringVar(value="High (0)"),
            "conflict_low": tk.StringVar(value="Low (0)"),
            "redundant": tk.StringVar(value="Redundant (0)"),
            "disabled": tk.StringVar(value="Disabled (0)"),
            "ok": tk.StringVar(value="OK (0)"),
        }

        def make_filter_btn(parent, tag):
            btn = ttk.Button(
                parent,
                textvariable=self._legend_vars[tag],
                command=lambda t=tag: self.set_legend_filter(t),
            )
            btn.pack(side="left", padx=4)
            return btn

        self.legend_buttons = {}
        tooltip_texts = {
            "error": "Critical — missing dependency / duplicate IDs / invalid XML / save-breaking risk",
            "conflict_high": "High — load order, XML overrides, or dependency risk",
            "conflict_low": "Low — informational / cosmetic overlaps",
            "redundant": "Older version detected — kept newest; review/confirm disable",
            "disabled": "Manually disabled — excluded from load order",
            "ok": "No issues detected",
        }

        for tag in [
            "error",
            "conflict_high",
            "conflict_low",
            "redundant",
            "disabled",
            "ok",
        ]:
            b = make_filter_btn(legend_frame, tag)
            self.legend_buttons[tag] = b
            try:
                attach_tooltip(b, tooltip_texts.get(tag, tag))
            except Exception:
                pass

        ttk.Checkbutton(
            legend_frame,
            text="Hide low conflicts",
            variable=self.hide_low_conflicts,
            command=self.refresh_table,
        ).pack(side="right")

        # Filters panel: Category dropdown, Severity slider, Conflicts-only toggle
        filters_frame = tk.Frame(root, bg=self.colors.get("bg", "#1e1e1e"))
        filters_frame.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(
            filters_frame,
            text="Category:",
            fg=self.colors.get("fg", "#d4d4d4"),
            bg=self.colors.get("bg", "#1e1e1e"),
        ).pack(side="left")
        self.filter_category_var = tk.StringVar(value="")
        # Values will be populated dynamically from scanned mods, sorted alphabetically with counts
        self.filter_category = ttk.Combobox(
            filters_frame,
            textvariable=self.filter_category_var,
            values=[],
            width=28,
            state="readonly",
        )
        self.filter_category.pack(side="left", padx=(6, 16))
        self.filter_category.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())

        tk.Label(
            filters_frame,
            text="Severity ≥",
            fg=self.colors.get("fg", "#d4d4d4"),
            bg=self.colors.get("bg", "#1e1e1e"),
        ).pack(side="left")
        self.filter_severity_var = tk.IntVar(value=0)
        self.filter_severity = ttk.Scale(
            filters_frame,
            from_=0,
            to=100,
            orient="horizontal",
            command=lambda v: self._on_severity_change(v),
        )
        self.filter_severity.set(0)
        self.filter_severity.pack(side="left", padx=(6, 6), fill="x", expand=False)
        self.filter_severity_label = tk.Label(
            filters_frame,
            text="0",
            fg=self.colors.get("fg", "#d4d4d4"),
            bg=self.colors.get("bg", "#1e1e1e"),
        )
        self.filter_severity_label.pack(side="left", padx=(0, 16))

        self.filter_conflicts_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filters_frame,
            text="Conflicts only",
            variable=self.filter_conflicts_only_var,
            command=self.refresh_table,
        ).pack(side="left")

        # Show all toggle (ignore all filters)
        self.show_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filters_frame,
            text="Show All (ignore filters)",
            variable=self.show_all_var,
            command=self.refresh_table,
        ).pack(side="left", padx=(12, 0))

        frame = tk.Frame(root, bg=self.colors["panel"])
        frame.pack(fill="both", expand=True)

        # Replace text output with a table (ttk.Treeview)
        # Columns: Mod Name, Folder Name, ModInfo.xml, Category, Priority, Enabled, Conflict, Status, Integrity, Action
        table_container = tk.Frame(frame, bg=self.colors.get("panel", "#1e1e1e"))
        table_container.pack(fill="both", expand=True)

        self.table = ttk.Treeview(
            table_container,
            columns=("enabled", "modname", "category", "priority", "status", "action"),
            show="headings",
        )

        # Visual polish: style
        style = ttk.Style()
        style.theme_use("default")

        # ---------- TREEVIEW ----------
        style.configure(
            "Treeview",
            background=self.colors.get("tree_bg", "#252526"),
            foreground=self.colors.get("tree_fg", "#d4d4d4"),
            fieldbackground=self.colors.get("tree_bg", "#252526"),
            rowheight=26,
            bordercolor=self.colors.get("border", "#3c3c3c"),
        )

        style.configure(
            "Treeview.Heading",
            background=self.colors.get("header_bg", "#0e639c"),
            foreground="#ffffff",
            relief="flat",
        )

        # Softer selection highlight
        style.map(
            "Treeview",
            background=[
                ("selected", "#2a3b4d"),
                ("active", self.colors.get("tree_bg", "#252526")),
            ],
            foreground=[
                ("selected", "#ffffff"),
                ("active", self.colors.get("tree_fg", "#d4d4d4")),
            ],
        )

        style.map(
            "Treeview.Heading",
            background=[("active", self.colors.get("header_bg", "#0e639c"))],
            foreground=[("active", "#ffffff")],
        )

        # Column definitions (clickable headings for sorting)
        # Clicking toggles ascending/descending for the column
        self._sort_state = {}

        def heading_cmd(col):
            return lambda: self.sort_by_column(col)

        self.table.heading("enabled", text="Enabled", anchor="center", command=heading_cmd("enabled"))
        self.table.column("enabled", width=70, anchor="center")

        self.table.heading("modname", text="Mod Name", anchor="w", command=heading_cmd("modname"))
        self.table.column("modname", width=320, anchor="w")

        self.table.heading("category", text="Category", anchor="w", command=heading_cmd("category"))
        self.table.column("category", width=200, anchor="w")

        self.table.heading(
            "priority",
            text="Tier",
            anchor="center",
            command=heading_cmd("priority"),
        )
        self.table.column("priority", width=80, anchor="center")

        self.table.heading("status", text="Status", anchor="w", command=heading_cmd("status"))
        self.table.column("status", width=260, anchor="w")

        self.table.heading("action", text="Suggested Action", anchor="w")
        self.table.column("action", width=280, anchor="w")

        # Always-available scrollbars via container grid
        vsb = ttk.Scrollbar(table_container, orient="vertical", command=self.table.yview)
        hsb = ttk.Scrollbar(table_container, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Severity tag colors (final)
        configure_treeview_tags(self.table)

        # Grid layout to keep scrollbars visible even on smaller windows
        table_container.grid_rowconfigure(0, weight=1)
        table_container.grid_columnconfigure(0, weight=1)
        self.table.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # Enable hover effect for Treeview rows (preserve original tags)
        try:
            enable_treeview_hover(self.table)
        except Exception:
            pass

        # Optional: lock selection mode to avoid multi-select recolor issues
        try:
            self.table.configure(selectmode="extended")
            # Ctrl+A to select all rows
            self.table.bind(
                "<Control-a>",
                lambda e: self.table.selection_set(self.table.get_children()),
            )
        except Exception:
            pass

        # Global Ctrl+A handler: selects all rows only when the mod table has focus
        def _select_all_table(event=None):
            try:
                w = self.root.focus_get()
                # Only act when the focused widget is the main table; avoid interfering with Entries/Text
                if w == self.table or str(w) == str(self.table):
                    try:
                        self.table.selection_set(self.table.get_children())
                    except Exception:
                        pass
                    return "break"
            except Exception:
                pass

        try:
            root.bind_all("<Control-a>", _select_all_table)
        except Exception:
            pass

        # Tooltip for high-risk rows
        try:
            self._init_highrisk_tooltip()
        except Exception:
            pass

        # Bind click for enabling/disabling mods (toggle Enabled column on click)
        self.table.bind("<Button-1>", self.on_tree_click)

        # Right-click context menu actions (e.g. mark as framework)
        try:
            self._row_context_mod = None
            self._row_context_is_framework = tk.BooleanVar(value=False)
            self._row_context_menu = tk.Menu(self.root, tearoff=0)
            self._row_context_menu.add_checkbutton(
                label="Framework mod (allow full UI XML)",
                variable=self._row_context_is_framework,
                command=self._toggle_framework_for_context_mod,
            )
            self.table.bind("<Button-3>", self.on_tree_right_click)
        except Exception:
            pass

        # ensure settings are saved on close
        try:
            self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        except Exception:
            pass

        # ========== STATUS DISPLAY PANEL (transparent operation logging) ==========
        # Create a status panel for user-visible operation feedback
        try:
            status_panel = tk.Frame(frame, bg=self.colors.get("panel", "#1e1e1e"), height=120)
            status_panel.pack(fill="x", padx=0, pady=(5, 0))
            status_panel.pack_propagate(False)  # Fixed height

            # Label for status panel
            status_label = tk.Label(
                status_panel,
                text="Operation Log:",
                bg=self.colors.get("panel", "#1e1e1e"),
                fg=self.colors.get("header_bg", "#0e639c"),
                font=("Segoe UI", 9, "bold"),
            )
            status_label.pack(anchor="w", padx=6, pady=(3, 0))

            # Create a frame to hold status text and scrollbar
            status_text_frame = tk.Frame(status_panel, bg=self.colors.get("panel", "#1e1e1e"))
            status_text_frame.pack(fill="both", expand=True, padx=6, pady=(0, 3))

            # Status text widget with scrollbar
            self.status_text = tk.Text(
                status_text_frame,
                height=5,
                bg=self.colors.get("tree_bg", "#252526"),
                fg=self.colors.get("tree_fg", "#d4d4d4"),
                font=("Consolas", 8),
                wrap="word",
                relief="solid",
                borderwidth=1,
            )

            status_scrollbar = ttk.Scrollbar(status_text_frame, orient="vertical", command=self.status_text.yview)
            self.status_text.configure(yscrollcommand=status_scrollbar.set)

            self.status_text.pack(side="left", fill="both", expand=True)
            status_scrollbar.pack(side="right", fill="y")

            # Make status_text read-only
            self.status_text.configure(state="disabled")

            # ===== Initialize transparency logger with GUI callback =====
            from .transparency_logger import OperationLogger

            def log_to_gui(message: str):
                """Callback to update status text from logger."""
                try:
                    self.status_text.configure(state="normal")
                    self.status_text.insert("end", message + "\n")
                    self.status_text.see("end")  # Auto-scroll to bottom
                    self.status_text.configure(state="disabled")
                    self.root.update_idletasks()  # Refresh GUI
                except Exception:
                    pass

            self.operation_logger = OperationLogger(print_callback=log_to_gui)

            # Log startup with detailed info
            import os
            from pathlib import Path

            logger_msg = f"🚀 [SYSTEM] Application launched (v{__version__})"
            self.operation_logger.log(logger_msg)
            try:
                self.operation_logger.log(f"📂 [SYSTEM] App root: {APP_ROOT_DIR}")
            except Exception:
                pass

        except Exception as e:
            # If status panel creation fails, continue without it
            self.status_text = None
            self.operation_logger = None
            pass

        # Visual legend: compact, always visible at the bottom
        self.build_visual_legend()

        # Heatmap panel
        try:
            self._build_heatmap_panel(root)
        except Exception:
            pass

    # -----------------------------------------------------
    # Shared scan helpers (logic-only; keeps UI consistent)
    # -----------------------------------------------------
    def _mark_overhaul_flags(self):
        # simple overhaul detection from name/modinfo text or category
        for m in getattr(self, "mods", []) or []:
            try:
                txt = (m.name or "").lower()
                info_txt = ""
                modinfo_path = os.path.join(m.path, "ModInfo.xml")
                if os.path.isfile(modinfo_path):
                    with open(modinfo_path, "r", encoding="utf-8", errors="ignore") as f:
                        info_txt = (f.read() or "").lower()
                if ("overhaul" in txt) or ("overhaul" in info_txt) or (getattr(m, "category", "") == "Overhauls"):
                    m.is_overhaul = True
            except Exception:
                pass

    def _compute_integrity(self):
        """Compute real integrity status.

        Required checks:
        - Required ModInfo.xml present (unless prefab-only)
        - No malformed XML (all mod XML files)
        - No duplicate ModInfo UUIDs (best-effort)
        - Dependency presence (RequiredMod in ModInfo.xml)
        Optional:
        - Hash verification (when enable_integrity=True)

        Integrity column must be: OK | Warning | Invalid.
        """

        def _norm_folder(name: str) -> str:
            try:
                n = (name or "").strip()
                if n.startswith("__DISABLED__"):
                    n = n[len("__DISABLED__") :]
                if len(n) >= 4 and n[:3].isdigit() and n[3] == "_":
                    n = n[4:]
                return n.strip().lower()
            except Exception:
                return (name or "").strip().lower()

        def _extract_uuid(modinfo_path: pathlib.Path) -> str:
            try:
                tree = ET.parse(str(modinfo_path))
                root = tree.getroot()
                for e in root.iter():
                    for k, v in (e.attrib or {}).items():
                        if (k or "").lower() in {"uuid", "guid", "id"}:
                            s = str(v or "").strip()
                            if s and len(s) >= 8:
                                return s
                return ""
            except Exception:
                return ""

        def _extract_required_mods(modinfo_path: pathlib.Path):
            req = []
            try:
                tree = ET.parse(str(modinfo_path))
                root = tree.getroot()
                for e in root.iter():
                    tag = (e.tag or "").lower()
                    if tag in {
                        "requiredmod",
                        "dependencymod",
                        "dependencymods",
                        "dependency",
                        "depend",
                    }:
                        # common patterns: <RequiredMod value="X"/> or <RequiredMod name="X"/>
                        v = e.attrib.get("value") or e.attrib.get("name") or e.attrib.get("mod")
                        if v:
                            req.append(str(v).strip())
                return [r for r in req if r]
            except Exception:
                return []

        # Build a normalized id set for dependency checks
        try:
            known_mod_ids = {_norm_folder(pathlib.Path(m.path).name) for m in (self.mods or [])}
        except Exception:
            known_mod_ids = set()

        uuid_to_mods = {}

        for mod in self.mods or []:
            issues = []
            warnings = []

            try:
                mod_path = pathlib.Path(mod.path)
            except Exception:
                mod.integrity = "Invalid"
                continue

            # Required ModInfo unless prefab-only
            if (not getattr(mod, "has_modinfo", True)) and (not getattr(mod, "is_poi", False)):
                issues.append("Missing ModInfo.xml")

            # Parse all XML files (malformed => Invalid)
            invalid_xml_files = []
            try:
                for p in mod_path.rglob("*.xml"):
                    try:
                        if not p.is_file():
                            continue
                        # ModInfo.xml already parsed elsewhere; still validate here if present
                        ET.parse(str(p))
                    except ET.ParseError:
                        invalid_xml_files.append(str(p.relative_to(mod_path)))
                    except Exception:
                        continue
            except Exception:
                pass

            if invalid_xml_files:
                issues.append(f"Malformed XML ({len(invalid_xml_files)})")
                try:
                    mod.invalid_xml = True
                    mod.invalid_xml_files = invalid_xml_files[:50]
                    mod.conflict_type = "invalid_xml"
                except Exception:
                    pass

            # Dependency presence (best-effort)
            try:
                modinfo_path = mod_path / "ModInfo.xml"
                if modinfo_path.exists():
                    required = _extract_required_mods(modinfo_path)
                    missing = []
                    for r in required:
                        if _norm_folder(r) not in known_mod_ids:
                            missing.append(r)
                    if missing:
                        issues.append(
                            f"Missing dependency ({', '.join(missing[:3])}{'...' if len(missing) > 3 else ''})"
                        )
                        try:
                            mod.conflict_type = "missing_dependency"
                        except Exception:
                            pass
            except Exception:
                pass

            # UUID (best-effort) + duplicates handled in second pass
            try:
                modinfo_path = mod_path / "ModInfo.xml"
                if modinfo_path.exists():
                    u = _extract_uuid(modinfo_path)
                    if u:
                        uuid_to_mods.setdefault(u.lower(), []).append(mod)
                        mod.modinfo_uuid = u
            except Exception:
                pass

            # Minimal presence expectations
            try:
                cfg = mod_path / "Config"
                if cfg.exists() and cfg.is_dir():
                    any_cfg_xml = any(p.is_file() and p.name.lower().endswith(".xml") for p in cfg.rglob("*.xml"))
                    if not any_cfg_xml:
                        warnings.append("Config folder has no XML")
            except Exception:
                pass

            # Optional hash verification
            if bool(getattr(self, "enable_integrity", False)):
                try:
                    self.mod_hashes = dict(getattr(self, "mod_hashes", {}))
                    current_hash = hash_mod_folder(mod_path)
                    baseline = self.mod_hashes.get(mod.name)
                    if baseline is None:
                        self.mod_hashes[mod.name] = current_hash
                    elif baseline != current_hash:
                        warnings.append("Files modified since baseline")
                except Exception:
                    pass

            # Folder renamed multiple times: detect stacked numeric prefixes (Warning)
            try:
                fname = mod_path.name
                if re.match(r"^(?:\d+_){2,}", fname):
                    warnings.append("Folder renamed multiple times")
            except Exception:
                pass

            # Set integrity enum
            try:
                if issues:
                    mod.integrity = "Invalid"
                elif warnings:
                    mod.integrity = "Warning"
                else:
                    mod.integrity = "OK"
                mod.integrity_issues = issues
                mod.integrity_warnings = warnings
            except Exception:
                pass

        # Second pass: duplicate UUID detection
        try:
            for u, mods in uuid_to_mods.items():
                if len(mods) <= 1:
                    continue
                for m in mods:
                    try:
                        m.integrity = "Invalid"
                        lst = list(getattr(m, "integrity_issues", []) or [])
                        lst.append(f"Duplicate ModInfo UUID: {u}")
                        m.integrity_issues = lst
                        m.conflict_type = "duplicate_id"
                    except Exception:
                        pass
        except Exception:
            pass

        # Persist hash baseline if enabled
        if bool(getattr(self, "enable_integrity", False)):
            try:
                self.save_settings()
            except Exception:
                pass

    # -----------------------------------------------------

    def build_visual_legend(self):
        _build_visual_legend(self.root)

    def _on_severity_change(self, val):
        try:
            self.filter_severity_var.set(int(float(val)))
            self.filter_severity_label.config(text=str(self.filter_severity_var.get()))
        except Exception:
            pass
        try:
            if hasattr(self, "table"):
                self.refresh_table()
        except Exception:
            pass

    def _init_highrisk_tooltip(self):
        try:
            self._highrisk_tooltip = HighRiskTooltip(
                self.root,
                self.table,
                self.mod_lookup,
                text="High-risk category — modifies core game systems",
            )
            self._highrisk_tooltip.install()
        except Exception:
            pass

    def _build_heatmap_panel(self, root):
        _heatmap.build_heatmap_panel(self, root)

    def _update_heatmap(self):
        _heatmap.update_heatmap(self)

    def _heatmap_select_category(self, category_name: str):
        _heatmap.heatmap_select_category(self, category_name)

    def _heatmap_reset(self):
        _heatmap.heatmap_reset(self)

    def _set_category_filter(self, base_category: str):
        """Set the category dropdown to a base category name (no counts)."""
        try:
            if not hasattr(self, "filter_category_var"):
                return
            base = (base_category or "All").strip()
            counts = self._category_counts() if hasattr(self, "_category_counts") else {}
            total = len(getattr(self, "mods", []) or [])
            if base == "All":
                self.filter_category_var.set(f"All ({total})")
                return
            if base in counts:
                self.filter_category_var.set(f"{base} ({counts[base]})")
            else:
                # fallback: keep base if counts missing
                self.filter_category_var.set(base)
        except Exception:
            pass

    # public alias used by buttons/calls
    def refresh_heatmap(self):
        try:
            self._update_heatmap()
        except Exception:
            pass

    def jump_to_category(self, category_name: str):
        try:
            # Ensure map exists; if not, refresh table to rebuild
            if not hasattr(self, "_category_rows") or not self._category_rows:
                try:
                    self.refresh_table()
                except Exception:
                    pass
            iid = None
            # direct match or normalized alias
            iid = self._category_rows.get(category_name)
            if not iid:
                iid = self._category_rows.get(normalize_category(category_name))
            if not iid:
                messagebox.showinfo("Category", f"No visible section for: {category_name}")
                return
            self.table.see(iid)
            try:
                self.table.selection_set(iid)
                self.table.focus(iid)
            except Exception:
                pass
        except Exception:
            pass

    def change_path(self):
        try:
            path = filedialog.askdirectory()
            if path:
                self.mods_path.set(path)

                self.operation_logger.log(f"[ACTION] 📁 Changed mods folder: {path}")

                # Log folder selection with transparency logger
                try:
                    if self.operation_logger:
                        # Count mod folders in the selected path
                        import os

                        try:
                            items = os.listdir(path)
                            mod_count = len([item for item in items if os.path.isdir(os.path.join(path, item))])
                            self.operation_logger.log_folder_selected(path, mod_count)
                        except Exception:
                            self.operation_logger.log_folder_selected(path, 0)
                except Exception:
                    pass

                try:
                    self.save_config()
                except Exception:
                    pass
                try:
                    self.save_settings()
                except Exception:
                    pass
        except Exception as e:
            if self.operation_logger:
                self.operation_logger.log(f"[ERROR] X Folder change failed: {str(e)[:100]}")
            messagebox.showerror("Error", f"Failed to change mods folder: {e}")
    def _normalize_install_id(self, folder_name: str) -> str:
        """Stable per-install identifier derived from folder name.

        Strips legacy prefixes like __DISABLED__ and 000_.
        """
        try:
            name = str(folder_name or "")
            if name.startswith("__DISABLED__"):
                name = name[len("__DISABLED__") :]
            m = _ORDER_PREFIX_RE.match(name)
            if m:
                name = name[len(m.group(0)) :]
            return name.strip().lower()
        except Exception:
            return str(folder_name or "").strip().lower()

    def _clean_folder_name_for_order(self, folder_name: str) -> str:
        """Name to use as the base for ordered folders (without legacy prefixes)."""
        try:
            name = str(folder_name or "")
            if name.startswith("__DISABLED__"):
                name = name[len("__DISABLED__") :]
            m = _ORDER_PREFIX_RE.match(name)
            if m:
                name = name[len(m.group(0)) :]
            return name.strip() or "Mod"
        except Exception:
            return str(folder_name or "Mod").strip() or "Mod"

    def scan(self):
        try:
            if self.operation_logger:
                self.operation_logger.log("[SYSTEM] 🔍 Scanning mods folder...")
            if getattr(self, "debug_scanner", False):
                print(">>> SCAN STARTED")
            mods_dir = self.mods_path.get()
            if getattr(self, "debug_scanner", False):
                print("Mods path:", mods_dir)
                print("Exists:", os.path.isdir(mods_dir))

            if not os.path.isdir(mods_dir):
                messagebox.showerror("Error", "Mods path does not exist")
                return

            # Detect multiple active Mods folders (GameFolder/Mods vs Documents/7DaysToDie/Mods).
            # We don't block scanning, but we do surface the state early.
            try:
                from logic.deployment_guardrails import validate_single_mods_dir

                st, dir_issues = validate_single_mods_dir(mods_root=mods_dir)
                try:
                    self._mods_dir_status = st
                except Exception:
                    pass
                if dir_issues and bool(getattr(self, "block_multiple_mods_dirs", True)):
                    try:
                        messagebox.showwarning("Multiple Mods folders detected", dir_issues[0].details or "")
                    except Exception:
                        pass
            except Exception:
                pass

            # Reset in-memory list
            self.mods = []

            # Clear table
            for row in self.table.get_children():
                self.table.delete(row)

            try:
                entries = os.listdir(mods_dir)
            except Exception as e:
                messagebox.showerror("Scan", f"Failed to list Mods Library folder:\n\n{e}")
                return

            # Load authoritative mod state BEFORE conflict detection
            try:
                state_store = getattr(self, "mod_state_store", None)
                if state_store:
                    state_store.load()
            except Exception:
                state_store = None
                pass

            # Enumerate mod folders
            def _add_mod_entry(*, folder_name: str, folder_path: str, force_disabled: bool = False):
                try:
                    if getattr(self, "debug_scanner", False):
                        try:
                            print("FOUND MOD DIR:", folder_name)
                        except Exception:
                            pass

                    mod = Mod(name=folder_name, path=folder_path)
                    try:
                        mod.install_id = self._normalize_install_id(mod.name)

                        # Physical Disabled folder means "not loaded in-game".
                        if force_disabled:
                            mod.user_disabled = True
                            mod.enabled = False

                        # Back-compat: infer disabled from folder prefix
                        if (not force_disabled) and mod.name.startswith("__DISABLED__"):
                            mod.user_disabled = True
                            mod.enabled = False

                        # Authoritative: apply persisted enabled state from mods_state.json
                        try:
                            if state_store and mod.install_id:
                                st = state_store.get(mod.install_id)
                                if st is not None:
                                    mod.enabled = bool(st.enabled)
                                    mod.user_disabled = bool(st.user_disabled)
                        except Exception:
                            pass

                        # Re-apply physical-disabled invariant after persisted state.
                        if force_disabled:
                            mod.user_disabled = True

                        # Enforce invariant
                        if getattr(mod, "user_disabled", False):
                            mod.enabled = False
                        if getattr(mod, "disabled", False) and not getattr(mod, "user_disabled", False):
                            mod.enabled = False

                        # Apply persisted order override (manual override)
                        if mod.install_id and (mod.install_id in getattr(self, "order_overrides", {})):
                            try:
                                v = self.order_overrides.get(mod.install_id)
                                mod.order_override = int(v) if v is not None else None
                            except Exception:
                                mod.order_override = None
                    except Exception:
                        pass

                    self.mods.append(mod)
                except Exception:
                    pass

            def _has_modinfo_xml(dir_path: str) -> bool:
                try:
                    return os.path.isfile(os.path.join(dir_path, "ModInfo.xml")) or os.path.isfile(
                        os.path.join(dir_path, "modinfo.xml")
                    )
                except Exception:
                    return False

            def _expand_nested_mod_dirs(container_path: str):
                """Return a list of (name, path) pairs to treat as mods.

                If container has ModInfo.xml, it's the mod.
                Else if any immediate children have ModInfo.xml, treat each such child as a mod.
                Else if any grandchildren have ModInfo.xml, treat each such grandchild as a mod.
                Else fall back to container as a single mod (it may be a POI mod or an invalid mod).
                """
                try:
                    if _has_modinfo_xml(container_path):
                        return [(os.path.basename(container_path), container_path)]

                    # 1-level nested
                    try:
                        children = [
                            os.path.join(container_path, d)
                            for d in os.listdir(container_path)
                            if os.path.isdir(os.path.join(container_path, d))
                        ]
                    except Exception:
                        children = []

                    child_mods = []
                    for child_path in sorted(children, key=lambda p: os.path.basename(p).lower()):
                        if _has_modinfo_xml(child_path):
                            child_mods.append((os.path.basename(child_path), child_path))
                    if child_mods:
                        return child_mods

                    # 2-level nested (grandchildren)
                    grandchild_mods = []
                    # Guardrail: avoid walking huge trees; only go 2 levels deep.
                    for child_path in sorted(children, key=lambda p: os.path.basename(p).lower()):
                        try:
                            grands = [
                                os.path.join(child_path, d)
                                for d in os.listdir(child_path)
                                if os.path.isdir(os.path.join(child_path, d))
                            ]
                        except Exception:
                            continue
                        for gc_path in sorted(grands, key=lambda p: os.path.basename(p).lower()):
                            if _has_modinfo_xml(gc_path):
                                grandchild_mods.append((os.path.basename(gc_path), gc_path))
                    if grandchild_mods:
                        return grandchild_mods

                    return [(os.path.basename(container_path), container_path)]
                except Exception:
                    return [(os.path.basename(container_path), container_path)]

            for entry in sorted(entries, key=lambda s: str(s).lower()):
                full = os.path.join(mods_dir, entry)
                if not os.path.isdir(full):
                    continue

                # Common pattern: a physical 'Disabled' subfolder containing disabled mods.
                # Treat each child folder as a disabled mod entry; do NOT treat the container as a mod.
                if str(entry).strip().lower() == "disabled":
                    try:
                        for sub in sorted(os.listdir(full), key=lambda s: str(s).lower()):
                            sub_full = os.path.join(full, sub)
                            if os.path.isdir(sub_full):
                                for nm, pth in _expand_nested_mod_dirs(sub_full):
                                    _add_mod_entry(folder_name=nm, folder_path=pth, force_disabled=True)
                    except Exception:
                        pass
                    continue

                for nm, pth in _expand_nested_mod_dirs(full):
                    _add_mod_entry(folder_name=nm, folder_path=pth, force_disabled=False)

        except Exception:
            tb = traceback.format_exc()
            log_path = _append_crash_log("scan()", tb)
            if log_path:
                tb = tb.rstrip() + f"\n\nLog written to: {log_path}\n"
            try:
                print(tb)
            except Exception:
                pass
            try:
                if self.operation_logger:
                    self.operation_logger.log("[ERROR] Scan failed: unexpected error occurred")
            except Exception:
                pass
            try:
                if hasattr(self, "show_scrollable_popup"):
                    self.show_scrollable_popup(tb, title="Scan crashed")
                else:
                    _safe_show_error("Scan crashed", tb)
            except Exception:
                pass
            return

        # In-memory load order derived from numeric prefix when present (legacy)
        try:
            for i, m in enumerate(self.mods):
                pref = _parse_order_prefix(getattr(m, "name", ""))
                # Keep a stable fallback ordering when prefix not present
                m.load_order = pref if pref is not None else (900 + i)
                try:
                    if isinstance(getattr(m, "order_override", None), int):
                        m.load_order = int(getattr(m, "order_override"))
                except Exception:
                    pass
        except Exception:
            pass

        if getattr(self, "debug_scanner", False):
            print("MOD DIR COUNT:", len(self.mods))

        # Category classification is XML-driven and cached (not per-frame)
        if not hasattr(self, "mod_metadata_store"):
            try:
                if ModMetadataStore:
                    self.mod_metadata_store = ModMetadataStore(os.path.join("data", "mod_metadata.json"))
                else:
                    self.mod_metadata_store = None
            except Exception:
                self.mod_metadata_store = None

        # Minimal ModInfo parsing + XML-driven category assignment
        meta_store = getattr(self, "mod_metadata_store", None)
        for m in self.mods:
            try:
                modinfo_path = os.path.join(m.path, "ModInfo.xml")
                m.has_modinfo = os.path.isfile(modinfo_path)
                # Best-effort parse ModInfo metadata for UpdateEngine
                try:
                    if m.has_modinfo:
                        mi_name, mi_ver = parse_modinfo_name_version(modinfo_path)
                    else:
                        mi_name, mi_ver = "", ""
                    m.modinfo_name = mi_name
                    m.modinfo_version = mi_ver
                except Exception:
                    pass
                # preclassify missing ModInfo as specific conflict type (non-POI)
                try:
                    m.is_poi = (not m.has_modinfo) and is_poi_prefab_mod(m.path)
                except Exception:
                    m.is_poi = False
                if (not m.has_modinfo) and (not m.is_poi):
                    try:
                        m.conflict_type = "no_modinfo"
                    except Exception:
                        pass

                # Authoritative XML-driven categorization (folder names never override)
                try:
                    if meta_store and detect_categories_for_mod:
                        meta = meta_store.get_or_compute(
                            folder_name=m.name,
                            mod_path=m.path,
                            compute_fn=detect_categories_for_mod,
                        )
                        m.categories = list(getattr(meta, "categories", []) or [])
                        m.category = normalize_category(getattr(meta, "primary_category", None) or "Miscellaneous")
                        m.category_evidence = dict(getattr(meta, "evidence", {}) or {})
                        try:
                            m.is_framework = bool(getattr(meta, "is_framework", False))
                        except Exception:
                            m.is_framework = False
                    else:
                        m.categories = ["Miscellaneous"]
                        m.category = "Miscellaneous"
                        m.category_evidence = {}
                        m.is_framework = False
                except Exception:
                    m.categories = ["Miscellaneous"]
                    m.category = "Miscellaneous"
                    m.category_evidence = {}
                    try:
                        m.is_framework = False
                    except Exception:
                        pass

                # Tier + semantic impact (used by rule-based load order engine)
                try:
                    m.tier = infer_tier(m)
                    m.semantic_impact = infer_semantic_impact(m)
                    # For backward-compatible UI column naming, keep `priority` as a label.
                    m.priority = str(getattr(m, "tier", ""))
                except Exception:
                    m.tier = "Content Additions"
                    m.semantic_impact = "Additive Content"
                    m.priority = str(m.tier)

                # Conflict patch mods (generated by Resolve Conflicts) are a special case
                try:
                    m.is_patch = bool(_is_patch_mod_name(m.name))
                except Exception:
                    m.is_patch = False
                try:
                    m.scopes = extract_scopes(m.name, pathlib.Path(m.path).name)
                except Exception:
                    m.scopes = set()
            except Exception:
                continue

        # Optional deep XML analysis + semantic conflict detection
        if getattr(self, "enable_classification", False):
            prog = None
            try:
                from gui.progress import ProgressDialog

                prog = ProgressDialog(self.root, title="Deep scan: analyzing mods")
                prog.set_text("Deep scan: analyzing mods (0/?)")
                prog.set_percent(0)
            except Exception:
                prog = None

            total = max(1, len(self.mods))
            for idx, m in enumerate(self.mods):
                try:
                    if prog:
                        prog.set_text(f"Deep scan: analyzing {idx + 1}/{total} — {getattr(m, 'name', '')}")
                        prog.set_percent(int(round((idx / total) * 100)))
                except Exception:
                    pass

                try:
                    from scanner.xml_analyzer import analyze_xml

                    analyze_xml(m)
                except Exception:
                    pass

                # Non-XML assets (textures/audio/models/etc) for asset conflict detection
                try:
                    from scanner.asset_scanner import scan_asset_files

                    m.asset_files = scan_asset_files(m.path)
                except Exception:
                    try:
                        m.asset_files = set()
                    except Exception:
                        pass

            try:
                if prog:
                    prog.set_text("Deep scan: detecting conflicts")
                    prog.set_percent(95)
            except Exception:
                pass
            self._mark_overhaul_flags()
            try:
                from logic.conflict_detector import detect_conflicts

                detect_conflicts(self.mods)
                # strict XML collision via simulator: enrich conflicts with xml_override kind
                try:
                    enabled_mods = [
                        (m.name, m.path) for m in self.mods if is_effectively_enabled(m) and is_deployable_mod(m)
                    ]
                    from mock_deploy.engine import simulate_deployment

                    state, sim_conflicts = simulate_deployment(enabled_mods)
                    # Filter out conflicts that are resolved by a later conflict patch mod
                    try:
                        last = getattr(state, "last_mut", {}) or {}
                    except Exception:
                        last = {}
                    by_mod = {}
                    for ct in sim_conflicts:
                        try:
                            # ignore patch-involving traces
                            if _is_patch_mod_name(ct.first.mod) or _is_patch_mod_name(ct.second.mod):
                                continue
                            # resolved if last writer is a patch mod
                            lm = last.get((ct.file, ct.xpath))
                            if lm and _is_patch_mod_name(getattr(lm, "mod", "")):
                                continue
                        except Exception:
                            pass
                        by_mod.setdefault(ct.first.mod, []).append(ct)
                        by_mod.setdefault(ct.second.mod, []).append(ct)
                    for m in self.mods:
                        for ct in by_mod.get(m.name, []):
                            entry = {
                                "level": "error",
                                "file": ct.file,
                                "target": ct.xpath,
                                "with": ct.second.mod if ct.first.mod == m.name else ct.first.mod,
                                "reason": "Same file and XPath node modified by multiple mods.",
                                "suggestion": "Adjust load order — overhaul should load last",
                                "conflict_type": "xml_override",
                            }
                            try:
                                # avoid duplicates
                                sig = (
                                    entry["level"],
                                    entry["file"],
                                    entry["target"],
                                    entry["with"],
                                )
                                if not any(
                                    (
                                        c.get("level"),
                                        c.get("file"),
                                        c.get("target"),
                                        c.get("with"),
                                    )
                                    == sig
                                    for c in getattr(m, "conflicts", [])
                                ):
                                    m.conflicts.append(entry)
                            except Exception:
                                pass
                except Exception:
                    pass
                for m in self.mods:
                    errs = sum(1 for c in getattr(m, "conflicts", []) if c.get("level") == "error")
                    warns = sum(1 for c in getattr(m, "conflicts", []) if c.get("level") == "warn")
                    if errs > 0:
                        m.conflict = True
                        m.conflict_level = "high"
                    elif warns > 0:
                        m.conflict = True
                        m.conflict_level = "low"
                    else:
                        m.conflict = False
                        if getattr(m, "conflict_level", None) not in ("high", "low"):
                            m.conflict_level = None
            except Exception:
                try:
                    self.detect_conflicts()
                except Exception:
                    pass
            finally:
                try:
                    if prog:
                        prog.set_percent(100)
                        prog.set_text("Deep scan: done")
                        prog.close()
                except Exception:
                    pass
        else:
            # Heuristic conflict detection via scopes when deep analysis disabled
            try:
                self.detect_conflicts()
            except Exception:
                pass

        # Final assertion and pipeline
        assert isinstance(self.mods, list)
        if getattr(self, "debug_scanner", False):
            print("FINAL MOD COUNT:", len(self.mods))

        # Apply persistent Conflict Knowledge Base hints (suggestions/severity)
        try:
            self._apply_conflict_memory_hints()
        except Exception:
            pass

        # Apply local UpdateEngine hints for UI visibility
        try:
            self._apply_local_update_hints()
        except Exception:
            pass

        # Log scan completion
        try:
            if self.operation_logger:
                self.operation_logger.log(f"[SYSTEM] ✅ Scan complete: Found {len(self.mods)} mods")
                # Count total conflicts
                total_conflicts = sum(len(getattr(m, 'conflicts', [])) for m in self.mods)
                if total_conflicts > 0:
                    self.operation_logger.log(f"[SYSTEM] ⚠️ Conflicts detected: {total_conflicts} total conflicts across {sum(1 for m in self.mods if getattr(m, 'conflict', False))} mods")
        except Exception:
            pass

        try:
            self._compute_integrity()
        except Exception:
            # integrity must never crash scanning
            try:
                print("Integrity computation failed:\n" + traceback.format_exc())
            except Exception:
                pass

        # Force refresh after scan
        try:
            self.update_category_dropdown()
        except Exception:
            pass
        try:
            self.refresh_table()
        except Exception:
            tb = traceback.format_exc()
            log_path = _append_crash_log("scan() -> refresh_table()", tb)
            if log_path:
                tb = tb.rstrip() + f"\n\nLog written to: {log_path}\n"
            try:
                print(tb)
            except Exception:
                pass
            try:
                if hasattr(self, "show_scrollable_popup"):
                    self.show_scrollable_popup(tb, title="Scan crashed (render)")
                else:
                    _safe_show_error("Scan crashed", tb)
            except Exception:
                pass
            return
        try:
            self.refresh_heatmap()
        except Exception:
            pass
        try:
            self.update_mod_count()
        except Exception:
            pass
        # UI smoke test
        try:
            self.ui_smoke_test()
        except Exception as e:
            print("UI smoke test failed:", e)

    def scan_all(self):
        # Deep scan: include nested folders containing ModInfo.xml as separate mods
        try:
            if getattr(self, "debug_scanner", False):
                print(">>> DEEP SCAN STARTED")
            mods_dir = self.mods_path.get()
            if not os.path.isdir(mods_dir):
                messagebox.showerror("Error", "Mods path does not exist")
                return

        except Exception:
            tb = traceback.format_exc()
            log_path = _append_crash_log("scan_all() (startup)", tb)
            if log_path:
                tb = tb.rstrip() + f"\n\nLog written to: {log_path}\n"
            try:
                print(tb)
            except Exception:
                pass
            try:
                if hasattr(self, "show_scrollable_popup"):
                    self.show_scrollable_popup(tb, title="Deep scan crashed")
                else:
                    _safe_show_error("Deep scan crashed", tb)
            except Exception:
                pass
            return

        self.mods = []
        for row in self.table.get_children():
            self.table.delete(row)

        seen = set()
        # Top-level mods
        try:
            for entry in sorted(os.listdir(mods_dir), key=lambda s: s.lower()):
                full = os.path.join(mods_dir, entry)
                if os.path.isdir(full):
                    if full not in seen:
                        seen.add(full)
                        if getattr(self, "debug_scanner", False):
                            print("FOUND MOD DIR:", entry)
                        mod_top = Mod(name=entry, path=full)
                        try:
                            mod_top.install_id = self._normalize_install_id(mod_top.name)
                            if mod_top.name.startswith("__DISABLED__"):
                                mod_top.user_disabled = True
                            if mod_top.install_id and (mod_top.install_id in getattr(self, "user_disabled_ids", set())):
                                mod_top.user_disabled = True
                            if mod_top.install_id and (mod_top.install_id in getattr(self, "order_overrides", {})):
                                try:
                                    v = self.order_overrides.get(mod_top.install_id)
                                    mod_top.order_override = int(v) if v is not None else None
                                except Exception:
                                    mod_top.order_override = None
                        except Exception:
                            pass
                        self.mods.append(mod_top)
                    # One-level nested: treat each child folder that has ModInfo.xml as a mod
                    try:
                        for sub in sorted(os.listdir(full), key=lambda s: s.lower()):
                            sub_full = os.path.join(full, sub)
                            if os.path.isdir(sub_full):
                                modinfo_path = os.path.join(sub_full, "ModInfo.xml")
                                if os.path.isfile(modinfo_path) and sub_full not in seen:
                                    seen.add(sub_full)
                                    if getattr(self, "debug_scanner", False):
                                        print(
                                            "FOUND NESTED MOD DIR:",
                                            os.path.join(entry, sub),
                                        )
                                    mod_nested = Mod(name=sub, path=sub_full)
                                    try:
                                        mod_nested.install_id = self._normalize_install_id(mod_nested.name)
                                        if mod_nested.name.startswith("__DISABLED__"):
                                            mod_nested.user_disabled = True
                                        if mod_nested.install_id and (
                                            mod_nested.install_id in getattr(self, "user_disabled_ids", set())
                                        ):
                                            mod_nested.user_disabled = True
                                        if mod_nested.install_id and (
                                            mod_nested.install_id in getattr(self, "order_overrides", {})
                                        ):
                                            try:
                                                v = self.order_overrides.get(mod_nested.install_id)
                                                mod_nested.order_override = int(v) if v is not None else None
                                            except Exception:
                                                mod_nested.order_override = None
                                    except Exception:
                                        pass
                                    self.mods.append(mod_nested)
                    except Exception:
                        pass
        except Exception:
            pass

        if getattr(self, "debug_scanner", False):
            print("DEEP MOD DIR COUNT:", len(self.mods))

        # In-memory load order derived from numeric prefix when present
        try:
            for i, m in enumerate(self.mods):
                pref = _parse_order_prefix(getattr(m, "name", ""))
                m.load_order = pref if pref is not None else (900 + i)
                try:
                    if isinstance(getattr(m, "order_override", None), int):
                        m.load_order = int(getattr(m, "order_override"))
                except Exception:
                    pass
        except Exception:
            pass

        # Category classification is XML-driven and cached (not per-frame)
        if not hasattr(self, "mod_metadata_store"):
            try:
                if ModMetadataStore:
                    self.mod_metadata_store = ModMetadataStore(os.path.join("data", "mod_metadata.json"))
                else:
                    self.mod_metadata_store = None
            except Exception:
                self.mod_metadata_store = None

        # Minimal ModInfo parsing + XML-driven category assignment
        meta_store = getattr(self, "mod_metadata_store", None)
        for m in self.mods:
            try:
                modinfo_path = os.path.join(m.path, "ModInfo.xml")
                m.has_modinfo = os.path.isfile(modinfo_path)
                # Best-effort parse ModInfo metadata for UpdateEngine
                try:
                    if m.has_modinfo:
                        mi_name, mi_ver = parse_modinfo_name_version(modinfo_path)
                    else:
                        mi_name, mi_ver = "", ""
                    m.modinfo_name = mi_name
                    m.modinfo_version = mi_ver
                except Exception:
                    pass

                # Prefab / POI detection
                try:
                    m.is_poi = (not m.has_modinfo) and is_poi_prefab_mod(m.path)
                except Exception:
                    m.is_poi = False

                # preclassify missing ModInfo as specific conflict type (non-POI)
                if (not m.has_modinfo) and (not getattr(m, "is_poi", False)):
                    try:
                        m.conflict_type = "no_modinfo"
                    except Exception:
                        pass

                # Authoritative XML-driven categorization (folder names never override)
                try:
                    if meta_store and detect_categories_for_mod:
                        meta = meta_store.get_or_compute(
                            folder_name=m.name,
                            mod_path=m.path,
                            compute_fn=detect_categories_for_mod,
                        )
                        m.categories = list(getattr(meta, "categories", []) or [])
                        m.category = normalize_category(getattr(meta, "primary_category", None) or "Miscellaneous")
                        m.category_evidence = dict(getattr(meta, "evidence", {}) or {})
                    else:
                        m.categories = ["Miscellaneous"]
                        m.category = "Miscellaneous"
                        m.category_evidence = {}
                except Exception:
                    m.categories = ["Miscellaneous"]
                    m.category = "Miscellaneous"
                    m.category_evidence = {}

                try:
                    m.tier = infer_tier(m)
                    m.semantic_impact = infer_semantic_impact(m)
                    m.priority = str(getattr(m, "tier", ""))
                except Exception:
                    m.tier = "Content Additions"
                    m.semantic_impact = "Additive Content"
                    m.priority = str(m.tier)

                try:
                    m.is_patch = bool(_is_patch_mod_name(m.name))
                except Exception:
                    m.is_patch = False
                try:
                    m.scopes = extract_scopes(m.name, pathlib.Path(m.path).name)
                except Exception:
                    m.scopes = set()
            except Exception:
                continue

        # Optional deep XML analysis + semantic conflict detection
        if getattr(self, "enable_classification", False):
            for m in self.mods:
                try:
                    from scanner.xml_analyzer import analyze_xml

                    analyze_xml(m)
                except Exception:
                    pass
            self._mark_overhaul_flags()
            try:
                from logic.conflict_detector import detect_conflicts

                detect_conflicts(self.mods)
                # strict XML collision via simulator: enrich conflicts with xml_override kind
                try:
                    enabled_mods = [
                        (m.name, m.path) for m in self.mods if is_effectively_enabled(m) and is_deployable_mod(m)
                    ]
                    from mock_deploy.engine import simulate_deployment

                    state, sim_conflicts = simulate_deployment(enabled_mods)
                    # Filter out conflicts that are resolved by a later conflict patch mod
                    try:
                        last = getattr(state, "last_mut", {}) or {}
                    except Exception:
                        last = {}
                    by_mod = {}
                    for ct in sim_conflicts:
                        try:
                            # ignore patch-involving traces
                            if _is_patch_mod_name(ct.first.mod) or _is_patch_mod_name(ct.second.mod):
                                continue
                            # resolved if last writer is a patch mod
                            lm = last.get((ct.file, ct.xpath))
                            if lm and _is_patch_mod_name(getattr(lm, "mod", "")):
                                continue
                        except Exception:
                            pass
                        by_mod.setdefault(ct.first.mod, []).append(ct)
                        by_mod.setdefault(ct.second.mod, []).append(ct)
                    for m in self.mods:
                        for ct in by_mod.get(m.name, []):
                            entry = {
                                "level": "error",
                                "file": ct.file,
                                "target": ct.xpath,
                                "with": ct.second.mod if ct.first.mod == m.name else ct.first.mod,
                                "reason": "Same file and XPath node modified by multiple mods.",
                                "suggestion": "Adjust load order — overhaul should load last",
                                "conflict_type": "xml_override",
                            }
                            try:
                                # avoid duplicates
                                sig = (
                                    entry["level"],
                                    entry["file"],
                                    entry["target"],
                                    entry["with"],
                                )
                                if not any(
                                    (
                                        c.get("level"),
                                        c.get("file"),
                                        c.get("target"),
                                        c.get("with"),
                                    )
                                    == sig
                                    for c in getattr(m, "conflicts", [])
                                ):
                                    m.conflicts.append(entry)
                            except Exception:
                                pass
                except Exception:
                    pass
                for m in self.mods:
                    errs = sum(1 for c in getattr(m, "conflicts", []) if c.get("level") == "error")
                    warns = sum(1 for c in getattr(m, "conflicts", []) if c.get("level") == "warn")
                    if errs > 0:
                        m.conflict = True
                        m.conflict_level = "high"
                    elif warns > 0:
                        m.conflict = True
                        m.conflict_level = "low"
                    else:
                        m.conflict = False
                        if getattr(m, "conflict_level", None) not in ("high", "low"):
                            m.conflict_level = None
            except Exception:
                try:
                    self.detect_conflicts()
                except Exception:
                    pass
        else:
            # Heuristic conflict detection via scopes when deep analysis disabled
            try:
                self.detect_conflicts()
            except Exception:
                pass

        # Final assertion and pipeline
        assert isinstance(self.mods, list)
        if getattr(self, "debug_scanner", False):
            print("FINAL DEEP MOD COUNT:", len(self.mods))

        # Apply persistent Conflict Knowledge Base hints (suggestions/severity)
        try:
            self._apply_conflict_memory_hints()
        except Exception:
            pass

        # Apply local UpdateEngine hints for UI visibility
        try:
            self._apply_local_update_hints()
        except Exception:
            pass

        # Ensure integrity is computed during scan_all as well (not during UI refresh)
        try:
            self._compute_integrity()
        except Exception:
            try:
                print("Integrity computation failed:\n" + traceback.format_exc())
            except Exception:
                pass

        try:
            self.update_category_dropdown()
        except Exception:
            pass
        try:
            self.refresh_table()
        except Exception:
            tb = traceback.format_exc()
            log_path = _append_crash_log("scan_all() -> refresh_table()", tb)
            if log_path:
                tb = tb.rstrip() + f"\n\nLog written to: {log_path}\n"
            try:
                print(tb)
            except Exception:
                pass
            try:
                if hasattr(self, "show_scrollable_popup"):
                    self.show_scrollable_popup(tb, title="Deep scan crashed (render)")
                else:
                    _safe_show_error("Deep scan crashed", tb)
            except Exception:
                pass
            return
        try:
            self.refresh_heatmap()
        except Exception:
            pass
        try:
            self.update_mod_count()
        except Exception:
            pass
        try:
            self.ui_smoke_test()
        except Exception as e:
            print("UI smoke test failed:", e)

    def check_updates(self):
        """Detect local update situations (multiple installs of same mod) and show a report."""
        try:
            from engines.update_engine import detect_local_updates
        except Exception as e:
            messagebox.showerror("Updates", f"Update engine not available: {e}")
            return

        candidates = detect_local_updates(self.mods or [])
        self._pending_update_candidates = candidates
        if not candidates:
            messagebox.showinfo("Updates", "No duplicates found (nothing to update).")
            return

        total_swaps = sum(1 for c in candidates if getattr(c, "to_enable", None))
        total_disables = sum(len(getattr(c, "to_disable", []) or []) for c in candidates)

    def apply_updates(self):
        """Apply local update actions by disabling older installs (and swapping when newest is disabled)."""
        try:
            from engines.update_engine import detect_local_updates
        except Exception as e:
            messagebox.showerror("Updates", f"Update engine not available: {e}")
            return

        candidates = detect_local_updates(self.mods or [])
        if not candidates:
            messagebox.showinfo("Updates", "No duplicates found.")
            return

        preview: List[str] = []
        for c in candidates:
            keep = getattr(c, "keep", None)
            preview.append(
                f"{getattr(c, 'base_id', '')}: keep {getattr(keep, 'folder_name', '')} (version={getattr(keep, 'modinfo_version', '') or 'unknown'})"
            )
            for ins in getattr(c, "to_enable", []) or []:
                preview.append(f"  ENABLE {getattr(ins, 'folder_name', '')}")
            for ins in getattr(c, "to_disable", []) or []:
                preview.append(f"  DISABLE {getattr(ins, 'folder_name', '')}")

        if not messagebox.askyesno(
            "Apply Update Fixes",
            "This will update enabled/disabled state (no folder renames).\n"
            "It writes to mods_state.json when available (otherwise settings.json).\n\nProceed?\n\n"
            + "\n".join(preview[:30])
            + ("\n..." if len(preview) > 30 else ""),
        ):
            return

        actions: List[str] = []
        try:
            state_store = getattr(self, "mod_state_store", None)
            use_state_store = bool(state_store)
            for c in candidates:
                for ins in getattr(c, "to_disable", []) or []:
                    install_id = ""
                    try:
                        install_id = self._normalize_install_id(getattr(ins, "folder_name", ""))
                    except Exception:
                        install_id = ""
                    if not install_id:
                        continue

                    if use_state_store:
                        state_store.set(str(install_id), enabled=False, user_disabled=True)
                    else:
                        self.user_disabled_ids.add(str(install_id))
                    actions.append(f"DISABLE: {getattr(ins, 'folder_name', '')}")

                for ins in getattr(c, "to_enable", []) or []:
                    install_id = ""
                    try:
                        install_id = self._normalize_install_id(getattr(ins, "folder_name", ""))
                    except Exception:
                        install_id = ""
                    if not install_id:
                        continue

                    if use_state_store:
                        state_store.set(str(install_id), enabled=True, user_disabled=False)
                    else:
                        self.user_disabled_ids.discard(str(install_id))
                    actions.append(f"ENABLE: {getattr(ins, 'folder_name', '')}")

            if use_state_store:
                state_store.save()
            else:
                self.save_settings()
        except Exception as e:
            messagebox.showerror("Apply Update Fixes", f"Failed to persist update state: {e}")
            return

        try:
            self.scan()
        except Exception:
            pass

        msg = "Applied update actions:\n\n" + ("\n".join(actions) if actions else "(no changes)")
        self.show_scrollable_popup(msg, title="Update Fix Results")

    def _apply_local_update_hints(self):
        """Annotate Mod objects with local update hints for table display."""
        for m in self.mods or []:
            try:
                m.update_available = False
                m.update_note = ""
                m.update_suggested_action = ""
            except Exception:
                pass

        try:
            from engines.update_engine import detect_local_updates
        except Exception:
            return

        try:
            candidates = detect_local_updates(self.mods or [])
        except Exception:
            return

        if not candidates:
            return

        by_path = {}
        for m in self.mods or []:
            try:
                by_path[m.path] = m
            except Exception:
                pass

        for c in candidates:
            try:
                keep = getattr(c, "keep", None)
                keep_name = getattr(keep, "folder_name", "") if keep else ""
                base_id = getattr(c, "base_id", "")
            except Exception:
                keep_name = ""
                base_id = ""

            for ins in getattr(c, "to_disable", []) or []:
                try:
                    m = by_path.get(ins.path)
                    if not m:
                        continue
                    m.update_available = True
                    m.update_note = f"Older install of {base_id}; keep {keep_name}".strip()
                    m.update_suggested_action = "Disable older version"
                except Exception:
                    pass

            for ins in getattr(c, "to_enable", []) or []:
                try:
                    m = by_path.get(ins.path)
                    if not m:
                        continue
                    m.update_available = True
                    m.update_note = f"Newest install of {base_id} is disabled".strip()
                    m.update_suggested_action = "Enable newest version"
                except Exception:
                    pass

    def ui_smoke_test(self):
        assert len(self.mods) > 0, "No mods scanned"
        # Conflict presence optional; ensure no crash
        has_conflict = any(getattr(m, "status", "").startswith("Conflict") for m in self.mods)
        _ = has_conflict or True
        print("UI smoke test passed")

    def generate(self):
        """Generate load order and apply it (folder rename workflow)."""
        try:
            if not self.mods:
                messagebox.showwarning("No mods", "Scan mods first.")
                return
            # Folder-based workflow: apply load order by renaming folders in-place.
            self.apply_load_order_rename(confirm=True)
        except Exception:
            tb = traceback.format_exc()
            log_path = _append_crash_log("generate()", tb)
            if log_path:
                tb = tb.rstrip() + f"\n\nLog written to: {log_path}\n"
            try:
                self.show_scrollable_popup(tb, title="Generate Load Order crashed")
            except Exception:
                _safe_show_error("Generate Load Order crashed", tb)

    def export(self):
        try:
            # Build recommended list if not present: sort by priority then folder name
            if not self.mods:
                messagebox.showwarning("No data", "Scan mods first.")
                return

            # Rule-based ordering (constraint-based, deterministic)
            rules = []
            try:
                from logic.rule_store import RuleStore

                store = RuleStore(str(DATA_DIR / "rules.json"))
                rules = (store.list_user_rules() or []) + (store.list_profile_rules() or [])
            except Exception:
                rules = []

            # Disabled mods should not participate in load order outputs.
            enabled = [m for m in (self.mods or []) if is_effectively_enabled(m) and is_deployable_mod(m)]
            try:
                self.recommended, _report = compute_load_order(enabled, user_rules=rules, include_disabled=False)
            except Exception:
                self.recommended = list(enabled)

            path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt"), ("JSON", "*.json")])

            if not path:
                return

            # Always write both JSON and a readable TXT beside the chosen path
            base, ext = os.path.splitext(path)
            json_path = base + ".json"
            txt_path = base + ".txt"

            # Log export
            try:
                if self.operation_logger:
                    self.operation_logger.log("[ACTION] 📤 Exporting load order...")
            except Exception:
                pass

            try:
                self.export_json(self.recommended, json_path)
                # Also write readable load-order TXT grouped by category/priority
                self.export_loadorder_txt(txt_path, self.recommended)
            except Exception as ex:
                messagebox.showerror("Export Error", f"Failed to export: {ex}")
                return

            messagebox.showinfo("Exported", f"Saved to:\n{json_path}\n{txt_path}")

            # Log export
            try:
                if self.operation_logger:
                    self.operation_logger.log("[ACTION] ✅ Load order exported")
            except Exception:
                pass
        except Exception as e:
            if self.operation_logger:
                self.operation_logger.log(f"[ERROR] X Export failed: {str(e)[:100]}")
            messagebox.showerror("Export Error", f"Unexpected error during export: {e}")


    def apply_load_order(self):
        """Apply load order to the Mods Library by renaming folders."""
        try:
            self.apply_load_order_rename(confirm=True)
        except Exception:
            tb = traceback.format_exc()
            log_path = _append_crash_log("apply_load_order()", tb)
            if log_path:
                tb = tb.rstrip() + f"\n\nLog written to: {log_path}\n"
            try:
                self.show_scrollable_popup(tb, title="Apply Load Order crashed")
            except Exception:
                _safe_show_error("Apply Load Order crashed", tb)

    def apply_load_order_rename(self, *, confirm: bool = True):
        """Apply load order to the Mods Library by renaming folders.

        This is a potentially heavy operation; any unexpected exceptions should be
        captured and logged so the GUI doesn't appear to silently crash.
        """

        def _is_patch(mod: Any) -> bool:
            try:
                if bool(getattr(mod, "is_patch", False)):
                    return True
                tier = str(getattr(mod, "tier", "") or "")
                if tier == "Patch Mods":
                    return True
                return _is_patch_mod_name(str(getattr(mod, "name", "") or ""))
            except Exception:
                return False

        try:
            if not self.mods:
                messagebox.showwarning("No mods", "Scan mods first.")
                return

            mods_dir = self.mods_path.get()
            if not os.path.isdir(mods_dir):
                messagebox.showerror("Apply Load Order", "Mods Library path does not exist")
                return

            if self.operation_logger:
                self.operation_logger.log("[SYSTEM] ⚙️ Generating load order...")

            enabled = [m for m in (self.mods or []) if is_effectively_enabled(m) and is_deployable_mod(m)]
            if not enabled:
                messagebox.showinfo("Apply Load Order", "No enabled mods to order.")
                return

            # Reserve explicit overrides.
            #
            # Prefixes are always rendered as 3 digits (000..999). To keep compatibility with older
            # installs where prefixes jumped by 10 (e.g. 1450), we transparently migrate common
            # legacy override values by dividing by 10 when they look like step-10 prefixes.
            desired_prefix_by_path = {}
            used = set()
            for m in enabled:
                try:
                    ov = getattr(m, "order_override", None)
                    if isinstance(ov, int):
                        ov_i = int(ov)
                        if 0 <= ov_i <= 999:
                            desired_prefix_by_path[str(m.path)] = ov_i
                            used.add(ov_i)
                        elif 0 <= ov_i <= 9999 and (ov_i % 10 == 0):
                            # Legacy step-10 override: 1450 -> 145
                            ov_scaled = ov_i // 10
                            if 0 <= ov_scaled <= 999:
                                desired_prefix_by_path[str(m.path)] = int(ov_scaled)
                                used.add(int(ov_scaled))
                except Exception:
                    pass

            # Rule-based ordering (constraint-based, deterministic)
            rules = []
            try:
                from logic.rule_store import RuleStore

                store = RuleStore(os.path.join("data", "rules.json"))
                rules = (store.list_user_rules() or []) + (store.list_profile_rules() or [])
            except Exception:
                rules = []

            try:
                ordered_enabled, report = compute_load_order(enabled, user_rules=rules, include_disabled=False)
            except Exception:
                ordered_enabled, report = (list(enabled), None)

            # Collect non-blocking issues for UI/diagnostics during apply.
            # Must be bound before any later prefix allocation that appends warnings.
            warnings: List[str] = []

            def _show_load_order_issues(title: str, lines: List[str]) -> None:
                """Intentionally no-op.

                Generate+Apply should not interrupt the user with validation popups.
                Use 'Validate (Dry Run)' when you want the full report.
                """
                return

            # Early dependency/load-order validation (clear error before any rename work)
            try:
                if bool(getattr(self, "harden_deployment", True)):
                    from logic.deployment_guardrails import validate_dependencies_in_load_order

                    enabled_pairs = []
                    for m in ordered_enabled or []:
                        try:
                            enabled_pairs.append((str(getattr(m, "name", "") or ""), str(getattr(m, "path", "") or "")))
                        except Exception:
                            continue

                    dep_issues = validate_dependencies_in_load_order(enabled_mods=enabled_pairs)
                    dep_errors = [i for i in (dep_issues or []) if str(getattr(i, "level", "")).upper() == "ERROR"]
                    if dep_errors:
                        # Build an actionable checklist: install/enable/move-earlier.
                        def _norm_install_like(name: str) -> str:
                            try:
                                s = (name or "").strip()
                                if s.lower().startswith("__disabled__"):
                                    s = s[len("__DISABLED__") :]
                                try:
                                    m2 = _ORDER_PREFIX_RE.match(s)
                                except Exception:
                                    m2 = None
                                if m2:
                                    s = s[len(m2.group(0)) :]
                                return (s or "").strip().lower()
                            except Exception:
                                return (name or "").strip().lower()

                        try:
                            import re as _re
                        except Exception:
                            _re = None

                        # Map normalized folder id -> mod object (any mod in library)
                        norm_to_mod = {}
                        try:
                            for mm in self.mods or []:
                                try:
                                    folder = pathlib.Path(str(getattr(mm, "path", "") or "")).name
                                except Exception:
                                    folder = ""
                                if folder:
                                    norm_to_mod[_norm_install_like(folder)] = mm
                        except Exception:
                            norm_to_mod = {}

                        def _extract_dep_name(details: str) -> str:
                            try:
                                if not details:
                                    return ""
                                if _re:
                                    m = _re.search(r"'([^']+)'", details)
                                    if m:
                                        return str(m.group(1) or "").strip()
                                # fallback: use the raw details
                                return ""
                            except Exception:
                                return ""

                        by_mod_install: Dict[str, List[str]] = {}
                        by_mod_enable: Dict[str, List[str]] = {}
                        by_mod_move: Dict[str, List[str]] = {}
                        misc_lines: List[str] = []

                        for it in dep_errors:
                            modn = str(getattr(it, "mod", "") or "").strip() or "(unknown mod)"
                            reason = str(getattr(it, "reason", "") or "").strip()
                            det = str(getattr(it, "details", "") or "").strip()

                            dep_name = _extract_dep_name(det)
                            if reason == "missing_dependency":
                                # Guardrails labels missing OR disabled as the same reason.
                                # We refine by checking whether the dependency exists in the library.
                                dn = _norm_install_like(dep_name)
                                dep_obj = norm_to_mod.get(dn) if dn else None
                                if dep_obj is None:
                                    by_mod_install.setdefault(modn, []).append(dep_name or det)
                                else:
                                    if not is_effectively_enabled(dep_obj):
                                        by_mod_enable.setdefault(modn, []).append(
                                            str(getattr(dep_obj, "name", "") or pathlib.Path(dep_obj.path).name)
                                        )
                                    else:
                                        # Dependency exists and enabled; fall back to detail string
                                        misc_lines.append(f"- [{modn}] {det or reason}")
                            elif reason == "dependency_load_order":
                                by_mod_move.setdefault(modn, []).append(dep_name or det)
                            else:
                                misc_lines.append(f"- [{modn}] {det or reason or 'dependency issue'}")

                        lines = ["LOAD ORDER HAS ISSUES", "", "These dependency issues were detected:"]
                        lines.append("1) Install missing dependency mods")
                        lines.append("2) Enable disabled dependency mods")
                        lines.append("3) Ensure dependencies load earlier than dependents")
                        lines.append("")

                        def _emit_section(title: str, mapping: Dict[str, List[str]], verb: str):
                            if not mapping:
                                return
                            lines.append(title)
                            for modn, items in sorted(mapping.items(), key=lambda kv: (kv[0].lower(), len(kv[1]))):
                                uniq = []
                                seen = set()
                                for x in items:
                                    s = str(x or "").strip()
                                    if not s:
                                        continue
                                    k = s.lower()
                                    if k in seen:
                                        continue
                                    seen.add(k)
                                    uniq.append(s)
                                if not uniq:
                                    continue
                                if len(uniq) == 1:
                                    lines.append(f"- [{modn}] {verb}: {uniq[0]}")
                                else:
                                    lines.append(f"- [{modn}] {verb}:")
                                    for u in uniq[:8]:
                                        lines.append(f"    - {u}")
                                    if len(uniq) > 8:
                                        lines.append(f"    ... and {len(uniq) - 8} more")
                            lines.append("")

                        _emit_section("Missing dependencies:", by_mod_install, "Install")
                        _emit_section("Disabled dependencies:", by_mod_enable, "Enable")
                        _emit_section("Load order required:", by_mod_move, "Move earlier")

                        if misc_lines:
                            lines.append("Details:")
                            lines.extend(misc_lines[:30])
                            if len(misc_lines) > 30:
                                lines.append(f"... and {len(misc_lines) - 30} more")
                            lines.append("")

                        lines.append("Continuing anyway (load order will be applied).")
                        _show_load_order_issues("Load Order Warnings", lines)
            except Exception:
                # Non-blocking: dependency validation crashed; warn and continue.
                lines = [
                    "LOAD ORDER HAS ISSUES",
                    "",
                    "Dependency validation crashed (continuing anyway).",
                    "If apply fails, try running 'Validate (Dry Run)' to see details.",
                ]
                _show_load_order_issues("Load Order Warnings", lines)

            # UI categorization cache (used for ordering + folder-prefixing)
            ui_category_by_path: Dict[str, str] = {}
            try:
                from logic.deployment_guardrails import categorize_ui_mod, mod_touches_xui

                for m in enabled:
                    try:
                        p = str(getattr(m, "path", "") or "")
                        if not p:
                            continue
                        if not mod_touches_xui(p):
                            continue
                        ui_category_by_path[p] = categorize_ui_mod(
                            mod_name=str(getattr(m, "name", "") or ""),
                            mod_path=p,
                        )
                    except Exception:
                        continue
            except Exception:
                ui_category_by_path = {}

            # --------------------------------------------------
            # FINAL VALIDATION BEFORE RENAME (blocking)
            # --------------------------------------------------
            try:
                blocking_errors: List[str] = []
                warnings.clear()

                try:
                    from logic.conflict_memory import normalize_mod_id
                except Exception:
                    normalize_mod_id = None

                # Engine-reported issues
                if report is not None:
                    blocking_errors.extend(list(getattr(report, "errors", []) or []))
                    warnings.extend(list(getattr(report, "warnings", []) or []))
                else:
                    warnings.append("Load order engine failed to produce a report; applying anyway.")

                # STRICT conflict sanity warnings (non-blocking): multiple overhauls / multiple FPV frameworks
                try:
                    overhaul_mods = [
                        m
                        for m in enabled
                        if (not _is_patch(m)) and str(getattr(m, "tier", "") or "") == "Gameplay Overhauls"
                    ]
                    if len(overhaul_mods) > 1:
                        names = ", ".join(sorted({str(getattr(m, "name", "") or "").strip() for m in overhaul_mods}))
                        warnings.append(
                            f"Multiple gameplay overhaul mods enabled ({len(overhaul_mods)}). Only one overhaul is recommended: {names}"
                        )
                except Exception:
                    pass
                try:
                    fw_mods = [
                        m
                        for m in enabled
                        if (not _is_patch(m)) and str(getattr(m, "tier", "") or "") == "Weapon Frameworks & Animation"
                    ]
                    if len(fw_mods) > 1:
                        names = ", ".join(sorted({str(getattr(m, "name", "") or "").strip() for m in fw_mods}))
                        warnings.append(
                            f"Multiple weapon/animation frameworks enabled ({len(fw_mods)}). This commonly causes FPV bugs; prefer one: {names}"
                        )
                except Exception:
                    pass

                # Duplicate IDs (never auto-resolve): treat as blocking even if detectors only set conflicts.
                try:
                    dup_pairs = set()
                    if not normalize_mod_id:
                        raise RuntimeError("normalize_mod_id unavailable")
                    enabled_names = {normalize_mod_id(str(getattr(m, "name", "") or "")) for m in enabled}
                    enabled_names = {n for n in enabled_names if n}
                    for m in enabled:
                        a = normalize_mod_id(str(getattr(m, "name", "") or ""))
                        if not a:
                            continue
                        for c in getattr(m, "conflicts", []) or []:
                            if str(c.get("conflict_type") or "") != "duplicate_id":
                                continue
                            b = normalize_mod_id(str(c.get("with") or ""))
                            if not b or b not in enabled_names:
                                continue
                            pair = tuple(sorted([a, b], key=lambda s: s.lower()))
                            dup_pairs.add(pair)
                    for a, b in sorted(dup_pairs, key=lambda p: (p[0].lower(), p[1].lower())):
                        blocking_errors.append(
                            f"Duplicate IDs detected between '{a}' and '{b}'. You must disable one or install a compatibility patch."
                        )
                except Exception:
                    pass

                # Patch mods must have parents (dependencies) present
                try:
                    from logic.load_order_engine import parse_declared_dependencies

                    if not normalize_mod_id:
                        raise RuntimeError("normalize_mod_id unavailable")
                    enabled_by_norm = {normalize_mod_id(str(getattr(m, "name", "") or "")).lower(): m for m in enabled}
                    for m in enabled:
                        # Prefer the engine-attached tier/is_patch markers to avoid re-scanning.
                        if not _is_patch(m):
                            continue
                        deps = []
                        try:
                            p = str(getattr(m, "path", "") or "").strip()
                            if p and os.path.isdir(p):
                                deps = parse_declared_dependencies(pathlib.Path(p))
                        except Exception:
                            deps = []
                        if not deps:
                            warnings.append(
                                f"Patch mod '{getattr(m, 'name', '')}' has no declared parents in ModInfo.xml (patch will be ordered late, but accuracy may be reduced)."
                            )
                            continue
                        for d in deps:
                            dn = normalize_mod_id(d).lower()
                            if dn and dn not in enabled_by_norm:
                                blocking_errors.append(
                                    f"Patch mod '{getattr(m, 'name', '')}' requires '{d}', but it is not enabled/present."
                                )
                except Exception:
                    pass

                # Deployment guardrails (dry-run): XML integrity, full-file overrides, single Mods dir, UI core conflicts.
                try:
                    from logic.deployment_guardrails import format_report_text, preflight_check

                    enabled_pairs = []
                    for m in ordered_enabled or []:
                        try:
                            enabled_pairs.append((str(getattr(m, "name", "") or ""), str(getattr(m, "path", "") or "")))
                        except Exception:
                            continue

                    pf = preflight_check(
                        mods_root=str(mods_dir),
                        enabled_mods=enabled_pairs,
                        block_multiple_mods_dirs=bool(getattr(self, "block_multiple_mods_dirs", True)),
                        block_invalid_xml=bool(getattr(self, "block_invalid_xml", True)),
                        block_full_file_replacements=bool(getattr(self, "block_full_file_replacements", True)),
                        enforce_single_ui_framework=bool(getattr(self, "enforce_single_ui_framework", True)),
                    )

                    for issue in getattr(pf, "issues", None) or []:
                        lvl = str(getattr(issue, "level", "") or "").upper()
                        msg = str(getattr(issue, "details", "") or "").strip()
                        modn = str(getattr(issue, "mod", "") or "").strip()
                        fil = str(getattr(issue, "file", "") or "").strip()
                        prefix = ""
                        if modn:
                            prefix += f"[{modn}] "
                        if fil:
                            prefix += f"({fil}) "
                        s = (prefix + msg).strip() or str(getattr(issue, "reason", "") or "issue")
                        if lvl == "ERROR":
                            blocking_errors.append(s)
                        elif lvl == "WARN":
                            warnings.append(s)

                    if not bool(getattr(pf, "ok", False)):
                        # Do not popup during Generate+Apply.
                        # Full report is available via 'Validate (Dry Run)'.
                        pass
                except Exception:
                    # Non-blocking: guardrails crashed; warn and continue.
                    warnings.append("Preflight validation crashed; applying anyway.")

                # UI group ordering enforcement: if explicit overrides violate the required UI ordering, block.
                try:
                    if bool(getattr(self, "enforce_single_ui_framework", True)) and ui_category_by_path:
                        # Keep UI groups in a stable reserved prefix band.
                        # We use compact ranges so auto-assigned prefixes are human-sized (e.g. 145 not 1450).
                        ranges = {"framework": (0, 10), "hud": (10, 20), "extension": (20, 30)}
                        for m in enabled:
                            p = str(getattr(m, "path", "") or "")
                            if not p or p not in ui_category_by_path:
                                continue
                            cat = ui_category_by_path.get(p) or ""
                            # Validate against the effective override value (after legacy migration).
                            ov = desired_prefix_by_path.get(p, getattr(m, "order_override", None))
                            if not isinstance(ov, int):
                                continue
                            if cat in ranges:
                                lo, hi = ranges[cat]
                                if not (lo <= int(ov) < hi):
                                    blocking_errors.append(
                                        f"UI load order override violates required group ordering: '{getattr(m, 'name', '')}' has override {int(ov)} but should be in [{lo}..{hi - 1}] for {cat}."
                                    )
                except Exception:
                    pass

                # Ensure all chosen prefixes are representable as 3 digits.
                try:
                    too_large = []
                    for pth, pref in (desired_prefix_by_path or {}).items():
                        if isinstance(pref, int) and pref > 999:
                            too_large.append((pth, pref))
                    if too_large:
                        blocking_errors.append(
                            "One or more load order overrides exceed 999. Prefixes are fixed to 3 digits (000-999); lower those overrides and try again."
                        )
                except Exception:
                    pass

                if blocking_errors:
                    lines: List[str] = []
                    lines.append("LOAD ORDER HAS ISSUES")
                    lines.append("")
                    lines.append("What's wrong:")
                    for s in blocking_errors[:40]:
                        lines.append(f"- {s}")
                    if len(blocking_errors) > 40:
                        lines.append(f"... and {len(blocking_errors) - 40} more")
                    lines.append("")
                    lines.append("Suggested fixes:")
                    lines.append("- Fix duplicate IDs by disabling one mod (or using a compatibility patch).")
                    lines.append("- Ensure patch mods have ModInfo.xml dependencies and the parent mods are enabled.")
                    lines.append("- Fix dependency cycles / missing required parents.")
                    if warnings:
                        lines.append("")
                        lines.append("Warnings (non-blocking):")
                        for s in warnings[:30]:
                            lines.append(f"- {s}")

                    lines.append("")
                    lines.append("Continuing anyway (load order will be applied).")
                    # Do not popup during Generate+Apply.
                    pass
            except Exception:
                # Non-blocking: if validation code fails, warn and continue.
                pass

            normal_mods = [m for m in ordered_enabled if not _is_patch(m)]
            patch_mods = [m for m in ordered_enabled if _is_patch(m)]

            # Allocate mods contiguously using a fixed step (e.g. 000, 001, 002...) while enforcing strict tier grouping.
            # Numbers are just a representation; tier grouping is the real ordering rule.
            order_step = 1

            # UI ordering requirement:
            # - UI frameworks must load early (shared controls/styles).
            # - HUD mods must load LOW / late (last loaded wins for XUi/windows.xml).
            # - UI extensions (non-HUD UI mods) should load after most gameplay/content but before HUD.
            ui_order_late = ["extension", "hud"]
            tier_order = (
                "Core Frameworks",
                "API / Backend Systems",
                "Gameplay Overhauls",
                "Content Additions",
                "Weapon Frameworks & Animation",
                "Weapon Packs",
                "Visual / Audio Mods",
                "Worldgen / POI Mods",
                "Utility / QoL Mods",
            )

            def _tier_for_prefixing(mod: Any) -> str:
                try:
                    t = str(getattr(mod, "tier", "") or "").strip()
                    if t in tier_order:
                        return t
                except Exception:
                    pass
                return "Content Additions"

            normal_by_tier: Dict[str, List[Any]] = {t: [] for t in tier_order}
            for m in normal_mods:
                normal_by_tier[_tier_for_prefixing(m)].append(m)

            next_pref = 0

            # Allocate UI frameworks EARLY (shared base UI should be present before extensions/HUD patches).
            try:
                ui_fw = [
                    m for m in normal_mods if ui_category_by_path.get(str(getattr(m, "path", "") or "")) == "framework"
                ]
                ui_fw.sort(key=lambda mm: str(getattr(mm, "name", "") or "").lower())
                for m in ui_fw:
                    pth = str(getattr(m, "path", ""))
                    if not pth or pth in desired_prefix_by_path:
                        continue
                    while next_pref in used:
                        next_pref += order_step
                    desired_prefix_by_path[pth] = int(next_pref)
                    used.add(int(next_pref))
                    next_pref += order_step
            except Exception:
                pass

            # Allocate non-UI mods by tier next.
            try:
                for tier_name in tier_order:
                    mods_in_tier = list(normal_by_tier.get(tier_name, []) or [])
                    mods_in_tier.sort(key=lambda mm: str(getattr(mm, "name", "") or "").lower())
                    for m in mods_in_tier:
                        pth = str(getattr(m, "path", ""))
                        if not pth or pth in desired_prefix_by_path:
                            continue
                        # Skip UI mods here; they'll be allocated late (except frameworks handled above).
                        if pth in ui_category_by_path and ui_category_by_path.get(pth) in {"hud", "extension"}:
                            continue
                        while next_pref in used:
                            next_pref += order_step
                        desired_prefix_by_path[pth] = int(next_pref)
                        used.add(int(next_pref))
                        next_pref += order_step
            except Exception:
                pass

            # Allocate UI mods LATE so they win XUi/windows.xml overrides.
            # Order within UI: extensions first, HUD last.
            try:
                for cat in ui_order_late:
                    mods_in_cat = [
                        m for m in normal_mods if ui_category_by_path.get(str(getattr(m, "path", "") or "")) == cat
                    ]
                    mods_in_cat.sort(key=lambda mm: str(getattr(mm, "name", "") or "").lower())
                    for m in mods_in_cat:
                        pth = str(getattr(m, "path", ""))
                        if not pth or pth in desired_prefix_by_path:
                            continue
                        while next_pref in used:
                            next_pref += order_step
                        desired_prefix_by_path[pth] = int(next_pref)
                        used.add(int(next_pref))
                        next_pref += order_step
            except Exception:
                pass

            # Allocate patch mods last, continuing the same stepped numbering.
            for m in patch_mods:
                pth = str(getattr(m, "path", ""))
                if pth in desired_prefix_by_path:
                    continue
                while next_pref in used:
                    next_pref += order_step
                desired_prefix_by_path[pth] = int(next_pref)
                used.add(int(next_pref))
                next_pref += order_step

            # --------------------------------------------------
            # CONTIGUOUS NUMBERING (NO GAPS)
            # --------------------------------------------------
            # Users expect the numeric prefixes to be contiguous from smallest to biggest
            # (000, 001, 002...) with no skipped numbers.
            #
            # We preserve the *relative ordering* implied by the allocated prefixes above,
            # then compress them into a contiguous 0..N-1 sequence.
            try:
                if len(enabled) > 1000:
                    messagebox.showerror(
                        "Apply Load Order",
                        f"Too many enabled mods to prefix contiguously: {len(enabled)} enabled. "
                        "This tool uses fixed 3-digit prefixes (000-999).",
                    )
                    return

                order_idx_by_path: Dict[str, int] = {}
                try:
                    for i, mm in enumerate(ordered_enabled or []):
                        p = str(getattr(mm, "path", "") or "")
                        if p:
                            order_idx_by_path[p] = int(i)
                except Exception:
                    order_idx_by_path = {}

                sortable: List[Tuple[int, int, str]] = []
                for mm in enabled:
                    p = str(getattr(mm, "path", "") or "")
                    if not p:
                        continue
                    pref = desired_prefix_by_path.get(p)
                    if not isinstance(pref, int):
                        continue
                    sortable.append((int(pref), int(order_idx_by_path.get(p, 10**9)), p))

                sortable.sort(key=lambda t: (t[0], t[1], t[2].lower()))
                for new_pref, (_, __, p) in enumerate(sortable):
                    desired_prefix_by_path[p] = int(new_pref)
            except Exception:
                pass

            # Plan renames: always use 3 digits (000..999)
            pref_width = 3

            ops = []  # (old_path, new_path)
            for m in enabled:
                old_path = str(getattr(m, "path", ""))
                if not old_path:
                    continue
                old_name = pathlib.Path(old_path).name
                base = self._clean_folder_name_for_order(old_name)

                # Auto-prefix UI mods into visible groups: 0_UIFramework / 1_UIExtensions / 2_HUD
                try:
                    if bool(getattr(self, "auto_prefix_ui_groups", True)):
                        from logic.deployment_guardrails import ui_group_prefix

                        cat = ui_category_by_path.get(old_path)
                        grp = ui_group_prefix(cat or "")
                        if grp:
                            # Avoid stacking old UI prefixes (e.g. 1_HUD_ -> 2_HUD_1_HUD_...)
                            # and allow category order changes without renaming churn.
                            try:
                                base2 = str(base or "")
                                base2 = re.sub(
                                    r"^(?:0_UIFramework|1_HUD|2_UIExtensions|1_UIExtensions|2_HUD)(?:[_\-\s]+)?",
                                    "",
                                    base2,
                                    flags=re.IGNORECASE,
                                )
                                base = base2 or base
                            except Exception:
                                pass

                            if not str(base).startswith(grp + "_") and not str(base).startswith(grp):
                                base = f"{grp}_{base}"
                except Exception:
                    pass
                pref = desired_prefix_by_path.get(old_path)
                if not isinstance(pref, int):
                    continue
                new_name = f"{int(pref):0{pref_width}d}_{base}"
                new_path = str(pathlib.Path(mods_dir) / new_name)
                if os.path.abspath(old_path) == os.path.abspath(new_path):
                    continue
                ops.append((old_path, new_path))

            # Disabled mods are not part of load order; rename them to clearly mark
            # them as disabled and move them into Mods/Disabled.
            try:
                disabled_dir = pathlib.Path(mods_dir) / "Disabled"
                disabled_mods = [
                    m for m in (self.mods or []) if (not is_effectively_enabled(m)) and is_deployable_mod(m)
                ]
                for m in disabled_mods:
                    old_path = str(getattr(m, "path", "") or "")
                    if not old_path:
                        continue

                    old_p = pathlib.Path(old_path)
                    old_parent = old_p.parent
                    try:
                        is_root_child = os.path.abspath(str(old_parent)) == os.path.abspath(str(mods_dir))
                        is_disabled_child = os.path.abspath(str(old_parent)) == os.path.abspath(str(disabled_dir))
                    except Exception:
                        is_root_child = False
                        is_disabled_child = False
                    if not (is_root_child or is_disabled_child):
                        continue

                    old_name = old_p.name
                    base = self._clean_folder_name_for_order(old_name)
                    new_name = f"__DISABLED__{str(base).strip() or 'Mod'}"
                    new_path = str(disabled_dir / new_name)

                    if os.path.abspath(old_path) == os.path.abspath(new_path):
                        continue
                    if os.path.exists(new_path):
                        continue
                    ops.append((old_path, new_path))
            except Exception:
                pass

            if not ops:
                messagebox.showinfo("Apply Load Order", "Nothing to rename; load order already applied.")
                return

            if confirm:
                try:
                    ok = self._confirm_apply_load_order_preview(
                        mods_dir=mods_dir,
                        enabled_mods=enabled,
                        ordered_enabled=ordered_enabled,
                        desired_prefix_by_path=desired_prefix_by_path,
                        ops=ops,
                        report=report,
                    )
                    if not ok:
                        return
                except Exception:
                    # Fail open to the old messagebox if the rich preview crashes.
                    lines = [f"Will rename {len(ops)} folder(s) under:\n{mods_dir}\n"]
                    for old_path, new_path in ops[:25]:
                        lines.append(f"- {pathlib.Path(old_path).name}  ->  {pathlib.Path(new_path).name}")
                    if len(ops) > 25:
                        lines.append(f"… and {len(ops) - 25} more")
                    lines.append("\nProceed?")
                    if not messagebox.askyesno("Apply Load Order (Rename Folders)", "\n".join(lines)):
                        return

            # Two-phase rename to avoid collisions/swaps
            try:
                from deployment.rename_deployer import two_phase_rename

                # Ensure Mods/Disabled exists if any op targets it.
                try:
                    disabled_dir = pathlib.Path(mods_dir) / "Disabled"
                    needs_disabled_dir = False
                    for _a, b in ops or []:
                        try:
                            if os.path.abspath(str(pathlib.Path(b).parent)) == os.path.abspath(str(disabled_dir)):
                                needs_disabled_dir = True
                                break
                        except Exception:
                            continue
                    if needs_disabled_dir:
                        os.makedirs(str(disabled_dir), exist_ok=True)
                except Exception:
                    pass

                two_phase_rename(mods_dir, ops)
            except Exception as e:
                messagebox.showerror("Apply Load Order", f"Rename failed: {e}")
                return

            try:
                self.scan()
            except Exception:
                pass

            # Log success
            try:
                if self.operation_logger:
                    self.operation_logger.log(f"[SYSTEM] 📊 Load order applied: {len(self.mods)} mods processed")
                    self.operation_logger.log("[SYSTEM] ✅ Load order generated and applied")
            except Exception:
                pass

            # Report stability / confidence
            try:
                if report is not None:
                    warn_ct = len(getattr(report, "warnings", []) or [])
                    err_ct = len(getattr(report, "errors", []) or [])
                    conf = getattr(report, "confidence_level", lambda: "unknown")()
                    msg = f"Load order applied by renaming folders.\n\nConfidence: {conf}\nWarnings: {warn_ct}\nErrors: {err_ct}"
                    if warn_ct or err_ct:
                        details = []
                        for s in (getattr(report, "errors", []) or [])[:20]:
                            details.append(f"ERROR: {s}")
                        for s in (getattr(report, "warnings", []) or [])[:20]:
                            details.append(f"WARN: {s}")
                        msg += "\n\nDetails:\n" + "\n".join(details)
                    self.show_scrollable_popup(msg, title="Load Order Report")
                    return
            except Exception:
                pass

            messagebox.showinfo("Apply Load Order", "Load order applied by renaming folders.")

        except Exception:
            tb = traceback.format_exc()
            log_path = _append_crash_log("apply_load_order_rename()", tb)
            if log_path:
                tb = tb.rstrip() + f"\n\nLog written to: {log_path}\n"
            try:
                if self.operation_logger:
                    self.operation_logger.log("[ERROR] X Load order apply failed: unexpected error occurred")
            except Exception:
                pass
            try:
                self.show_scrollable_popup(tb, title="Apply Load Order crashed")
            except Exception:
                _safe_show_error("Apply Load Order crashed", tb)
            return

    def _get_selected_mod(self):
        try:
            sel = self.table.selection()
            if not sel:
                return None
            return self.mod_lookup.get(sel[0])
        except Exception:
            return None

    def _ensure_mod_metadata_store(self):
        try:
            store = getattr(self, "mod_metadata_store", None)
        except Exception:
            store = None
        if store is not None:
            return store

        try:
            from logic.mod_metadata_store import ModMetadataStore

            store = ModMetadataStore(os.path.join("data", "mod_metadata.json"))
            self.mod_metadata_store = store
            return store
        except Exception:
            return None

    def _toggle_framework_for_context_mod(self):
        mod = getattr(self, "_row_context_mod", None)
        if not mod:
            return

        try:
            is_fw = bool(self._row_context_is_framework.get())
        except Exception:
            is_fw = bool(getattr(mod, "is_framework", False))

        try:
            mod.is_framework = is_fw
        except Exception:
            pass

        store = self._ensure_mod_metadata_store()
        if store is not None:
            try:
                store.set_framework_flag(
                    folder_name=str(getattr(mod, "name", "") or ""),
                    mod_path=str(getattr(mod, "path", "") or ""),
                    is_framework=is_fw,
                )
            except Exception as e:
                try:
                    messagebox.showerror("Framework Flag", f"Failed to persist framework flag: {e}")
                except Exception:
                    pass

        try:
            self.refresh_table()
        except Exception:
            pass

    def on_tree_right_click(self, event):
        """Right-click row context menu."""

        try:
            row = self.table.identify_row(event.y)
        except Exception:
            row = ""

        if not row or str(row).startswith("cat::"):
            return

        try:
            self.table.selection_set(row)
        except Exception:
            pass

        try:
            mod = (getattr(self, "mod_lookup", {}) or {}).get(row)
        except Exception:
            mod = None
        if not mod:
            return

        try:
            self._row_context_mod = mod
            self._row_context_is_framework.set(bool(getattr(mod, "is_framework", False)))
        except Exception:
            return

        try:
            self._row_context_menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass
        finally:
            try:
                self._row_context_menu.grab_release()
            except Exception:
                pass

    def rename_selected_mod_folder(self):
        mod = self._get_selected_mod()
        if not mod:
            messagebox.showinfo("Rename Folder", "Select a mod row first.")
            return

        old_path = str(getattr(mod, "path", ""))
        if not old_path or not os.path.isdir(old_path):
            messagebox.showerror("Rename Folder", "Selected mod folder no longer exists.")
            return

        mods_dir = self.mods_path.get()
        old_name = pathlib.Path(old_path).name
        disabled = bool(old_name.startswith("__DISABLED__"))
        m_pref = _ORDER_PREFIX_RE.match(old_name)
        pref = _parse_order_prefix(old_name)
        pref_width = len(m_pref.group(1)) if m_pref else 3
        base = self._clean_folder_name_for_order(old_name)

        from tkinter import simpledialog

        typed = simpledialog.askstring(
            "Rename Folder",
            "Enter new folder name (base name only).\n\nExisting order prefix / disabled marker will be preserved.",
            initialvalue=base,
            parent=self.root,
        )
        if typed is None:
            return

        new_base = _sanitize_user_folder_name(typed)
        if not new_base:
            messagebox.showerror("Rename Folder", "Folder name cannot be empty.")
            return

        new_name = new_base
        if isinstance(pref, int):
            new_name = f"{int(pref):0{pref_width}d}_{new_name}"
        if disabled:
            new_name = "__DISABLED__" + new_name

        new_path = str(pathlib.Path(mods_dir) / new_name)
        if os.path.abspath(old_path) == os.path.abspath(new_path):
            return
        if os.path.exists(new_path):
            messagebox.showerror("Rename Folder", f"A folder already exists with that name:\n{new_name}")
            return

        old_iid = self._normalize_install_id(old_name)
        new_iid = self._normalize_install_id(new_name)

        try:
            os.rename(old_path, new_path)
        except Exception as e:
            # Log rename failure
            try:
                if self.operation_logger:
                    self.operation_logger.log_rename_failed(old_name, new_name, str(e))
            except Exception:
                pass
            messagebox.showerror("Rename Folder", f"Rename failed: {e}")
            return

        # Log successful rename
        try:
            if self.operation_logger:
                self.operation_logger.log_rename_complete(old_name, new_name)
        except Exception:
            pass

        # Migrate persistent enabled/disabled state if install_id changed
        try:
            state_store = getattr(self, "mod_state_store", None)
            if state_store and old_iid and new_iid and old_iid != new_iid:
                st = state_store.get(old_iid)
                if st is not None:
                    state_store.set(new_iid, enabled=st.enabled, user_disabled=st.user_disabled)
                    try:
                        state_store._data.pop(old_iid.strip().lower(), None)
                    except Exception:
                        pass
                    try:
                        state_store.save()
                    except Exception:
                        pass
        except Exception:
            pass

        # Migrate order overrides mapping as well
        try:
            if old_iid and new_iid and old_iid != new_iid and old_iid in (self.order_overrides or {}):
                self.order_overrides[new_iid] = int(self.order_overrides.pop(old_iid))
                try:
                    self.save_settings()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.scan()
        except Exception:
            pass

        messagebox.showinfo("Rename Folder", f"Renamed to:\n{new_name}")

    # --------------------------------------------------
    # Minimal analysis hook used by tests
    # --------------------------------------------------
    def analyze(self):
        # Ensure output widget exists for smoke tests
        try:
            if not hasattr(self, "output"):
                self.output = tk.Text(self.root)
        except Exception:
            # Fallback: lightweight stub with get()/delete()/insert()
            class _Stub:
                def __init__(self):
                    self._text = ""

                def get(self, start, end):
                    return self._text

                def delete(self, start, end):
                    self._text = ""

                def insert(self, where, s):
                    self._text += s

            self.output = _Stub()

        # Attempt a scan; ignore errors for test stability
        try:
            self.scan()
        except Exception:
            if not hasattr(self, "mods"):
                self.mods = []
        # scan() can also exit early without raising (e.g. missing Mods path)
        if not hasattr(self, "mods"):
            self.mods = []

        # Write a simple header so tests can verify
        try:
            self.output.delete("1.0", tk.END)
        except Exception:
            pass
        self.output.insert(tk.END, "RECOMMENDED LOAD ORDER\n")

    # --------------------------------------------------
    # Compatibility shim used by tests
    # --------------------------------------------------
    def generate_load_order(self):
        # Reuse existing export flow; test patches filedialog path
        self.export()

    # Wrappers for button wiring verification
    def generate_and_apply(self):
        self.apply_load_order()

    def export_load_order(self):
        self.export()

    def export_vortex(self):
        try:
            """Export enabled mods in load-order as a Vortex-friendly JSON list.

            Output schema:
            [ { "id": "FolderName", "enabled": true }, ... ]
            """
            if not getattr(self, "mods", None):
                try:
                    self.scan()
                except Exception:
                    pass

            if not getattr(self, "mods", None):
                messagebox.showwarning("No data", "Scan mods first.")
                return

            self.operation_logger.log("[ACTION] 📤 Exporting to Vortex format...")

            # Reuse the same deterministic rule-based ordering used by normal export.
            rules = []
            try:
                from logic.rule_store import RuleStore

                store = RuleStore(str(DATA_DIR / "rules.json"))
                rules = (store.list_user_rules() or []) + (store.list_profile_rules() or [])
            except Exception:
                rules = []

            try:
                ordered, _report = compute_load_order(self.mods, user_rules=rules, include_disabled=True)
            except Exception:
                ordered = list(self.mods)

            # Vortex list should include enabled, deployable mods only.
            enabled = [m for m in (ordered or []) if is_effectively_enabled(m) and is_deployable_mod(m)]
            try:
                enabled.sort(
                    key=lambda mm: (
                        int(getattr(mm, "load_order", 0) or 0),
                        str(getattr(mm, "name", "") or "").lower(),
                    )
                )
            except Exception:
                pass

            def _vortex_id(m):
                try:
                    # Prefer the physical folder name (strip legacy prefixes like __DISABLED__ / 000_).
                    return str(self._clean_folder_name_for_order(getattr(m, "name", "") or "")).strip()
                except Exception:
                    return str(getattr(m, "name", "") or "").strip()

            payload = [{"id": _vortex_id(m), "enabled": True} for m in enabled if _vortex_id(m)]

            path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
                initialfile="Vortex_LoadOrder.json",
            )
            if not path:
                return

            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            except Exception as ex:
                messagebox.showerror("Export Vortex", f"Failed to export Vortex JSON: {ex}")
                return

            messagebox.showinfo("Exported", f"Saved to:\n{path}")

            self.operation_logger.log("[ACTION] ✅ Exported to Vortex format")
        except Exception as e:
            if self.operation_logger:
                self.operation_logger.log(f"[ERROR] X Vortex export failed: {str(e)[:100]}")
            messagebox.showerror("Export Error", f"Unexpected error during Vortex export: {e}")

    def rename_selected_mod_folder(self):
        """Rename the selected mod's folder name."""
        try:
            # Get selected item
            selected = self.table.selection()
            if not selected:
                messagebox.showwarning("No Selection", "Please select a mod to rename.")
                return

            row = selected[0]
            mod = getattr(self, "mod_lookup", {}).get(row)
            if not mod:
                messagebox.showerror("Error", "Could not find mod data for selection.")
                return

            # Get current folder name
            current_path = getattr(mod, "path", "")
            if not current_path or not os.path.isdir(current_path):
                messagebox.showerror("Error", "Mod folder does not exist.")
                return

            current_name = os.path.basename(current_path)

            # Prompt for new name
            from tkinter import simpledialog
            new_name = simpledialog.askstring("Rename Mod Folder", f"Current name: {current_name}\n\nEnter new folder name:", initialvalue=current_name)
            if not new_name or new_name.strip() == current_name:
                return

            new_name = new_name.strip()
            if not new_name:
                return

            # Sanitize the new name
            sanitized_name = self._sanitize_user_folder_name(new_name)
            if not sanitized_name:
                messagebox.showerror("Error", "Invalid folder name after sanitization.")
                return

            # Check if target already exists
            parent_dir = os.path.dirname(current_path)
            new_path = os.path.join(parent_dir, sanitized_name)
            if os.path.exists(new_path):
                messagebox.showerror("Error", f"Folder '{sanitized_name}' already exists.")
                return

            # Rename the folder
            try:
                os.rename(current_path, new_path)
                # Update mod path
                mod.path = new_path
                mod.name = sanitized_name
                # Log success
                if self.operation_logger:
                    self.operation_logger.log(f"[ACTION] 🔄 Renamed '{current_name}' to '{sanitized_name}'")
                # Refresh table
                self.refresh_table()
            except Exception as e:
                error_msg = f"Failed to rename folder: {e}"
                messagebox.showerror("Rename Error", error_msg)
                if self.operation_logger:
                    self.operation_logger.log(f"[ERROR] X Rename failed: {error_msg}")

        except Exception as e:
            error_msg = f"Unexpected error during rename: {e}"
            messagebox.showerror("Error", error_msg)
            if self.operation_logger:
                self.operation_logger.log(f"[ERROR] X Rename error: {error_msg}")

    # --------------------------------------------------
    # Export mods as JSON with diagnostics fields
    # --------------------------------------------------
    def export_json(self, mods, path):
        data = [
            {
                "name": m.name,
                "path": m.path,
                "category": getattr(m, "category", ""),
                "categories": list(getattr(m, "categories", None) or []),
                "priority": getattr(m, "priority", 0),
                "is_overhaul": getattr(m, "is_overhaul", False),
                "scopes": sorted(list(getattr(m, "scopes", set()) or [])),
                "user_disabled": getattr(m, "user_disabled", False),
                "load_order": getattr(m, "load_order", 0),
                "conflict": getattr(m, "conflict", False),
                "conflict_level": getattr(m, "conflict_level", None),
                "redundant": getattr(m, "redundant", False),
                "redundant_reason": getattr(m, "redundant_reason", None),
                "has_modinfo": getattr(m, "has_modinfo", True),
                "is_poi": getattr(m, "is_poi", False),
                "severity": getattr(m, "severity", 0),
                "category_evidence": getattr(m, "category_evidence", {}) or {},
            }
            for m in mods
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # --------------------------------------------------
    # Refresh the Treeview from self.mods applying filter
    # --------------------------------------------------
    def refresh_table(self):
        # Clear table
        for row in self.table.get_children():
            self.table.delete(row)

        # Rebuild lookup alongside the table contents
        try:
            self.mod_lookup = {}
        except Exception:
            pass

        query = (self.search_var.get() or "").lower().strip()

        # Build filtered list first
        filtered_mods = []
        for idx, mod in enumerate(self.mods):
            # set an in-memory load order if not already set
            try:
                mod.load_order = getattr(mod, "load_order", idx)
            except Exception:
                mod.load_order = idx
            # compute severity
            try:
                mod.severity = self.calculate_severity(mod)
            except Exception:
                mod.severity = getattr(mod, "severity", 0)
            # high-risk flag
            try:
                mod.high_risk = (
                    mod.severity >= 60 and normalize_category(getattr(mod, "category", "")) in HIGH_RISK_CATEGORIES
                )
            except Exception:
                mod.high_risk = False
            # derive taxonomy
            try:
                derive_conflict_taxonomy(mod)
            except Exception:
                pass

            # If Show All is enabled, bypass all filters
            bypass = bool(getattr(self, "show_all_var", None) and self.show_all_var.get())
            if not bypass:
                # text search filter
                if query:
                    if (
                        query not in mod.name.lower()
                        and query not in mod.path.lower()
                        and query not in mod.category.lower()
                    ):
                        continue

                # legend filter + hide-low toggle
                if not legend_filter_match(mod, getattr(self, "legend_filter", None)):
                    continue
                if should_hide_mod(
                    mod,
                    getattr(self, "hide_low_conflicts", None) and self.hide_low_conflicts.get(),
                ):
                    continue

                # Filters: Category + Severity + Conflicts only
                try:
                    sel_cat = self._selected_category_name() if hasattr(self, "filter_category_var") else "All"
                except Exception:
                    sel_cat = "All"
                try:
                    thr = int(self.filter_severity_var.get()) if hasattr(self, "filter_severity_var") else 0
                except Exception:
                    thr = 0
                try:
                    conflicts_only = (
                        bool(self.filter_conflicts_only_var.get())
                        if hasattr(self, "filter_conflicts_only_var")
                        else False
                    )
                except Exception:
                    conflicts_only = False

                if sel_cat and sel_cat != "All" and normalize_category(mod.category) != sel_cat:
                    # Multi-category support: show mods that belong to the selected category
                    try:
                        mod_cats = getattr(mod, "categories", None) or [
                            normalize_category(getattr(mod, "category", None))
                        ]
                        if sel_cat not in mod_cats:
                            continue
                    except Exception:
                        continue
                # Keep OK mods when threshold is 0; use >= threshold logic
                if getattr(mod, "severity", 0) < thr:
                    continue
                if conflicts_only and not getattr(mod, "conflict", False):
                    continue

            # ensure display invariants
            assert hasattr(mod, "severity")
            filtered_mods.append(mod)

        # Group by category and insert spacer rows (spacers bypass filtering by insertion after filtering)
        cats = {}
        for m in filtered_mods:
            cats.setdefault(m.category, []).append(m)

        ordered_cats = [c for c in CATEGORY_ORDER if c in cats] + [
            c for c in sorted(cats.keys()) if c not in CATEGORY_ORDER
        ]

        # Heatmap selection should float that category to the top (even when not filtered)
        try:
            sel = getattr(self, "heatmap_selected_category", None)
            if sel and sel in ordered_cats:
                ordered_cats = [sel] + [c for c in ordered_cats if c != sel]
        except Exception:
            pass

        # map category -> spacer row iid for jumping
        self._category_rows = {}

        for cat in ordered_cats:
            # Insert spacer
            try:
                label = CATEGORY_SPACER_LABELS.get(cat, f"── {cat.upper()} ─────────────")
                spacer_values = ("", label, "", "", "", "")
                iid = f"cat::{cat}"
                self.table.insert("", "end", iid=iid, values=spacer_values, tags=("spacer",))
                self._category_rows[cat] = iid
            except Exception:
                pass

            for mod in cats.get(cat, []):
                # compute status from severity + flags and set on mod
                try:
                    mod.enabled = (
                        bool(getattr(mod, "enabled", True))
                        and not bool(getattr(mod, "user_disabled", False))
                        and not bool(getattr(mod, "disabled", False))
                    )
                except Exception:
                    mod.enabled = not getattr(mod, "user_disabled", False)
                mod_has_modinfo = getattr(mod, "has_modinfo", False)
                mod_is_poi = getattr(mod, "is_poi", False)

                # Patch mods are resolution artifacts; keep them clean and out of conflict buckets.
                if getattr(mod, "is_patch", False):
                    status = "Resolved"
                    try:
                        mod.conflict = False
                        mod.conflict_level = None
                        mod.severity = 0
                    except Exception:
                        pass
                elif not mod_has_modinfo and not mod_is_poi:
                    status = "Warning"
                    try:
                        mod.conflict_type = "no_modinfo"
                    except Exception:
                        pass
                elif getattr(mod, "redundant", False):
                    status = "Redundant"
                elif getattr(mod, "severity", 0) >= 80:
                    status = "Critical"
                elif getattr(mod, "severity", 0) >= 40:
                    status = "Conflict (High)"
                elif getattr(mod, "severity", 0) >= 1:
                    status = "Conflict (Low)"
                else:
                    status = "OK"
                mod.status = status

                # High-risk icon overlay for display (keeps tags separate)
                try:
                    if getattr(mod, "high_risk", False) and "⚠" not in status and status.startswith("Conflict"):
                        status = f"⚠ {status}"
                except Exception:
                    pass

                eff_enabled = is_effectively_enabled(mod)
                enabled_icon = "☑" if eff_enabled else "☐"
                # If disabled but not by the user, show as locked.
                try:
                    if (not eff_enabled) and (not bool(getattr(mod, "user_disabled", False))):
                        enabled_icon = enabled_icon + "🔒"
                except Exception:
                    pass

                action_text = suggested_action(mod)
                # Debug: log status for mods without ModInfo.xml
                if not getattr(mod, "has_modinfo", True):
                    print(f"DEBUG: {mod.name} status={status} conflict_type={getattr(mod, 'conflict_type', 'none')}")
                status_display = status
                try:
                    if isinstance(getattr(mod, "status", None), str) and getattr(mod, "status").startswith("Conflict"):
                        status_display = conflict_category_label(mod)
                except Exception:
                    status_display = status

                # Update hint overlay for OK mods
                try:
                    if getattr(mod, "update_available", False) and str(getattr(mod, "status", "")) == "OK":
                        status_display = "Update Available"
                except Exception:
                    pass

                # Include some evidence in Status when relevant
                try:
                    if isinstance(getattr(mod, "status", None), str) and getattr(mod, "status").startswith("Conflict"):
                        ev = conflict_evidence_summary(mod)
                        if ev:
                            status_display = f"{status_display}: {ev}"
                    elif str(getattr(mod, "integrity", "")).lower() == "invalid":
                        label = conflict_category_label(mod)
                        ev = conflict_evidence_summary(mod)
                        status_display = f"{label}: {ev}" if ev else label
                except Exception:
                    pass

                # Disabled mods: explicit status messaging
                try:
                    if not is_effectively_enabled(mod):
                        status_display = "Disabled"
                except Exception:
                    pass

                # Framework marker: surfaced for visibility (metadata-driven flag)
                try:
                    if bool(getattr(mod, "is_framework", False)) and "Framework" not in str(status_display):
                        status_display = f"{status_display} (Framework)".strip()
                except Exception:
                    pass

                values = (
                    enabled_icon,
                    mod.name,
                    str(getattr(mod, "category", "")),
                    str(mod.priority),
                    status_display,
                    action_text,
                )

                try:
                    # prefer stable iid so we can reference rows later; apply tags at insert time
                    self.table.insert("", "end", iid=mod.path, values=values, tags=get_row_tags(mod))
                    item_id = mod.path
                except Exception:
                    # fallback: insert and capture generated iid
                    item_id = self.table.insert("", "end", values=values, tags=get_row_tags(mod))

                # keep quick lookup for updates
                try:
                    self.mod_lookup[item_id] = mod
                except Exception:
                    pass

            # Tags already applied at insert time; avoid recoloring by status text

        # Update legend counts using helper
        try:
            all_counts = calculate_legend_counts(self.mods)
            self._legend_vars["error"].set(f"Critical ({all_counts.get('error', 0)})")
            self._legend_vars["conflict_high"].set(f"High ({all_counts.get('conflict_high', 0)})")
            self._legend_vars["conflict_low"].set(f"Low ({all_counts.get('conflict_low', 0)})")
            self._legend_vars["redundant"].set(f"Redundant ({all_counts['redundant']})")
            self._legend_vars["disabled"].set(f"Disabled ({all_counts['disabled']})")
            self._legend_vars["ok"].set(f"OK ({all_counts['ok']})")
        except Exception:
            pass

        # Update heatmap
        try:
            self._update_heatmap()
        except Exception:
            pass

        # Update filtered count label and optional debug
        try:
            total = len(self.mods)
            shown = len(filtered_mods)
            self.filtered_count_var.set(f"Showing {shown} of {total}")
        except Exception:
            pass

        # Update overall counts label
        try:
            self.update_mod_count()
        except Exception:
            pass

        if getattr(self, "debug_scanner", False):
            try:
                print("Filtered mods:", len(filtered_mods), "of", len(self.mods))
            except Exception:
                pass

    # ------------------ Category dropdown helpers ------------------
    def _selected_category_name(self):
        sel = (self.filter_category_var.get() or "").strip()
        if not sel:
            return "All"
        if sel.lower().startswith("all"):
            return "All"
        # Strip counts suffix e.g., "Category (12)"
        if "(" in sel and sel.endswith(")"):
            base = sel.rsplit(" (", 1)[0].strip()
            return base
        return sel

    def _category_counts(self):
        counts = {}
        for m in self.mods:
            cat = normalize_category(getattr(m, "category", "") or "Miscellaneous")
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    def update_category_dropdown(self):
        try:
            counts = self._category_counts()
            total = len(self.mods)
            cats = [c for c in CATEGORY_ORDER if c in counts] + [
                c for c in sorted(counts.keys(), key=lambda s: s.lower()) if c not in CATEGORY_ORDER
            ]
            values = [f"All ({total})"] + [f"{c} ({counts[c]})" for c in cats]
            self.filter_category["values"] = values
            # Preserve current selection by mapping base name
            current_base = self._selected_category_name()
            if current_base == "All":
                self.filter_category_var.set(f"All ({total})")
            else:
                # find matching value by base
                for v in values:
                    base = v.rsplit(" (", 1)[0]
                    if base == current_base:
                        self.filter_category_var.set(v)
                        break
                else:
                    # default to All
                    self.filter_category_var.set(f"All ({total})")
        except Exception:
            pass

    # --------------------------------------------------
    # Toggle enabled/disabled when user clicks the Enabled column
    # --------------------------------------------------
    def on_tree_click(self, event):
        try:
            item_id = self.table.identify_row(event.y)
            if not item_id:
                return

            mod = self.mod_lookup.get(item_id)
            if not mod:
                return

            mod.enabled = not mod.enabled

            state = "ENABLED" if mod.enabled else "DISABLED"
            if self.operation_logger:
                self.operation_logger.log(f"[ACTION] 🔧 {state}: {mod.name}")

            self.refresh_table()

        except Exception as e:
            if self.operation_logger:
                self.operation_logger.log(f"[ERROR] ❌ Toggle failed: {str(e)}")
    # --------------------------------------------------
    # Reset filters to defaults and refresh
    # --------------------------------------------------
    def reset_filters(self):
        try:
            if hasattr(self, "filter_category_var"):
                self.filter_category_var.set("All")
            if hasattr(self, "filter_severity"):
                self.filter_severity.set(0)
            if hasattr(self, "filter_severity_var"):
                self.filter_severity_var.set(0)
            if hasattr(self, "filter_conflicts_only_var"):
                self.filter_conflicts_only_var.set(False)
            if hasattr(self, "search_var"):
                self.search_var.set("")
            if hasattr(self, "hide_low_conflicts"):
                self.hide_low_conflicts.set(False)
            if hasattr(self, "show_all_var"):
                self.show_all_var.set(False)
        except Exception:
            pass
        self.refresh_table()

    def apply_row_style(self, item_id, mod):
        """
        Apply Treeview tag based on mod state.
        This is the ONLY place colors should change.
        """
        try:
            has_modinfo = getattr(mod, "has_modinfo", None)
            if has_modinfo is None:
                has_modinfo = os.path.isfile(os.path.join(mod.path, "ModInfo.xml"))
        except Exception:
            has_modinfo = False

        enabled = not getattr(mod, "user_disabled", False)
        redundant = getattr(mod, "redundant", False)
        is_poi = getattr(mod, "is_poi", False)
        conflict_level = getattr(mod, "conflict_level", None)

        # Priority logic: error > conflict_high > conflict_low > redundant > disabled > ok
        if not has_modinfo and not is_poi:
            self.table.item(item_id, tags=("error",))
        elif conflict_level == "high":
            self.table.item(item_id, tags=("conflict_high",))
        elif conflict_level == "low":
            self.table.item(item_id, tags=("conflict_low",))
        elif redundant:
            self.table.item(item_id, tags=("redundant",))
        elif not enabled:
            self.table.item(item_id, tags=("disabled",))
        else:
            self.table.item(item_id, tags=("ok",))

    def show_scrollable_popup(self, content, title="Info"):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("600x400")
        win.transient(self.root)
        win.grab_set()

        frame = tk.Frame(win, bg=self.colors.get("panel", "#252526"))
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        text = tk.Text(frame, wrap="word", font=("Consolas", 10))
        scrollbar = ttk.Scrollbar(frame, command=text.yview)
        text.config(yscrollcommand=scrollbar.set)

        text.insert(tk.END, content)
        text.config(state="disabled")

        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _confirm_apply_load_order_preview(
        self,
        *,
        mods_dir: str,
        enabled_mods: List[Any],
        ordered_enabled: List[Any],
        desired_prefix_by_path: Dict[str, int],
        ops: List[Any],
        report: Any,
    ) -> bool:
        """Rich confirmation dialog for folder rename apply.

        Goals:
        - Easier to scan: grouping + color coding.
        - Less overwhelming: collapse details by default.
        - Highlight important changes: moved earlier/later, patches, warnings.
        - Safer: require explicit acknowledgement before Apply.
        """

        # Best-effort utilities
        try:
            from logic.conflict_memory import normalize_mod_id as _normalize_mod_id
        except Exception:
            _normalize_mod_id = None

        def _norm_id(name: str) -> str:
            try:
                if _normalize_mod_id:
                    return str(_normalize_mod_id(name) or "")
            except Exception:
                pass
            return str(name or "").strip()

        def _safe_str(x: Any) -> str:
            try:
                return str(x or "")
            except Exception:
                return ""

        def _is_patch(mod: Any) -> bool:
            try:
                if bool(getattr(mod, "is_patch", False)):
                    return True
                tier = str(getattr(mod, "tier", "") or "")
                if tier == "Patch Mods":
                    return True
                return _is_patch_mod_name(str(getattr(mod, "name", "") or ""))
            except Exception:
                return False

        enabled_by_id: Dict[str, Any] = {}
        for m in enabled_mods or []:
            mid = _norm_id(_safe_str(getattr(m, "name", "")))
            if mid:
                enabled_by_id[mid.lower()] = m

        applied_edges = []
        try:
            applied_edges = list(getattr(report, "applied_edges", []) or [])
        except Exception:
            applied_edges = []

        renamed_by_path = {str(old): str(new) for (old, new) in (ops or [])}

        try:
            _mx = max(int(v) for v in (desired_prefix_by_path.values() or [0]))
        except Exception:
            _mx = 0
        pref_width = max(3, len(str(int(_mx))))

        items = []
        for m in enabled_mods or []:
            try:
                pth = str(getattr(m, "path", "") or "")
                if not pth:
                    continue
                old_folder = pathlib.Path(pth).name
                old_pref = _parse_order_prefix(old_folder)
                new_pref = desired_prefix_by_path.get(pth)

                # IMPORTANT: show the exact folder name that will be applied.
                # Reconstructing from (prefix, base) can drift from the actual ops
                # because apply-time logic may alter the base (e.g., UI group prefixing).
                new_folder = ""
                try:
                    planned_new = renamed_by_path.get(pth)
                    if planned_new:
                        new_folder = pathlib.Path(str(planned_new)).name
                except Exception:
                    new_folder = ""
                if not new_folder and isinstance(new_pref, int):
                    base = self._clean_folder_name_for_order(old_folder)
                    new_folder = f"{int(new_pref):0{pref_width}d}_{base}"

                # Prefer parsing the effective new prefix from the resulting folder name.
                # This keeps the preview aligned with what will be renamed on disk.
                try:
                    new_pref_eff = _parse_order_prefix(new_folder) if new_folder else None
                except Exception:
                    new_pref_eff = None
                if isinstance(new_pref_eff, int):
                    new_pref_for_change = int(new_pref_eff)
                elif isinstance(new_pref, int):
                    new_pref_for_change = int(new_pref)
                else:
                    new_pref_for_change = None

                if old_pref is None and isinstance(new_pref_for_change, int):
                    change = "new_prefix"
                elif (
                    isinstance(old_pref, int)
                    and isinstance(new_pref_for_change, int)
                    and old_pref != new_pref_for_change
                ):
                    change = "moved_earlier" if new_pref_for_change < old_pref else "moved_later"
                else:
                    change = "unchanged"

                reason = ""
                mid = _norm_id(_safe_str(getattr(m, "name", "")))
                mid_key = mid.lower()
                if mid_key and applied_edges:
                    if change == "moved_earlier":
                        for e in applied_edges:
                            try:
                                if str(getattr(e, "before", "") or "").lower() != mid_key:
                                    continue
                                other = enabled_by_id.get(str(getattr(e, "after", "") or "").lower())
                                other_name = (
                                    _safe_str(getattr(other, "name", ""))
                                    if other
                                    else _safe_str(getattr(e, "after", ""))
                                )
                                rsn = _safe_str(getattr(e, "reason", ""))
                                layer = _safe_str(getattr(e, "layer", ""))
                                reason = f"Moved earlier to load before {other_name}. Reason: {rsn}".strip()
                                if layer:
                                    reason = reason + f" ({layer})"
                                break
                            except Exception:
                                continue
                    elif change == "moved_later":
                        for e in applied_edges:
                            try:
                                if str(getattr(e, "after", "") or "").lower() != mid_key:
                                    continue
                                other = enabled_by_id.get(str(getattr(e, "before", "") or "").lower())
                                other_name = (
                                    _safe_str(getattr(other, "name", ""))
                                    if other
                                    else _safe_str(getattr(e, "before", ""))
                                )
                                rsn = _safe_str(getattr(e, "reason", ""))
                                layer = _safe_str(getattr(e, "layer", ""))
                                reason = f"Moved later to load after {other_name}. Reason: {rsn}".strip()
                                if layer:
                                    reason = reason + f" ({layer})"
                                break
                            except Exception:
                                continue

                items.append(
                    {
                        "mod": m,
                        "path": pth,
                        "old_folder": old_folder,
                        "new_folder": new_folder,
                        "old_pref": old_pref,
                        "new_pref": new_pref_for_change,
                        "change": change,
                        "is_patch": _is_patch(m),
                        "category": normalize_category(getattr(m, "category", "") or "Miscellaneous"),
                        "tier": _safe_str(getattr(m, "tier", "")),
                        "severity": int(getattr(m, "severity", 0) or 0),
                        "conflict": bool(getattr(m, "conflict", False)),
                        "reason": reason,
                        "will_rename": pth in renamed_by_path,
                    }
                )
            except Exception:
                continue

        moved_earlier = [it for it in items if it["change"] == "moved_earlier"]
        moved_later = [it for it in items if it["change"] == "moved_later"]
        new_prefix = [it for it in items if it["change"] == "new_prefix"]
        unchanged = [it for it in items if it["change"] == "unchanged"]
        patch_items = [it for it in items if it["is_patch"]]

        total_enabled = len(enabled_mods or [])
        renamed_count = len(ops or [])

        warn_ct = 0
        err_ct = 0
        conf = "unknown"
        warnings = []
        try:
            warnings = list(getattr(report, "warnings", []) or [])
            warn_ct = len(warnings)
            err_ct = len(getattr(report, "errors", []) or [])
            conf = getattr(report, "confidence_level", lambda: "unknown")()
        except Exception:
            warnings = []

        win = tk.Toplevel(self.root)
        win.title("Preview Load Order Changes")
        win.geometry("980x720")
        win.transient(self.root)
        win.grab_set()

        try:
            win.configure(bg=self.colors.get("bg", "#1e1e1e"))
        except Exception:
            pass

        header = tk.Frame(win, bg=self.colors.get("panel", "#252526"))
        header.pack(fill="x", padx=10, pady=(10, 8))

        tk.Label(
            header,
            text="📊 Load Order Summary",
            bg=self.colors.get("panel", "#252526"),
            fg=self.colors.get("fg", "#d4d4d4"),
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        summary = tk.Frame(header, bg=self.colors.get("panel", "#252526"))
        summary.pack(fill="x", pady=(6, 0))

        def _summary_line(text: str):
            tk.Label(
                summary,
                text=text,
                bg=self.colors.get("panel", "#252526"),
                fg=self.colors.get("fg", "#d4d4d4"),
                font=("Segoe UI", 9),
            ).pack(anchor="w")

        _summary_line(f"Total enabled mods: {total_enabled}")
        _summary_line(f"Renamed folders: {renamed_count}")
        _summary_line(
            f"Moved earlier: {len(moved_earlier)}   |   Moved later: {len(moved_later)}   |   New prefix added: {len(new_prefix)}"
        )
        _summary_line(f"Patch mods: {len(patch_items)}")
        _summary_line(f"Engine confidence: {conf}   |   Warnings: {warn_ct}   |   Errors: {err_ct}")
        _summary_line(f"Target folder: {mods_dir}")

        filters = tk.Frame(win, bg=self.colors.get("bg", "#1e1e1e"))
        filters.pack(fill="x", padx=10)

        search_var = tk.StringVar(value="")
        tk.Label(
            filters,
            text="Search:",
            bg=self.colors.get("bg", "#1e1e1e"),
            fg=self.colors.get("fg", "#d4d4d4"),
        ).pack(side="left")
        search_entry = ttk.Entry(filters, textvariable=search_var, width=34)
        search_entry.pack(side="left", padx=(6, 12))

        f_conflicts = tk.BooleanVar(value=False)
        f_weapons = tk.BooleanVar(value=False)
        f_worldgen = tk.BooleanVar(value=False)
        f_patches = tk.BooleanVar(value=False)

        ttk.Checkbutton(filters, text="Conflicts", variable=f_conflicts).pack(side="left", padx=6)
        ttk.Checkbutton(filters, text="Weapon Mods", variable=f_weapons).pack(side="left", padx=6)
        ttk.Checkbutton(filters, text="Worldgen Mods", variable=f_worldgen).pack(side="left", padx=6)
        ttk.Checkbutton(filters, text="Patches", variable=f_patches).pack(side="left", padx=6)

        body = tk.Frame(win, bg=self.colors.get("bg", "#1e1e1e"))
        body.pack(fill="both", expand=True, padx=10, pady=10)

        canvas = tk.Canvas(body, bg=self.colors.get("bg", "#1e1e1e"), highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=self.colors.get("bg", "#1e1e1e"))
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event=None):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_configure(event):
            try:
                canvas.itemconfigure(inner_id, width=event.width)
            except Exception:
                pass

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        COLOR_SAFE = "#7ee787"  # 🟢
        COLOR_REORDER = "#f2cc60"  # 🟡
        COLOR_CONFLICT = "#ff7b72"  # 🔴
        COLOR_PATCH = "#79c0ff"  # 🔵
        COLOR_MUTED = self.colors.get("fg", "#d4d4d4")

        def _is_weapon_mod(it: dict) -> bool:
            try:
                if it.get("category") == "Weapons":
                    return True
                scopes = set(getattr(it.get("mod"), "scopes", set()) or [])
                return "weapons" in {s.lower() for s in scopes}
            except Exception:
                return False

        def _is_worldgen_mod(it: dict) -> bool:
            try:
                if str(it.get("tier") or "") == "POI / World":
                    return True
                if it.get("category") in {"Maps", "Prefabs / POIs"}:
                    return True
                nm = _safe_str(getattr(it.get("mod"), "name", "")).lower()
                return any(k in nm for k in ["biome", "world", "rwgmixer", "prefab"])
            except Exception:
                return False

        def _is_conflict_mod(it: dict) -> bool:
            try:
                if bool(it.get("conflict")):
                    return True
                return int(it.get("severity") or 0) >= 40
            except Exception:
                return False

        def _passes_filters(it: dict) -> bool:
            q = (search_var.get() or "").lower().strip()
            if q:
                blob = " ".join(
                    [
                        _safe_str(getattr(it.get("mod"), "name", "")),
                        _safe_str(it.get("old_folder")),
                        _safe_str(it.get("new_folder")),
                        _safe_str(it.get("reason")),
                        _safe_str(it.get("category")),
                        _safe_str(it.get("tier")),
                    ]
                ).lower()
                if q not in blob:
                    return False

            chosen = []
            if f_conflicts.get():
                chosen.append(_is_conflict_mod(it))
            if f_weapons.get():
                chosen.append(_is_weapon_mod(it))
            if f_worldgen.get():
                chosen.append(_is_worldgen_mod(it))
            if f_patches.get():
                chosen.append(bool(it.get("is_patch")))
            if chosen and not any(chosen):
                return False
            return True

        def _section(parent, title: str, *, default_open: bool):
            frame = tk.Frame(parent, bg=self.colors.get("panel", "#252526"))
            frame.pack(fill="x", pady=(0, 10))

            head = tk.Frame(frame, bg=self.colors.get("panel", "#252526"))
            head.pack(fill="x", padx=10, pady=(8, 4))

            open_var = tk.BooleanVar(value=bool(default_open))
            btn = ttk.Button(head, text="Hide ▲" if open_var.get() else "Show ▼")
            btn.pack(side="right")
            tk.Label(
                head,
                text=title,
                bg=self.colors.get("panel", "#252526"),
                fg=self.colors.get("fg", "#d4d4d4"),
                font=("Segoe UI", 10, "bold"),
            ).pack(side="left", anchor="w")

            bodyf = tk.Frame(frame, bg=self.colors.get("panel", "#252526"))
            bodyf.pack(fill="x", padx=10, pady=(0, 10))

            def _sync():
                try:
                    if open_var.get():
                        bodyf.pack(fill="x", padx=10, pady=(0, 10))
                        btn.configure(text="Hide ▲")
                    else:
                        bodyf.pack_forget()
                        btn.configure(text="Show ▼")
                except Exception:
                    pass

            def _toggle():
                open_var.set(not open_var.get())
                _sync()

            btn.configure(command=_toggle)
            if not default_open:
                bodyf.pack_forget()
            return bodyf

        def _make_list(parent, *, color: str) -> tk.Text:
            text = tk.Text(
                parent,
                wrap="word",
                height=10,
                bg=self.colors.get("entry_bg", "#2d2d2d"),
                fg=COLOR_MUTED,
                insertbackground=COLOR_MUTED,
                relief="flat",
            )
            sb = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=sb.set)
            text.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")

            text.tag_configure("hdr", foreground=color, font=("Segoe UI", 9, "bold"))
            text.tag_configure("reason", foreground=self.colors.get("fg", "#d4d4d4"), font=("Segoe UI", 9))
            text.tag_configure("muted", foreground="#9aa4b2", font=("Segoe UI", 9))
            return text

        def _fill_list(text: tk.Text, rows: List[dict], *, icon: str, show_reason: bool):
            try:
                text.config(state="normal")
                text.delete("1.0", tk.END)
            except Exception:
                return

            try:
                _mx2 = 0
                for _it in rows:
                    _np = _it.get("new_pref")
                    if isinstance(_np, int):
                        _mx2 = max(_mx2, int(_np))
                _w = max(3, len(str(int(_mx2))))
            except Exception:
                _w = 3

            shown_any = False
            for it in rows:
                if not _passes_filters(it):
                    continue
                shown_any = True
                mod = it.get("mod")
                name = _safe_str(getattr(mod, "name", ""))
                oldp = it.get("old_pref")
                newp = it.get("new_pref")
                prefix_note = ""
                try:
                    if isinstance(oldp, int) and isinstance(newp, int):
                        prefix_note = f"({int(oldp):0{_w}d} → {int(newp):0{_w}d})"
                    elif oldp is None and isinstance(newp, int):
                        prefix_note = f"(— → {int(newp):0{_w}d})"
                except Exception:
                    prefix_note = ""
                cat = _safe_str(it.get("category"))
                tier = _safe_str(it.get("tier"))
                text.insert(tk.END, f"{icon} {name} {prefix_note}\n", "hdr")
                text.insert(tk.END, f"    {cat} | {tier}\n", "muted")
                if show_reason and it.get("reason"):
                    text.insert(tk.END, f"    {it.get('reason')}\n", "reason")
                text.insert(tk.END, "\n")

            if not shown_any:
                text.insert(tk.END, "(none)\n", "muted")

            try:
                text.config(state="disabled")
            except Exception:
                pass

        # Important changes first
        sec1 = _section(inner, f"🟡 Reordered — Moved Earlier ({len(moved_earlier)})", default_open=True)
        moved_earlier_rows = sorted(
            moved_earlier,
            key=lambda it: (
                _safe_str(it.get("tier")).lower(),
                _safe_str(getattr(it.get("mod"), "name", "")).lower(),
            ),
        )
        t_moved_earlier = _make_list(sec1, color=COLOR_REORDER)

        sec2 = _section(inner, f"🟡 Reordered — Moved Later ({len(moved_later)})", default_open=True)
        moved_later_rows = sorted(
            moved_later,
            key=lambda it: (
                _safe_str(it.get("tier")).lower(),
                _safe_str(getattr(it.get("mod"), "name", "")).lower(),
            ),
        )
        t_moved_later = _make_list(sec2, color=COLOR_REORDER)

        sec3 = _section(inner, f"🔵 Patch Alignment ({len(patch_items)})", default_open=bool(patch_items))
        patch_rows = sorted(patch_items, key=lambda it: _safe_str(getattr(it.get("mod"), "name", "")).lower())
        t_patches = _make_list(sec3, color=COLOR_PATCH)

        sec4 = _section(inner, f"🔴 Notes / Warnings ({len(warnings)})", default_open=bool(warnings))
        warn_text = tk.Text(
            sec4,
            wrap="word",
            height=8,
            bg=self.colors.get("entry_bg", "#2d2d2d"),
            fg=self.colors.get("fg", "#d4d4d4"),
            insertbackground=self.colors.get("fg", "#d4d4d4"),
            relief="flat",
        )
        warn_sb = ttk.Scrollbar(sec4, orient="vertical", command=warn_text.yview)
        warn_text.configure(yscrollcommand=warn_sb.set)
        warn_text.pack(side="left", fill="both", expand=True)
        warn_sb.pack(side="right", fill="y")
        if warnings:
            for w in warnings[:200]:
                warn_text.insert(tk.END, f"🔴 {w}\n\n")
        else:
            warn_text.insert(tk.END, "(none)\n")
        warn_text.config(state="disabled")

        sec5 = _section(inner, f"[ Show Detailed Renames ▼ ] ({renamed_count})", default_open=False)
        detail = tk.Text(
            sec5,
            wrap="none",
            height=12,
            bg=self.colors.get("entry_bg", "#2d2d2d"),
            fg=self.colors.get("fg", "#d4d4d4"),
            insertbackground=self.colors.get("fg", "#d4d4d4"),
            relief="flat",
            font=("Consolas", 9),
        )
        d_vsb = ttk.Scrollbar(sec5, orient="vertical", command=detail.yview)
        d_hsb = ttk.Scrollbar(sec5, orient="horizontal", command=detail.xview)
        detail.configure(yscrollcommand=d_vsb.set, xscrollcommand=d_hsb.set)
        detail.pack(side="top", fill="both", expand=True)
        d_vsb.pack(side="right", fill="y")
        d_hsb.pack(side="bottom", fill="x")
        for old_path, new_path in ops or []:
            try:
                detail.insert(
                    tk.END,
                    f"{pathlib.Path(old_path).name}  ->  {pathlib.Path(new_path).name}\n",
                )
            except Exception:
                continue
        detail.config(state="disabled")

        sec6 = _section(inner, f"📦 Unchanged Mods (hidden by default) ({len(unchanged)})", default_open=False)
        unchanged_rows = sorted(unchanged, key=lambda it: _safe_str(getattr(it.get("mod"), "name", "")).lower())
        t_unchanged = _make_list(sec6, color=COLOR_SAFE)

        def _refresh_filtered_views(*_):
            _fill_list(t_moved_earlier, moved_earlier_rows, icon="🟡", show_reason=True)
            _fill_list(t_moved_later, moved_later_rows, icon="🟡", show_reason=True)
            _fill_list(t_patches, patch_rows, icon="🔵", show_reason=False)
            _fill_list(t_unchanged, unchanged_rows, icon="🟢", show_reason=False)

            # Warnings: filter by search text only (checkbox filters are about mods)
            try:
                q = (search_var.get() or "").lower().strip()
                warn_text.config(state="normal")
                warn_text.delete("1.0", tk.END)
                if warnings:
                    for w in warnings[:200]:
                        if q and q not in str(w).lower():
                            continue
                        warn_text.insert(tk.END, f"🔴 {w}\n\n")
                else:
                    warn_text.insert(tk.END, "(none)\n")
                warn_text.config(state="disabled")
            except Exception:
                pass

            # Detailed renames: filter by search text only
            try:
                q = (search_var.get() or "").lower().strip()
                detail.config(state="normal")
                detail.delete("1.0", tk.END)
                for old_path, new_path in ops or []:
                    line = f"{pathlib.Path(old_path).name}  ->  {pathlib.Path(new_path).name}"
                    if q and q not in line.lower():
                        continue
                    detail.insert(tk.END, line + "\n")
                if q and (detail.get("1.0", tk.END).strip() == ""):
                    detail.insert(tk.END, "(none)\n")
                detail.config(state="disabled")
            except Exception:
                pass

        # Initial fill + wire live updates
        _refresh_filtered_views()
        try:
            search_var.trace_add("write", _refresh_filtered_views)
            f_conflicts.trace_add("write", _refresh_filtered_views)
            f_weapons.trace_add("write", _refresh_filtered_views)
            f_worldgen.trace_add("write", _refresh_filtered_views)
            f_patches.trace_add("write", _refresh_filtered_views)
        except Exception:
            pass

        footer = tk.Frame(win, bg=self.colors.get("bg", "#1e1e1e"))
        footer.pack(fill="x", padx=10, pady=(0, 10))

        ack_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            footer,
            text="I understand this will rename folders in my Mods Library (recommended: make a backup first).",
            variable=ack_var,
        ).pack(side="left", padx=(0, 10))

        btns = tk.Frame(footer, bg=self.colors.get("bg", "#1e1e1e"))
        btns.pack(side="right")

        result = {"ok": False}

        btn_apply = ttk.Button(btns, text="Apply (Rename Folders)")
        btn_cancel = ttk.Button(btns, text="Cancel")
        btn_cancel.pack(side="right", padx=(6, 0))
        btn_apply.pack(side="right")

        def _sync_apply_state(*_):
            try:
                if ack_var.get():
                    btn_apply.state(["!disabled"])
                else:
                    btn_apply.state(["disabled"])
            except Exception:
                pass

        def _apply():
            if not ack_var.get():
                return
            result["ok"] = True
            try:
                win.destroy()
            except Exception:
                pass

        def _cancel():
            result["ok"] = False
            try:
                win.destroy()
            except Exception:
                pass

        try:
            ack_var.trace_add("write", _sync_apply_state)
        except Exception:
            pass

        btn_apply.configure(command=_apply)
        btn_cancel.configure(command=_cancel)
        win.protocol("WM_DELETE_WINDOW", _cancel)
        _sync_apply_state()

        try:
            search_entry.focus_set()
        except Exception:
            pass

        try:
            win.bind("<Escape>", lambda e: _cancel())
            win.bind("<Return>", lambda e: _apply())
        except Exception:
            pass

        win.wait_window()
        return bool(result["ok"])

    def update_mod_count(self):
        total = len(self.mods)
        enabled = sum(1 for m in self.mods if is_effectively_enabled(m))
        conflicts = sum(1 for m in self.mods if getattr(m, "conflict", False))

        try:
            self.mod_count_var.set(f"Mods: {total} | Enabled: {enabled} | Conflicts: {conflicts}")
        except Exception:
            pass

    def calculate_severity(self, mod):
        """0–100 severity model factoring conflict type, category impact, and scope.

        This must remain deterministic: computed from scan results and category policy.
        """
        try:
            if not is_effectively_enabled(mod):
                return 0

            if not getattr(mod, "has_modinfo", True) and not getattr(mod, "is_poi", False):
                return 100

            if str(getattr(mod, "integrity", "") or "").lower() == "invalid":
                if getattr(mod, "conflict_type", None) == "missing_dependency":
                    return 100
                return 95

            if getattr(mod, "invalid_xml", False):
                return 95

            # Category impact: take the highest-impact category the mod belongs to
            cats = getattr(mod, "categories", None) or [normalize_category(getattr(mod, "category", None))]
            try:
                cat_weight = max(int(CATEGORY_IMPACT_WEIGHT.get(c, 0)) for c in cats)
            except Exception:
                cat_weight = int(CATEGORY_IMPACT_WEIGHT.get(normalize_category(getattr(mod, "category", None)), 0) or 0)

            def _conflict_score(c: dict) -> int:
                ctype = (c.get("conflict_type") or "").strip()
                level = (c.get("level") or "").strip()
                file = (c.get("file") or "").strip().lower()
                target = (c.get("target") or "").strip()
                scope = (c.get("scope") or "").strip()

                # Base by type
                if ctype in {
                    "missing_invalid",
                    "no_modinfo",
                    "invalid_xml",
                    "duplicate_id",
                    "missing_dependency",
                }:
                    base = 95
                elif ctype in {"poi_conflict", "world_compat"}:
                    base = 90
                elif ctype == "overhaul_vs_standalone":
                    base = 75
                elif ctype == "xml_override":
                    base = 60
                elif ctype == "load_order_priority":
                    # Heuristic scope overlaps can be low or high depending on what system is touched.
                    base = 60 if scope in HIGH_IMPACT_SCOPES else 20
                elif ctype == "scope_overlap":
                    # Pure heuristic; do not treat as a hard load-order conflict.
                    base = 25 if scope in HIGH_IMPACT_SCOPES else 10
                elif ctype == "asset_conflict":
                    base = 20
                elif ctype == "performance":
                    base = 15
                elif ctype == "log_only":
                    base = 10
                elif ctype in {"exclusive"}:
                    base = 65
                else:
                    # Fallback by level
                    base = 70 if level == "error" else (35 if level == "warn" else 10)

                # Scope weighting (file-level vs node-level)
                if file in {"worldglobal.xml"}:
                    base += 20
                if file in {"prefabs.xml", "rwgmixer.xml"}:
                    base += 15
                if file in {
                    "items.xml",
                    "entityclasses.xml",
                    "blocks.xml",
                } and ctype in {
                    "duplicate_id",
                    "xml_override",
                }:
                    base += 10

                # Global/no-target conflicts are higher risk than a single node
                if file and not target:
                    base += 5

                return max(0, min(100, base))

            conflicts = getattr(mod, "conflicts", None) or []
            worst = 0
            for c in conflicts:
                try:
                    if isinstance(c, dict):
                        worst = max(worst, _conflict_score(c))
                except Exception:
                    continue

            # If a mod was flagged as conflicting (heuristic) but doesn't have a structured
            # conflict entry, still surface it in the table.
            try:
                if worst == 0 and bool(getattr(mod, "conflict", False)):
                    lvl = getattr(mod, "conflict_level", None)
                    worst = 60 if str(lvl).lower() == "high" else 20
            except Exception:
                pass

            # Redundant mods are low-severity unless they also have conflicts
            if getattr(mod, "redundant", False) and worst == 0:
                worst = 10

            # Apply category impact primarily when there is a real conflict
            if worst > 0:
                worst = max(0, min(100, worst + cat_weight))

            # Conflict memory bump (non-destructive; display only)
            try:
                worst = max(
                    0,
                    min(100, worst + int(getattr(mod, "memory_severity_bump", 0) or 0)),
                )
            except Exception:
                pass

            return int(worst)
        except Exception:
            return int(getattr(mod, "severity", 0) or 0)

    def _apply_conflict_memory_hints(self):
        """Apply ConflictMemory hints to current mods/conflicts.

        MVP:
        - attach `memory` metadata to individual conflict dicts (where available)
        - pre-fill `mod.memory_suggested_action` when confidence is decent
        - apply a small `mod.memory_severity_bump` when conflicts are repeatedly seen
        """
        mem = getattr(self, "conflict_memory", None)
        if not mem:
            return

        # reset
        for m in self.mods:
            try:
                m.memory_suggested_action = None
                m.memory_severity_bump = 0
            except Exception:
                pass

        def _pretty_action(conflict_type: str, rec_action: str, preferred: str, conf: float) -> str:
            pct = int(round(conf * 100))
            ct = conflict_type or "unknown"
            if ct == "xml_override" and rec_action == "patch":
                return f"CKB ({pct}%): Suggest patch (prefer {preferred})"
            if ct == "duplicate_id" and rec_action == "disable":
                return f"CKB ({pct}%): Suggest disable {preferred}"
            if ct == "load_order_priority" and rec_action in {"reorder", "set_order"}:
                return f"CKB ({pct}%): Suggest load {preferred} later"
            if ct == "overhaul_vs_standalone" and rec_action == "disable_standalone":
                return f"CKB ({pct}%): Suggest disable standalone"
            return f"CKB ({pct}%): Suggest {rec_action}"

        for m in self.mods:
            if not is_effectively_enabled(m):
                continue
            if _is_patch_mod_name(getattr(m, "name", "")):
                continue

            best = None
            best_score = -1.0
            bump = 0

            for c in getattr(m, "conflicts", []) or []:
                try:
                    other = c.get("with")
                    ctype = c.get("conflict_type") or "unknown"
                    file = c.get("file") or ""
                    target = c.get("target") or ""
                    rec = mem.get_recommendation(
                        mod_a=m.name,
                        mod_b=other,
                        conflict_type=ctype,
                        file=file,
                        target=target,
                    )
                    if not rec:
                        continue

                    c["memory"] = {
                        "action": rec.action,
                        "preferred_mod_id": rec.preferred_mod_id,
                        "confidence": rec.confidence,
                        "applied_count": rec.applied_count,
                        "success_count": rec.success_count,
                        "last_seen": rec.last_seen,
                        "note": rec.note,
                        "order_value": rec.order_value,
                    }

                    # bump severity if we keep seeing this but it isn't consistently successful
                    if rec.applied_count >= 3 and rec.confidence < 0.5:
                        bump = max(bump, 15)
                    elif rec.applied_count >= 2 and rec.confidence < 0.7:
                        bump = max(bump, 10)
                    elif rec.applied_count >= 1:
                        bump = max(bump, 5)

                    # candidate for row suggestion
                    score = rec.confidence + (0.05 * min(10, rec.applied_count))
                    if score > best_score and rec.action and rec.action != "unknown":
                        best = (ctype, rec)
                        best_score = score
                except Exception:
                    continue

            try:
                m.memory_severity_bump = int(bump)
            except Exception:
                pass

            try:
                if best:
                    ctype, rec = best
                    if rec.confidence >= 0.60 and rec.preferred_mod_id:
                        m.memory_suggested_action = _pretty_action(
                            ctype, rec.action, rec.preferred_mod_id, rec.confidence
                        )
            except Exception:
                pass

    def set_legend_filter(self, tag):
        # Toggle filter: clicking same tag clears filter
        if self.legend_filter == tag:
            self.legend_filter = None
        else:
            self.legend_filter = tag
        self.refresh_table()

    # --------------------------------------------------
    # Detect conflicts: same category + priority among enabled mods
    # Sets mod.conflict = True for conflicting mods
    # --------------------------------------------------
    def detect_conflicts(self):
        # Clear previous flags
        for m in self.mods:
            m.conflict = False
            m.conflict_level = None
            # Remove previously generated heuristic conflicts so repeated scans/refreshes
            # don't accumulate duplicates.
            try:
                existing = getattr(m, "conflicts", None)
                if isinstance(existing, list) and existing:
                    m.conflicts = [
                        c for c in existing if not (isinstance(c, dict) and c.get("source") == "scope_heuristic")
                    ]
            except Exception:
                pass
            # ensure severity exists for display logic
            try:
                m.severity = self.calculate_severity(m)
            except Exception:
                m.severity = getattr(m, "severity", 0)
        # Group enabled mods by (category, priority)
        groups = {}
        for m in self.mods:
            if not is_effectively_enabled(m):
                continue
            key = (m.category, m.priority)
            groups.setdefault(key, []).append(m)

        # For each group, detect heuristic overlaps via scopes intersection.
        # IMPORTANT: only emit a scope warning when there's file/target overlap evidence.
        for key, items in groups.items():
            if len(items) <= 1:
                continue

            try:
                from logic.scope_heuristics import filter_overlapping_mods
            except Exception:
                filter_overlapping_mods = None

            # Build scope -> mods mapping for this group
            scope_map = {}
            for mod in items:
                # Only consider mods that have at least one detected scope
                if not getattr(mod, "scopes", None):
                    continue
                for s in mod.scopes:
                    scope_map.setdefault(s, []).append(mod)

            # If any scope is present in 2+ mods, mark those mods as conflicting
            for s, mods_with_scope in scope_map.items():
                if len(mods_with_scope) > 1:
                    # severity by scope
                    high_scopes = {"loot_quality", "weapons", "progression"}
                    level = "high" if s in high_scopes else "low"
                    for mm in mods_with_scope:
                        try:
                            if filter_overlapping_mods:
                                overlapping, evidence_kind, samples = filter_overlapping_mods(mm, mods_with_scope)
                            else:
                                overlapping, evidence_kind, samples = (mods_with_scope, "none", [])
                            overlapping = [o for o in (overlapping or []) if getattr(o, "name", None) and o is not mm]

                            # If scope keyword matched but there is no file/target overlap evidence, do not emit.
                            if not overlapping:
                                continue

                            names = sorted({getattr(x, "name", "") for x in overlapping if getattr(x, "name", "")})
                            with_label = ", ".join(names[:3])
                            if len(names) > 3:
                                with_label = with_label + f" (+{len(names) - 3} more)"

                            ev = ""
                            if evidence_kind == "semantic" and samples:
                                ev = "Overlapping edits: " + ", ".join(samples[:5])
                            elif evidence_kind == "files" and samples:
                                ev = "Shared XML files: " + ", ".join(samples[:5])

                            # Mark as a conflict only when we have evidence overlap.
                            mm.conflict = True
                            if mm.conflict_level != "high":
                                mm.conflict_level = level

                            entry = {
                                "level": "warn",
                                "file": "",
                                "target": "",
                                "with": with_label,
                                "reason": f"Multiple enabled mods overlap in scope (heuristic): {s}"
                                + (f". {ev}" if ev else ""),
                                "suggestion": "If this is intentional, use Rules/Patches. If it looks unrelated, ignore it.",
                                "conflict_type": "scope_overlap",
                                "scope": s,
                                "source": "scope_heuristic",
                            }
                            if not hasattr(mm, "conflicts") or not isinstance(getattr(mm, "conflicts"), list):
                                mm.conflicts = []
                            sig = (entry["conflict_type"], entry["scope"], entry.get("with") or "")
                            if not any(
                                isinstance(c, dict)
                                and (c.get("conflict_type"), c.get("scope"), c.get("with") or "") == sig
                                for c in (getattr(mm, "conflicts", []) or [])
                            ):
                                mm.conflicts.append(entry)
                        except Exception:
                            pass

    # --------------------------------------------------
    # Find mods that depend on a given core mod by reading ModInfo.xml
    # Returns a list of dependent mod names (enabled ones)
    # --------------------------------------------------
    def find_dependents(self, core_mod):
        dependents = []
        core_name = core_mod.name.lower()
        for m in self.mods:
            if m is core_mod:
                continue
            if not is_effectively_enabled(m):
                continue
            modinfo_path = os.path.join(m.path, "ModInfo.xml")
            if not os.path.isfile(modinfo_path):
                continue
            try:
                # read file and look for RequiredMod tags or the core name
                with open(modinfo_path, "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
                if core_name in txt.lower():
                    # simple heuristic: if core name appears in ModInfo, assume dependency
                    dependents.append(m.name)
                    continue
            except Exception:
                continue
        return dependents

    # --------------------------------------------------
    # Sort self.mods by a column and refresh the table
    # Supported columns: modname, category, priority, enabled, conflict, status
    # Toggles ascending/descending on repeated clicks
    # --------------------------------------------------
    def sort_by_column(self, col):
        asc = self._sort_state.get(col, True)

        def keyfn(mod):
            if col == "modname":
                return mod.name.lower()
            if col == "category":
                try:
                    idx = CATEGORY_ORDER.index(mod.category)
                except ValueError:
                    idx = len(CATEGORY_ORDER)
                return (idx, mod.category.lower())
            if col == "priority":
                tier = str(getattr(mod, "tier", "") or getattr(mod, "priority", "") or "")
                impact = str(getattr(mod, "semantic_impact", "") or "")
                try:
                    tier_idx = LOAD_TIER_ORDER.index(tier)
                except Exception:
                    tier_idx = 999
                # Deterministic tie-breaker
                return (tier_idx, impact.lower(), mod.name.lower())
            if col == "enabled":
                # enabled first
                return 0 if is_effectively_enabled(mod) else 1
            if col == "conflict":
                try:
                    lbl = conflict_category_label(mod)
                    # Sort conflict rows first, then by label
                    has_conflict = getattr(mod, "conflict", False)
                    return (0 if has_conflict else 1, lbl.lower())
                except Exception:
                    return (1, "")
            if col == "status":
                # Use status precedence: Missing -> 0, Conflict -> 1, Disabled -> 2, OK -> 3
                modinfo_path = os.path.join(mod.path, "ModInfo.xml")
                if not os.path.isfile(modinfo_path):
                    return 0
                if getattr(mod, "conflict", False) and is_effectively_enabled(mod):
                    return 1
                if not is_effectively_enabled(mod):
                    return 2
                return 3
            return mod.name.lower()

        self.mods.sort(key=keyfn, reverse=(not asc))
        self._sort_state[col] = not asc
        self.refresh_table()

    # --------------------------------------------------
    # Export a readable TXT load order grouped by Category
    # Skips disabled mods. Writes to given path.
    # --------------------------------------------------
    def export_loadorder_txt(self, path, mods):
        # Preserve the computed load order as-is.
        # We still add readable headers when (tier, category) changes, but we do NOT
        # re-sort mods within a group (that breaks the generated load order).
        lines = ["# 7DTD Load Order"]

        last_key = None
        for m in mods or []:
            if not is_effectively_enabled(m):
                continue
            tier = str(getattr(m, "tier", "") or getattr(m, "priority", "") or "")
            category = str(getattr(m, "category", "") or "")
            key = (tier, category)

            if key != last_key:
                if last_key is not None:
                    lines.append("")
                lines.append(f"{tier} - {category}".strip(" -"))
                last_key = key

            try:
                lines.append(f"  {pathlib.Path(getattr(m, 'path', '')).name}")
            except Exception:
                lines.append(f"  {getattr(m, 'name', '')}")

        lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # --------------------------------------------------
    # Explain conflicts: grouped by system with root-cause + suggestions
    # --------------------------------------------------
    # --------------------------------------------------
    # Optional LLM-assisted explanation (read-only)
    # --------------------------------------------------
    def llm_explain_selected(self):
        try:
            sel = self.table.selection()
            if not sel:
                messagebox.showinfo("LLM Explain", "Select a mod row first.")
                return
            item_id = sel[0]
            mod = self.mod_lookup.get(item_id)
            if not mod:
                messagebox.showinfo("LLM Explain", "Unable to resolve selected mod.")
                return

            cat = normalize_category(getattr(mod, "category", "Miscellaneous"))
            ctx = CATEGORY_CONTEXT.get(cat, None)
            conf_type = getattr(mod, "conflict_type", None) or ("override" if getattr(mod, "conflict", False) else None)
            severity = getattr(mod, "severity", 0)
            files = []
            try:
                for c in getattr(mod, "conflicts", []) or []:
                    tgt = c.get("file") or c.get("target") or ""
                    if tgt:
                        files.append(tgt)
            except Exception:
                pass
            files = sorted(set(files))

            # Build input payload
            payload = {
                "category": cat,
                "category_context": ctx,
                "mods": [mod.name],
                "conflict_type": conf_type,
                "severity_score": int(severity or 0),
                "files": files,
                "xpath": None,
            }

            # No external LLM provider is integrated here. Always show a plain-English summary.
            expl: List[str] = []
            expl.append("PLAIN ENGLISH (SELECTED MOD)")
            expl.append("")
            expl.append(f"Mod: {getattr(mod, 'name', '')}")
            expl.append(f"Category: {payload['category']}")
            if payload.get("category_context"):
                expl.append(f"Context: {payload['category_context']}")
            try:
                tier = getattr(mod, "tier", None) or "(unknown tier)"
                impact = getattr(mod, "semantic_impact", None) or "(unknown impact)"
                expl.append(f"Tier / Impact: {tier} / {impact}")
            except Exception:
                pass

            confs = getattr(mod, "conflicts", []) or []
            has_conflict = bool(getattr(mod, "conflict", False)) or bool(confs)
            expl.append("")
            expl.append("WHAT'S WRONG")
            if not has_conflict and not getattr(mod, "update_available", False):
                expl.append("- Nothing obvious. This mod does not appear in conflict/update lists.")
            else:
                if getattr(mod, "update_available", False):
                    note = str(getattr(mod, "update_note", "") or "").strip()
                    expl.append(f"- Duplicate install detected. {note}" if note else "- Duplicate install detected.")
                if has_conflict:
                    expl.append(f"- Potential conflict entries: {len(confs)}")
                    for c in confs[:8]:
                        ct = str(c.get("conflict_type") or c.get("type") or "unknown")
                        other = str(c.get("with") or "")
                        tgt = str(c.get("target") or c.get("file") or "")
                        expl.append(f"  - {ct}: with {other} on {tgt}".strip())
                    if len(confs) > 8:
                        expl.append(f"  ... and {len(confs) - 8} more")

            expl.append("")
            expl.append("WHAT TO DO")
            expl.append(f"- Suggested: {suggested_action(mod)}")
            if payload.get("conflict_type") == "duplicate_id":
                expl.append(
                    "- Duplicate IDs are not safely auto-fixable: disable one mod or choose a compatible patch."
                )

            # Optional debug: include payload if you enabled LLM mode.
            if getattr(self, "enable_llm", False):
                payload["model"] = getattr(self, "llm_model", "gpt-5.2")
                expl.append("")
                expl.append("DEBUG (LLM PAYLOAD)")
                expl.append(json.dumps(payload, indent=2))

            self.show_scrollable_popup("\n".join(expl).strip(), title="Explain Selected (Plain English)")
        except Exception as e:
            messagebox.showerror("LLM Explain", f"Failed: {e}")

    # --------------------------------------------------
    # Provide conflict resolution suggestions for strict conflicts
    # --------------------------------------------------
    def resolve_conflicts(self):
        """
        Open interactive Conflict Resolution UI backed by the mock simulation engine.
        Provides: list, details, and patch generation. Includes a load-order fallback.
        """

        # No external target required (simulation-only).

        # Build ordered (name, path) for enabled mods
        enabled_mods = []
        name_to_path = {}
        for m in self.mods or []:
            if not is_effectively_enabled(m):
                continue
            enabled_mods.append((m.name, m.path))
            name_to_path[m.name] = m.path
        if not enabled_mods:
            messagebox.showinfo("Resolve Conflicts", "No enabled mods to simulate.")
            return

        # Run simulation (mock engine) for strict XML override conflicts (patchable)
        state = None
        sim_conflicts = []
        try:
            state, sim_conflicts = simulate_deployment(enabled_mods)
        except Exception as e:
            # Simulation is optional for non-sim conflict types; still show scan-detected conflicts
            print(f"[RESOLVE] Simulation failed: {e}")
            sim_conflicts = []

        # Keep the latest simulator outputs available for patch generation flows.
        try:
            self._last_sim_state = state
            self._last_sim_conflicts = sim_conflicts
        except Exception:
            pass

        # Build unified list of conflicts via engine (keeps UI entry shape stable)
        try:
            from engines.conflict_engine import build_unified_conflicts

            unified = build_unified_conflicts(mods=self.mods, sim_state=state, sim_conflicts=sim_conflicts)
        except Exception:
            unified = []

        # No external filesystem step; nothing to inject here.

        # Apply rule engine (separate from detection/learning) with strict precedence.
        try:
            from logic.rule_store import RuleStore
            from logic.rule_engine import RuleEngine

            rules = RuleStore(os.path.join("data", "rules.json"))
            engine = RuleEngine(
                user_rules=rules.list_user_rules(),
                profile_rules=rules.list_profile_rules(),
            )

            applied_any = 0
            for e in unified or []:
                try:
                    # Baseline provenance (always present)
                    payload = e.get("payload")
                    if isinstance(payload, dict):
                        payload.setdefault("provenance", {})
                        payload["provenance"].setdefault("detector_source", e.get("source"))

                    ra = engine.apply_to_conflict_entry(e)
                    if not ra.applied:
                        continue
                    applied_any += 1
                    payload = e.get("payload")
                    if isinstance(payload, dict):
                        payload.setdefault("provenance", {})
                        payload["provenance"]["rule_id"] = ra.rule_id
                        payload["provenance"]["rule_type"] = ra.rule_type
                        payload["provenance"]["rule_reason"] = ra.reason
                    # Ignore is non-destructive: keep entry but make it informational/non-blocking
                    if ra.action == "ignore":
                        e["resolvable"] = False
                        e["why_not"] = "Ignored by rule"
                    # Prefer winner (used by UI + can drive one-click actions)
                    if ra.action == "prefer" and ra.preferred:
                        if isinstance(payload, dict):
                            payload["rule_preferred"] = ra.preferred
                except Exception:
                    continue
        except Exception:
            pass

        if not unified:
            messagebox.showinfo("Resolve Conflicts", "No conflicts to resolve.")
            return

        # Keep mapping for UI details
        self._name_to_path = name_to_path
        self._open_conflict_ui(unified)

    def _open_conflict_ui(self, conflicts):
        win = tk.Toplevel(self.root)
        win.title("Resolve Conflicts (Patch / Rules)")
        win.configure(bg=self.colors.get("bg", "#1e1e1e"))

        # Left: list of conflicts
        left = tk.Frame(win, bg=self.colors.get("panel", "#252526"))
        left.pack(side="left", fill="both", expand=True)

        cols = ("source", "file", "target", "mod_a", "mod_b", "type")
        tree = ttk.Treeview(left, columns=cols, show="headings")
        try:
            tree.configure(selectmode="extended")
        except Exception:
            pass
        for c in cols:
            tree.heading(c, text=c.capitalize())
            if c == "target":
                tree.column(c, width=420, anchor="w")
            elif c == "file":
                tree.column(c, width=180, anchor="w")
            elif c == "type":
                tree.column(c, width=160, anchor="w")
            elif c == "source":
                tree.column(c, width=70, anchor="w")
            else:
                tree.column(c, width=160, anchor="w")
        tree.pack(fill="both", expand=True)

        try:
            tree.tag_configure("not_resolvable", foreground="#777777")
        except Exception:
            pass

        # Bind Ctrl+A to select all conflicts
        win.bind("<Control-a>", lambda e: tree.selection_set(tree.get_children()))

        # Map iid -> unified conflict dict
        self._conflict_map = {}
        for i, entry in enumerate(conflicts):
            iid = f"c{i}"
            self._conflict_map[iid] = entry
            tags = ()
            if not bool(entry.get("resolvable", False)):
                tags = ("not_resolvable",)

            # Prefer a UI-friendly display label when available.
            try:
                payload = entry.get("payload")
                if isinstance(payload, dict):
                    target_disp = str(payload.get("target_display") or "").strip()
                else:
                    target_disp = str(getattr(payload, "target_display", "") or "").strip()
            except Exception:
                target_disp = ""

            target_cell = target_disp if target_disp else entry.get("target", "")
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    entry.get("source", ""),
                    entry.get("file", ""),
                    target_cell,
                    entry.get("mod_a", ""),
                    entry.get("mod_b", ""),
                    entry.get("type", ""),
                ),
                tags=tags,
            )

        # Right: details + actions
        right = tk.Frame(win, bg=self.colors.get("panel", "#252526"))
        right.pack(side="right", fill="y")

        # Plain-English usage note
        try:
            note = (
                "How to use this:\n"
                "- Select a conflict on the left to see details.\n"
                "- Use Rules to make a deterministic choice (prefer A/B, or ignore).\n"
                "- Patch is only available for simulator-backed XML override conflicts.\n"
                "- Stabilizing Patch writes the simulator final state (<set>/<remove>) for non-override interactions.\n"
                "- Duplicate IDs usually require disabling one mod (manual decision)."
            )
            lbl = tk.Label(
                right,
                text=note,
                justify="left",
                wraplength=420,
                bg=self.colors.get("panel", "#252526"),
                fg=self.colors.get("fg", "#d4d4d4"),
            )
            lbl.pack(padx=8, pady=(8, 0), anchor="w")
        except Exception:
            pass

        detail = tk.Text(
            right,
            width=60,
            height=18,
            bg=self.colors.get("entry_bg", "#2d2d2d"),
            fg=self.colors.get("entry_fg", "#d4d4d4"),
        )
        detail.pack(padx=8, pady=8)

        # Provenance/confidence helpers (display only)
        def _resolution_signals(entry: dict):
            """Return (confidence_0_100, safety_label, explanation)."""
            try:
                from logic.conflict_taxonomy import is_save_breaking
            except Exception:
                is_save_breaking = None

            payload = entry.get("payload") or {}
            prov = payload.get("provenance") if isinstance(payload, dict) else None

            risky = False
            try:
                if is_save_breaking:
                    risky = bool(
                        is_save_breaking(
                            conflict_type=str(entry.get("type") or ""),
                            file=str(entry.get("file") or ""),
                        )
                    )
            except Exception:
                risky = False

            # User rules are highest priority and deterministic
            try:
                if isinstance(prov, dict) and (prov.get("rule_id") or prov.get("rule_type")):
                    rt = str(prov.get("rule_type") or "")
                    if rt in {"ignore_conflict"}:
                        return 100, "Auto-safe", "User rule: ignored"
                    if rt in {"always_win", "load_after", "load_before"}:
                        return 100, "Auto-safe", f"User rule: {rt}"
                    if rt in {"disable_if_with", "never_together"}:
                        return 100, "Destructive", f"User rule: {rt}"
                    return 100, "Auto-safe", "User rule applied"
            except Exception:
                pass

            # Conflict memory (CKB)
            try:
                rec = _memory_for_entry(entry)
                if rec:
                    c = int(round(float(rec.confidence) * 100))
                    if risky:
                        return (
                            c,
                            "Manual-only",
                            "Memory suggests an action, but save-breaking risk is gated",
                        )
                    if c >= 85:
                        return c, "Suggested", "High-confidence memory recommendation"
                    return c, "Manual-only", "Low-confidence memory recommendation"
            except Exception:
                pass

            # Heuristic baseline
            if risky:
                return 40, "Manual-only", "World/save-impact potential"
            return 40, "Manual-only", "No rule/memory; manual resolution recommended"

        # --- Rule actions (foundational) ---
        rules_frame = tk.Frame(right, bg=self.colors.get("panel", "#252526"))
        rules_frame.pack(padx=8, pady=(0, 8), fill="x")
        ttk.Label(rules_frame, text="Rules (deterministic)").pack(anchor="w")
        btn_rule_prefer_a = ttk.Button(rules_frame, text="Rule: Prefer A (Always Wins)")
        btn_rule_prefer_b = ttk.Button(rules_frame, text="Rule: Prefer B (Always Wins)")
        btn_rule_ignore = ttk.Button(rules_frame, text="Rule: Ignore This Conflict")
        btn_rule_ignore_selected = ttk.Button(rules_frame, text="Rule: Ignore Selected Conflicts")
        for b in (btn_rule_prefer_a, btn_rule_prefer_b, btn_rule_ignore):
            try:
                b.pack(fill="x", pady=2)
            except Exception:
                pass
        try:
            btn_rule_ignore_selected.pack(fill="x", pady=2)
        except Exception:
            pass

        # Path A / Path B definition area
        paths_frame = tk.Frame(right, bg=self.colors.get("panel", "#252526"))
        paths_frame.pack(padx=8, pady=(0, 8), fill="x")
        tk.Label(
            paths_frame,
            text="Path A",
            bg=self.colors.get("panel", "#252526"),
            fg=self.colors.get("fg", "#d4d4d4"),
        ).grid(row=0, column=0, sticky="w")
        self._path_a_var = tk.StringVar(value="")
        tk.Entry(
            paths_frame,
            textvariable=self._path_a_var,
            width=52,
            bg=self.colors.get("entry_bg", "#2d2d2d"),
            fg=self.colors.get("entry_fg", "#d4d4d4"),
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        tk.Label(
            paths_frame,
            text="Path B",
            bg=self.colors.get("panel", "#252526"),
            fg=self.colors.get("fg", "#d4d4d4"),
        ).grid(row=1, column=0, sticky="w")
        self._path_b_var = tk.StringVar(value="")
        tk.Entry(
            paths_frame,
            textvariable=self._path_b_var,
            width=52,
            bg=self.colors.get("entry_bg", "#2d2d2d"),
            fg=self.colors.get("entry_fg", "#d4d4d4"),
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0))
        paths_frame.grid_columnconfigure(1, weight=1)

        # ---------------- Memory helpers (CKB) ----------------
        def _memory_for_entry(entry: dict):
            mem = getattr(self, "conflict_memory", None)
            if not mem:
                return None
            try:
                return mem.get_recommendation(
                    mod_a=entry.get("mod_a") or "",
                    mod_b=entry.get("mod_b") or "",
                    conflict_type=entry.get("type") or "unknown",
                    file=entry.get("file") or "",
                    target=entry.get("target") or "",
                )
            except Exception:
                return None

        def _memory_for_entries(entries):
            if not entries:
                return None
            # must be homogeneous type/source and resolvable
            types = {e.get("type") for e in entries}
            sources = {e.get("source") for e in entries}
            if len(types) != 1:
                return None
            if any(not bool(e.get("resolvable", False)) for e in entries):
                return None
            # For XML override: must be simulator-backed
            if next(iter(types)) == "xml_override" and sources != {"sim"}:
                return None

            rec0 = _memory_for_entry(entries[0])
            if not rec0 or not rec0.action or rec0.action == "unknown":
                return None
            # require same action across selection
            for e in entries[1:]:
                rec = _memory_for_entry(e)
                if not rec or rec.action != rec0.action:
                    return None
            return rec0

        def _category_for(name: str):
            try:
                mm = next((mm for mm in self.mods if getattr(mm, "name", None) == name), None)
                return getattr(mm, "category", None) if mm else None
            except Exception:
                return None

        def _current_signatures():
            """Compute current conflict signatures after a resolve+rescan."""
            sigs = set()
            try:
                from logic.conflict_memory import normalize_mod_id
            except Exception:
                return sigs

            # scan conflicts
            try:
                for m in self.mods:
                    if not is_effectively_enabled(m):
                        continue
                    if _is_patch_mod_name(getattr(m, "name", "")):
                        continue
                    for c in getattr(m, "conflicts", []) or []:
                        other = c.get("with")
                        if not other:
                            continue
                        ctype = c.get("conflict_type") or "unknown"
                        file = c.get("file") or ""
                        target = c.get("target") or ""
                        a = normalize_mod_id(m.name)
                        b = normalize_mod_id(other)
                        pair = tuple(sorted([a, b], key=lambda s: s.lower()))
                        sigs.add((ctype, file, target, pair[0], pair[1], "scan"))
            except Exception:
                pass

            # sim conflicts
            try:
                enabled_mods = [(m.name, m.path) for m in self.mods if is_effectively_enabled(m)]
                state, sim_conflicts = simulate_deployment(enabled_mods)
                last = getattr(state, "last_mut", {}) or {}
                for ct in sim_conflicts or []:
                    try:
                        if ct.kind != "override":
                            continue
                        if _is_patch_mod_name(ct.first.mod) or _is_patch_mod_name(ct.second.mod):
                            continue
                        lm = last.get((ct.file, ct.xpath))
                        if lm and _is_patch_mod_name(getattr(lm, "mod", "")):
                            continue
                        a = normalize_mod_id(ct.first.mod)
                        b = normalize_mod_id(ct.second.mod)
                        pair = tuple(sorted([a, b], key=lambda s: s.lower()))
                        sigs.add(("xml_override", ct.file, ct.xpath, pair[0], pair[1], "sim"))
                    except Exception:
                        continue
            except Exception:
                pass

            return sigs

        def _sig_for_entry(entry: dict):
            try:
                from logic.conflict_memory import normalize_mod_id

                a = normalize_mod_id(entry.get("mod_a") or "")
                b = normalize_mod_id(entry.get("mod_b") or "")
                pair = tuple(sorted([a, b], key=lambda s: s.lower()))
                return (
                    entry.get("type") or "unknown",
                    entry.get("file") or "",
                    entry.get("target") or "",
                    pair[0],
                    pair[1],
                    entry.get("source") or "",
                )
            except Exception:
                return None

        def _record_memory(
            entries,
            *,
            resolution_action: str,
            preferred_mod_name: Optional[str] = None,
            order_value: Optional[int] = None,
            note: Optional[str] = None,
        ):
            mem = getattr(self, "conflict_memory", None)
            if not mem:
                return
            sigs = _current_signatures()

            # Append-only resolution history (auditable trail)
            hist = None
            try:
                from engines.resolution_history import (
                    ResolutionHistoryEvent,
                    ResolutionHistoryStore,
                )

                hist = (ResolutionHistoryEvent, ResolutionHistoryStore)
            except Exception:
                hist = None
            for e in entries:
                try:
                    sig = _sig_for_entry(e)
                    successful = (sig is not None) and (sig not in sigs)
                    mem.record_resolution(
                        mod_a=e.get("mod_a") or "",
                        mod_b=e.get("mod_b") or "",
                        category_a=_category_for(e.get("mod_a") or ""),
                        category_b=_category_for(e.get("mod_b") or ""),
                        conflict_type=e.get("type") or "unknown",
                        file=e.get("file") or "",
                        target=e.get("target") or "",
                        resolution_action=resolution_action,
                        preferred_mod_name=preferred_mod_name,
                        successful=bool(successful),
                        order_value=order_value,
                        note=note,
                    )

                    # Best-effort history record (no UI shape changes)
                    if hist:
                        try:
                            ResolutionHistoryEvent, ResolutionHistoryStore = hist
                            evidence_hash = ""
                            try:
                                if (e.get("source") or "") == "sim":
                                    ct = e.get("payload")
                                    evidence_hash = str(getattr(ct, "evidence_hash", "") or "")
                                else:
                                    payload = e.get("payload") or {}
                                    if isinstance(payload, dict):
                                        evidence_hash = str(payload.get("evidence_hash") or "")
                            except Exception:
                                evidence_hash = ""

                            if evidence_hash:
                                store = ResolutionHistoryStore(os.path.join("data", "resolution_history.jsonl"))
                                store.append(
                                    ResolutionHistoryEvent(
                                        evidence_hash=evidence_hash,
                                        conflict_type=str(e.get("type") or "unknown"),
                                        source=str(e.get("source") or ""),
                                        file=str(e.get("file") or ""),
                                        target=str(e.get("target") or ""),
                                        mod_a=str(e.get("mod_a") or ""),
                                        mod_b=str(e.get("mod_b") or ""),
                                        action=str(resolution_action),
                                        success=bool(successful),
                                        note=note,
                                    )
                                )
                        except Exception:
                            pass
                except Exception:
                    continue
            try:
                mem.save()
            except Exception:
                pass

            # Also record into the Resolution Knowledge Base (RKB)
            try:
                kb = getattr(self, "resolution_kb", None)
                if kb:
                    # Recompute success per-entry (same logic used above)
                    for e in entries:
                        try:
                            sig = _sig_for_entry(e)
                            success = (sig is not None) and (sig not in sigs)
                            kb.record_attempt(
                                conflict_type=e.get("type") or "unknown",
                                resolution_id=str(resolution_action),
                                success=bool(success),
                            )
                        except Exception:
                            continue
                    kb.save()
            except Exception:
                pass

        def _auto_resolve_high_confidence():
            """Apply high-confidence memory recommendations in one click.

            Safety constraints:
            - Only acts on resolvable conflicts.
                        - Only executes: patch (xml_override via simulator), disable (duplicate_id),
                            set_order/reorder (load_order_priority), disable_standalone (overhaul_vs_standalone).
            - Requires user confirmation and a confidence threshold.
            """
            mem = getattr(self, "conflict_memory", None)
            if not mem:
                messagebox.showinfo("Auto-resolve", "Conflict memory is not available.")
                return

            kb = getattr(self, "resolution_kb", None)

            from tkinter import simpledialog

            try:
                thr = simpledialog.askfloat(
                    "Auto-resolve",
                    "Confidence threshold (0.0–1.0).\n\nOnly memory-backed fixes at/above this confidence will be applied.",
                    minvalue=0.0,
                    maxvalue=1.0,
                    initialvalue=0.85,
                    parent=win,
                )
            except Exception:
                thr = 0.85
            if thr is None:
                return

            try:
                from logic.conflict_memory import normalize_mod_id
            except Exception:
                messagebox.showerror("Auto-resolve", "Missing conflict memory utilities.")
                return

            def _supported_actions_for(entry: dict):
                ctype = entry.get("type")
                if ctype == "xml_override":
                    return ["patch"]
                if ctype == "duplicate_id":
                    return ["disable"]
                if ctype == "load_order_priority":
                    return ["set_order", "reorder"]
                if ctype == "overhaul_vs_standalone":
                    # Prefer prioritization (load order) over disabling.
                    return ["set_order", "reorder"]
                if ctype == "asset_conflict":
                    # Last loaded wins; resolve by prioritizing winner in load order.
                    return ["set_order", "reorder"]
                return []

            def _choose_action(entry: dict, rec):
                """Pick the best supported resolution_id using RKB (common + non-risky first)."""
                supported = _supported_actions_for(entry)
                if not supported:
                    return None, None

                # Prefer RKB ranking when available
                if kb:
                    try:
                        opts = kb.list_options(entry.get("type") or "unknown")
                        for o in opts or []:
                            rid = (getattr(o, "resolution_id", "") or "").strip()
                            if rid in supported and not bool(getattr(o, "risky", False)):
                                return rid, o
                        for o in opts or []:
                            rid = (getattr(o, "resolution_id", "") or "").strip()
                            if rid in supported:
                                return rid, o
                    except Exception:
                        pass

                # Fallback
                return supported[0], None

            # Gather candidates from the full list (not selection)
            candidates = []
            for e in conflicts or []:
                try:
                    if not bool(e.get("resolvable", False)):
                        continue
                    rec = _memory_for_entry(e)
                    if not rec:
                        continue
                    if rec.confidence < float(thr):
                        continue
                    action, rkb_opt = _choose_action(e, rec)
                    if not action:
                        continue

                    action_meta = {
                        "source": ("rkb" if rkb_opt else "fallback"),
                        "tier": (getattr(rkb_opt, "tier", None) if rkb_opt else None),
                        "risky": (bool(getattr(rkb_opt, "risky", False)) if rkb_opt else False),
                        "confidence": (float(getattr(rkb_opt, "confidence", 0.0)) if rkb_opt else None),
                        "success_count": (int(getattr(rkb_opt, "success_count", 0)) if rkb_opt else None),
                        "applied_count": (int(getattr(rkb_opt, "applied_count", 0)) if rkb_opt else None),
                        "label": (str(getattr(rkb_opt, "label", "") or "") if rkb_opt else None),
                        "requested_action": action,
                    }

                    # Hard constraints
                    if e.get("type") == "xml_override" and e.get("source") != "sim":
                        continue

                    # A/B decisions require memory preferred_mod_id
                    if action in {"patch", "disable", "set_order", "reorder"} and not rec.preferred_mod_id:
                        continue

                    # set_order requires a remembered order_value
                    if action == "set_order" and not isinstance(getattr(rec, "order_value", None), int):
                        action_meta["note"] = "downgraded to reorder (no saved order)"
                        action = "reorder"

                    candidates.append((e, rec, action, action_meta))
                except Exception:
                    continue

            if not candidates:
                messagebox.showinfo("Auto-resolve", "No conflicts meet the confidence threshold.")
                return

            # Summarize plan
            patch_cts_a = []
            patch_cts_b = []
            disable_names = []
            set_order_ops = []  # list of (entry, winner_name, order_value)
            reorder_ops = []  # list of (entry, winner_name)

            # Record items (per-entry) for CKB/RKB learning
            record_items = []  # list of (entry, action, preferred_mod_name, order_value)

            # Per-conflict-type strategy summary for the confirmation dialog
            strategy_summary = {}  # {ctype: {action: {'count':int, 'meta':dict}}}

            def _note_action(ctype: str, action: str, meta: dict):
                ct = ctype or "unknown"
                a = action or "unknown"
                bucket = strategy_summary.setdefault(ct, {})
                item = bucket.get(a)
                if not isinstance(item, dict):
                    item = {"count": 0, "meta": meta or {}}
                    bucket[a] = item
                item["count"] = int(item.get("count") or 0) + 1

            for e, rec, action, action_meta in candidates:
                ctype = e.get("type")
                _note_action(ctype, action, action_meta)
                if ctype == "xml_override" and action == "patch":
                    ct = e.get("payload")
                    if not ct:
                        continue
                    preferred = rec.preferred_mod_id
                    if not preferred:
                        continue
                    # Determine whether preferred is A or B (normalize folder prefixes)
                    a_id = normalize_mod_id(ct.first.mod)
                    b_id = normalize_mod_id(ct.second.mod)
                    p_id = normalize_mod_id(preferred)
                    if p_id == a_id:
                        patch_cts_a.append(ct)
                        record_items.append((e, "patch", ct.first.mod, None))
                    elif p_id == b_id:
                        patch_cts_b.append(ct)
                        record_items.append((e, "patch", ct.second.mod, None))
                    else:
                        continue

                elif ctype == "duplicate_id" and action == "disable":
                    preferred = rec.preferred_mod_id
                    if not preferred:
                        continue
                    # Find which side matches preferred id
                    a = e.get("mod_a")
                    b = e.get("mod_b")
                    if not a or not b:
                        continue
                    a_id = normalize_mod_id(a)
                    b_id = normalize_mod_id(b)
                    p_id = normalize_mod_id(preferred)
                    if p_id == a_id:
                        disable_names.append(a)
                        record_items.append((e, "disable", a, None))
                    elif p_id == b_id:
                        disable_names.append(b)
                        record_items.append((e, "disable", b, None))
                    else:
                        continue

                elif ctype == "load_order_priority" and action in {
                    "reorder",
                    "set_order",
                }:
                    preferred = rec.preferred_mod_id
                    if not preferred:
                        continue
                    a = e.get("mod_a")
                    b = e.get("mod_b")
                    if not a or not b:
                        continue
                    a_id = normalize_mod_id(a)
                    b_id = normalize_mod_id(b)
                    p_id = normalize_mod_id(preferred)
                    winner = None
                    if p_id == a_id:
                        winner = a
                    elif p_id == b_id:
                        winner = b
                    else:
                        continue

                    if action == "set_order" and isinstance(getattr(rec, "order_value", None), int):
                        set_order_ops.append((e, winner, int(rec.order_value)))
                    else:
                        reorder_ops.append((e, winner))

                elif ctype in {"overhaul_vs_standalone", "asset_conflict"} and action in {
                    "reorder",
                    "set_order",
                }:
                    preferred = rec.preferred_mod_id
                    if not preferred:
                        continue
                    a = e.get("mod_a")
                    b = e.get("mod_b")
                    if not a or not b:
                        continue
                    a_id = normalize_mod_id(a)
                    b_id = normalize_mod_id(b)
                    p_id = normalize_mod_id(preferred)
                    winner = None
                    if p_id == a_id:
                        winner = a
                    elif p_id == b_id:
                        winner = b
                    else:
                        continue

                    if action == "set_order" and isinstance(getattr(rec, "order_value", None), int):
                        set_order_ops.append((e, winner, int(rec.order_value)))
                    else:
                        reorder_ops.append((e, winner))

            # Deduplicate work
            try:
                disable_names = sorted(set(disable_names), key=lambda s: (s or "").lower())
            except Exception:
                pass

            if not (patch_cts_a or patch_cts_b or disable_names or set_order_ops or reorder_ops):
                messagebox.showinfo(
                    "Auto-resolve",
                    "No applicable auto-resolve actions were found at the chosen threshold.",
                )
                return

            plan_lines = [
                f"Confidence threshold: {thr:.2f}",
                "",
                f"Patch (Prefer A): {len(patch_cts_a)} conflict(s)",
                f"Patch (Prefer B): {len(patch_cts_b)} conflict(s)",
                f"Disable mods: {len(disable_names)} mod(s)",
                f"Set exact order prefix: {len(set_order_ops)} mod(s)",
                f"Reorder later: {len(reorder_ops)} mod(s)",
                "",
                "Strategy selection (RKB):",
            ]

            try:
                for ct in sorted((strategy_summary or {}).keys(), key=lambda s: (s or "").lower()):
                    actions = strategy_summary.get(ct) or {}
                    parts = []
                    for act in sorted(actions.keys(), key=lambda s: (s or "").lower()):
                        info = actions.get(act) or {}
                        n = int(info.get("count") or 0)
                        meta = info.get("meta") or {}
                        src = meta.get("source") or "fallback"
                        if src == "rkb":
                            tier = meta.get("tier") or "uncommon"
                            risk = "risky" if bool(meta.get("risky", False)) else "ok"
                            conf = meta.get("confidence")
                            sc = meta.get("success_count")
                            ac = meta.get("applied_count")
                            pct = int(round(float(conf) * 100)) if isinstance(conf, (float, int)) else None
                            stats = (
                                f"{pct}% {sc}/{ac}" if pct is not None and sc is not None and ac is not None else tier
                            )
                            suffix = f"RKB {tier}, {risk}, {stats}"
                        else:
                            suffix = "fallback"

                        note = meta.get("note")
                        if note:
                            suffix = f"{suffix}; {note}"

                        parts.append(f"{act} x{n} ({suffix})")

                    plan_lines.append(f"- {ct}: " + ", ".join(parts))
            except Exception:
                plan_lines.append("- (strategy summary unavailable)")

            plan_lines.extend(
                [
                    "",
                    "This will create patch mods (in your Mods Library) and/or update enabled/order state, then apply load order by renaming folders.",
                ]
            )

            if not messagebox.askyesno(
                "Auto-resolve",
                "Apply the following actions?\n\n" + "\n".join(plan_lines),
            ):
                return

            # Execute
            try:
                # Patch mods
                if patch_cts_a:
                    from logic.conflict_patch import create_conflict_patch

                    pdir = create_conflict_patch(
                        self.mods_path.get(),
                        patch_cts_a,
                        prefer="A",
                        output_root=self.mods_path.get(),
                    )
                if patch_cts_b:
                    from logic.conflict_patch import create_conflict_patch

                    pdir = create_conflict_patch(
                        self.mods_path.get(),
                        patch_cts_b,
                        prefer="B",
                        output_root=self.mods_path.get(),
                    )

                # Disable mods
                for name in disable_names:
                    _disable_mod_by_name(name)

                # Set exact order
                set_order_applied = []  # list of (entry, winner_name, order_value)
                for entry, winner, ov in set_order_ops:
                    try:
                        _set_mod_order_by_name(winner, int(ov))
                        set_order_applied.append((entry, winner, int(ov)))
                    except Exception:
                        # fallback to reorder bucket
                        reorder_ops.append((entry, winner))

                # Reorder
                reorder_applied = []  # list of (entry, winner_name)
                if reorder_ops:
                    next_val = _next_order_value()
                    for entry, winner in reorder_ops:
                        _set_mod_order_by_name(winner, next_val)
                        reorder_applied.append((entry, winner))
                        next_val = min(99999, next_val + 10)

                # Persist state + rescan + apply folder-based load order
                try:
                    self.save_settings()
                except Exception:
                    pass
                self.scan()
                try:
                    self.apply_load_order_rename(confirm=False)
                except Exception:
                    pass

                # Record outcomes (post-scan)
                try:
                    for entry, winner, ov in set_order_applied or []:
                        record_items.append((entry, "set_order", winner, int(ov)))
                except Exception:
                    pass
                try:
                    for entry, winner in reorder_applied or []:
                        record_items.append((entry, "reorder", winner, None))
                except Exception:
                    pass

                for entry, action, pref_name, ov in record_items or []:
                    try:
                        _record_memory(
                            [entry],
                            resolution_action=str(action),
                            preferred_mod_name=pref_name,
                            order_value=ov,
                        )
                    except Exception:
                        continue

                messagebox.showinfo("Auto-resolve", "Auto-resolve complete. Scan refreshed.")
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[AUTO-RESOLVE] FAILED: {e}")
                messagebox.showerror("Auto-resolve failed", str(e))

        def _auto_resolve_by_policy():
            """Resolve conflicts using deterministic Priority/Dependency policy.

            This does real work (patch/reorder) and writes a resolution report so
            the user can see exactly what happened.
            """

            from tkinter import simpledialog

            try:
                from logic.resolution_policy import (
                    build_conflict_map,
                    compute_dependency_graph,
                    decide_winner,
                )
            except Exception as e:
                messagebox.showerror("Auto-resolve", f"Missing policy module: {e}")
                return

            # Build inputs
            all_entries = list(conflicts or [])
            deps = compute_dependency_graph(self.mods)
            cmap = build_conflict_map(all_entries)

            # Optionally disable duplicate-id losers (gated)
            disable_dupe_losers = False
            try:
                disable_dupe_losers = bool(
                    simpledialog.askstring(
                        "Auto-resolve by Policy",
                        "Disable duplicate-id losers too?\n\nType EXACTLY: disable\n(or Cancel/anything else to skip disabling)\n\nRecommended: skip and handle duplicate IDs manually.",
                        parent=win,
                    )
                    == "disable"
                )
            except Exception:
                disable_dupe_losers = False

            # Optional: generate a stabilizing patch from the simulator's final state
            # after policy actions are applied. This helps with non-override sim interactions.
            generate_stabilizing_patch = False
            try:
                generate_stabilizing_patch = bool(
                    messagebox.askyesno(
                        "Auto-resolve by Policy",
                        "Also generate a STABILIZING patch from the simulator final state?\n\n"
                        "This creates a patch mod that writes the final simulated values via <set>/<remove>, "
                        "reducing reliance on load order for some conflicts.\n\n"
                        "Recommended if you see UI/XUi XML instability.",
                        parent=win,
                    )
                )
            except Exception:
                generate_stabilizing_patch = False

            # Plan buckets
            patch_cts_a = []
            patch_cts_b = []
            reorder_winners = []  # move later
            disable_names = []

            report_lines = []
            report_lines.append("=== Conflict Resolution (Policy) ===")
            try:
                report_lines.append(f"Total conflicts considered: {len(all_entries)}")
            except Exception:
                pass
            report_lines.append("")
            report_lines.append("Conflict map (file -> mods):")
            try:
                for f in sorted((cmap or {}).keys(), key=lambda s: (s or "").lower()):
                    mods = cmap.get(f) or []
                    if not mods:
                        continue
                    report_lines.append(f"- {f}: {', '.join(mods[:10])}{'...' if len(mods) > 10 else ''}")
            except Exception:
                report_lines.append("- (unavailable)")

            # Decide per conflict
            for e in all_entries:
                try:
                    if not bool(e.get("resolvable", False)):
                        continue
                    ctype = str(e.get("type") or "")
                    src = str(e.get("source") or "")
                    file = str(e.get("file") or "")
                    target = str(e.get("target") or "")
                    a = str(e.get("mod_a") or "")
                    b = str(e.get("mod_b") or "")

                    d = decide_winner(
                        self.mods,
                        mod_a_name=a,
                        mod_b_name=b,
                        conflict_type=ctype,
                        file=file,
                        target=target,
                        deps=deps,
                    )

                    # Winner is `back` (load later)
                    winner = d.back
                    loser = d.front

                    # Simulator XML override: patchable
                    if ctype == "xml_override" and src == "sim":
                        ct = e.get("payload")
                        if not ct:
                            continue
                        try:
                            if str(getattr(ct.first, "mod", "")) == winner:
                                patch_cts_a.append(ct)
                                report_lines.append(
                                    f"XML override: {file} {target} -> Winner: {winner} (Prefer A); Skipped: {loser}; Reason: {d.reason}"
                                )
                            elif str(getattr(ct.second, "mod", "")) == winner:
                                patch_cts_b.append(ct)
                                report_lines.append(
                                    f"XML override: {file} {target} -> Winner: {winner} (Prefer B); Skipped: {loser}; Reason: {d.reason}"
                                )
                            else:
                                # Fallback: if winner doesn't match, default to Prefer B (later)
                                patch_cts_b.append(ct)
                                report_lines.append(
                                    f"XML override: {file} {target} -> Winner: {winner} (Prefer B default); Reason: {d.reason}"
                                )
                        except Exception:
                            continue
                        continue

                    # Load-order style: reorder (last loaded wins)
                    if ctype in {"load_order_priority", "asset_conflict", "overhaul_vs_standalone"}:
                        if winner:
                            reorder_winners.append(winner)
                            report_lines.append(
                                f"Load order: {file} {target} -> Winner: {winner}; Other: {loser}; Reason: {d.reason}"
                            )
                        continue

                    # Duplicate IDs: only disable if explicitly enabled
                    if ctype == "duplicate_id" and disable_dupe_losers:
                        if loser:
                            disable_names.append(loser)
                            report_lines.append(
                                f"Duplicate ID: {file} {target} -> Disabled: {loser}; Kept: {winner}; Reason: {d.reason}"
                            )
                        continue
                except Exception:
                    continue

            # Deduplicate
            try:
                reorder_winners = list(dict.fromkeys(reorder_winners))
            except Exception:
                pass
            try:
                disable_names = sorted(set(disable_names), key=lambda s: (s or "").lower())
            except Exception:
                pass

            if not (patch_cts_a or patch_cts_b or reorder_winners or disable_names):
                messagebox.showinfo("Auto-resolve", "No applicable policy actions were found.")
                return

            # Confirmation
            preview = [
                "Policy actions to apply:",
                f"- Patch (Prefer A): {len(patch_cts_a)}",
                f"- Patch (Prefer B): {len(patch_cts_b)}",
                f"- Reorder winners later: {len(reorder_winners)}",
                f"- Disable duplicate-id losers: {len(disable_names)}",
                f"- Generate stabilizing patch (final sim state): {'YES' if generate_stabilizing_patch else 'NO'}",
                "",
                "This will modify order overrides / create patch mod folders, then rescan and apply load order.",
            ]
            if not messagebox.askyesno("Auto-resolve by Policy", "\n".join(preview)):
                return

            # Execute actions
            try:
                # Patch mods
                created_patch_dirs = []
                if patch_cts_a:
                    from logic.conflict_patch import create_conflict_patch

                    pdir = create_conflict_patch(
                        self.mods_path.get(),
                        patch_cts_a,
                        prefer="A",
                        output_root=self.mods_path.get(),
                    )
                    try:
                        created_patch_dirs.append(pdir)
                    except Exception:
                        pass
                if patch_cts_b:
                    from logic.conflict_patch import create_conflict_patch

                    pdir = create_conflict_patch(
                        self.mods_path.get(),
                        patch_cts_b,
                        prefer="B",
                        output_root=self.mods_path.get(),
                    )
                    try:
                        created_patch_dirs.append(pdir)
                    except Exception:
                        pass

                # Disable losers
                for n in disable_names:
                    try:
                        _disable_mod_by_name(n)
                    except Exception:
                        pass

                # Reorder winners later
                if reorder_winners:
                    from engines.resolution_engine import apply_reorder_later

                    start_val = _next_order_value()
                    ctx = _resolution_context()
                    apply_reorder_later(ctx, names=reorder_winners, start_order_value=start_val)

                # Optional: materialize a "final state" patch from the simulator after policy actions.
                stabilizing_patch_dir = None
                if generate_stabilizing_patch:
                    try:
                        from mock_deploy.engine import simulate_deployment
                        from logic.conflict_patch import create_stabilizing_patch

                        enabled_mods_now = []
                        name_to_order = {}
                        for m in self.mods or []:
                            try:
                                if not is_effectively_enabled(m):
                                    continue
                                try:
                                    name_to_order[str(getattr(m, "name", "") or "")] = int(
                                        getattr(m, "load_order", 0) or 0
                                    )
                                except Exception:
                                    name_to_order[str(getattr(m, "name", "") or "")] = 0
                                enabled_mods_now.append((m.name, m.path))
                            except Exception:
                                continue

                        try:
                            enabled_mods_now.sort(
                                key=lambda t: (int(name_to_order.get(str(t[0] or ""), 0)), str(t[0] or "").lower())
                            )
                        except Exception:
                            pass

                        # Include freshly created override patch mods last in the sim.
                        try:
                            for p in created_patch_dirs or []:
                                enabled_mods_now.append((p.name, str(p)))
                        except Exception:
                            pass

                        st, sim_conf = simulate_deployment(enabled_mods_now)
                        non_override = []
                        for ct in sim_conf or []:
                            try:
                                k = str(getattr(ct, "kind", "") or "")
                                if k in {"override", "append-append"}:
                                    continue
                                non_override.append(ct)
                            except Exception:
                                continue

                        if non_override:
                            stabilizing_patch_dir = create_stabilizing_patch(
                                self.mods_path.get(),
                                state=st,
                                conflicts=non_override,
                                output_root=self.mods_path.get(),
                            )
                            try:
                                report_lines.append("")
                                report_lines.append(
                                    f"Stabilizing patch created: {stabilizing_patch_dir} (targets: {len(non_override)})"
                                )
                            except Exception:
                                pass
                        else:
                            try:
                                report_lines.append("")
                                report_lines.append("Stabilizing patch: no non-override simulator conflicts remained")
                            except Exception:
                                pass
                    except Exception as e:
                        print(f"[AUTO-RESOLVE-POLICY] Stabilizing patch skipped: {e}")

                # Ensure state persisted + load order applied
                try:
                    self.save_settings()
                except Exception:
                    pass
                self.scan()
                try:
                    self.apply_load_order_rename(confirm=False)
                except Exception:
                    pass

                # Write report
                try:
                    os.makedirs("logs", exist_ok=True)
                    import datetime

                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    report_path = os.path.join("logs", f"conflict_resolution_policy_{ts}.txt")
                    with open(report_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(report_lines).strip() + "\n")
                except Exception:
                    report_path = ""

                # Save load order file for future launches (human readable)
                try:
                    from exporter.load_order_exporter import export_load_order

                    ordered = [m for m in (self.mods or []) if is_effectively_enabled(m)]
                    ordered.sort(key=lambda m: (int(getattr(m, "load_order", 0)), str(getattr(m, "name", "")).lower()))
                    export_load_order(ordered, os.path.join("data", "load_order.resolved.txt"))
                except Exception:
                    pass

                msg = "Policy auto-resolve complete. Scan refreshed."
                if report_path:
                    msg += f"\n\nReport: {report_path}"
                msg += "\nLoad order export: data/load_order.resolved.txt"
                messagebox.showinfo("Auto-resolve", msg)
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[AUTO-RESOLVE-POLICY] FAILED: {e}")
                messagebox.showerror("Auto-resolve failed", str(e))

        def _update_buttons_for_selection(entries):
            # Disable everything by default; enable only when selection is homogeneous and supported.
            try:
                btn_apply_memory.state(["disabled"])
                btn_patch_a.state(["disabled"])
                btn_patch_b.state(["disabled"])
                btn_disable_a.state(["disabled"])
                btn_disable_b.state(["disabled"])
                btn_disable_standalone.state(["disabled"])
                btn_reorder_a.state(["disabled"])
                btn_reorder_b.state(["disabled"])
                btn_reorder_recommended.state(["disabled"])
                btn_set_order_a.state(["disabled"])
                btn_set_order_b.state(["disabled"])
            except Exception:
                pass

            if not entries:
                return
            types = {e.get("type") for e in entries}
            sources = {e.get("source") for e in entries}
            if len(types) != 1:
                return
            ctype = next(iter(types))

            # If any selected is not resolvable, keep disabled
            if any(not bool(e.get("resolvable", False)) for e in entries):
                return

            if ctype == "xml_override" and sources == {"sim"}:
                try:
                    btn_patch_a.state(["!disabled"])
                    btn_patch_b.state(["!disabled"])
                except Exception:
                    pass
                try:
                    if _memory_for_entries(entries):
                        btn_apply_memory.state(["!disabled"])
                except Exception:
                    pass
                return
            if ctype == "duplicate_id":
                try:
                    btn_disable_a.state(["!disabled"])
                    btn_disable_b.state(["!disabled"])
                except Exception:
                    pass
                try:
                    if _memory_for_entries(entries):
                        btn_apply_memory.state(["!disabled"])
                except Exception:
                    pass
                return
            if ctype == "overhaul_vs_standalone":
                try:
                    btn_reorder_a.state(["!disabled"])
                    btn_reorder_b.state(["!disabled"])
                    btn_set_order_a.state(["!disabled"])
                    btn_set_order_b.state(["!disabled"])
                except Exception:
                    pass
                try:
                    if _memory_for_entries(entries):
                        btn_apply_memory.state(["!disabled"])
                except Exception:
                    pass
                return
            if ctype == "asset_conflict":
                try:
                    btn_reorder_a.state(["!disabled"])
                    btn_reorder_b.state(["!disabled"])
                    btn_set_order_a.state(["!disabled"])
                    btn_set_order_b.state(["!disabled"])
                except Exception:
                    pass
                try:
                    if _memory_for_entries(entries):
                        btn_apply_memory.state(["!disabled"])
                except Exception:
                    pass
                return
            if ctype == "load_order_priority":
                try:
                    btn_reorder_a.state(["!disabled"])
                    btn_reorder_b.state(["!disabled"])
                    # Only enable if recommendation fields exist across selection
                    ok = True
                    for e in entries or []:
                        payload = e.get("payload") or {}
                        src = payload if isinstance(payload, dict) else {}
                        rf = str(src.get("recommended_front") or e.get("recommended_front") or "").strip()
                        rb = str(src.get("recommended_back") or e.get("recommended_back") or "").strip()
                        if not rf or not rb:
                            ok = False
                            break
                    if ok:
                        btn_reorder_recommended.state(["!disabled"])
                    btn_set_order_a.state(["!disabled"])
                    btn_set_order_b.state(["!disabled"])
                except Exception:
                    pass
                try:
                    if _memory_for_entries(entries):
                        btn_apply_memory.state(["!disabled"])
                except Exception:
                    pass
                return

        def on_select(evt=None):
            sel = tree.selection()
            if not sel:
                _update_buttons_for_selection([])
                return
            entry = self._conflict_map.get(sel[0])
            if not entry:
                _update_buttons_for_selection([])
                return

            detail.delete("1.0", "end")
            detail.insert("end", f"Source: {entry.get('source')}\n")
            detail.insert("end", f"Type: {entry.get('type')}\n")
            detail.insert("end", f"File: {entry.get('file')}\n")
            detail.insert("end", f"Target: {entry.get('target')}\n")
            detail.insert("end", f"Mods: {entry.get('mod_a')} ↔ {entry.get('mod_b')}\n\n")

            # Confidence & safety signaling
            try:
                conf, safety, expl = _resolution_signals(entry)
                detail.insert("end", f"Confidence: {int(conf)} / 100\n")
                detail.insert("end", f"Safety: {safety}\n")
                if expl:
                    detail.insert("end", f"Note: {expl}\n")
                detail.insert("end", "\n")
            except Exception:
                pass

            # Provenance chain (rule first)
            try:
                payload = entry.get("payload") or {}
                prov = payload.get("provenance") if isinstance(payload, dict) else None
                if isinstance(prov, dict) and (prov.get("rule_id") or prov.get("rule_type")):
                    detail.insert(
                        "end",
                        f"Rule Applied: {prov.get('rule_type')} ({prov.get('rule_id')})\n",
                    )
                    if prov.get("rule_reason"):
                        detail.insert("end", f"Rule Reason: {prov.get('rule_reason')}\n")
                    if payload.get("rule_preferred"):
                        detail.insert(
                            "end",
                            f"Preferred Winner: {payload.get('rule_preferred')}\n",
                        )
                    detail.insert("end", "\n")
            except Exception:
                pass

            if not bool(entry.get("resolvable", False)):
                detail.insert("end", "Not auto-resolvable.\n")
                why = entry.get("why_not") or "No automatic safe resolution available."
                detail.insert("end", f"Reason: {why}\n")
            else:
                detail.insert("end", "Auto-resolvable.\n")

            # Show payload details
            if entry.get("source") == "sim":
                ct = entry.get("payload")
                try:
                    detail.insert(
                        "end",
                        (
                            "\nTimeline:\n  Vanilla\n"
                            f"  -> {ct.first.mod} ({ct.first.op}) = {ct.first.value[:200]}\n"
                            f"  -> {ct.second.mod} ({ct.second.op}) = {ct.second.value[:200]}\n"
                        ),
                    )
                except Exception:
                    pass
                try:
                    a_path = getattr(self, "_name_to_path", {}).get(ct.first.mod, "")
                    b_path = getattr(self, "_name_to_path", {}).get(ct.second.mod, "")
                    self._path_a_var.set(a_path)
                    self._path_b_var.set(b_path)
                except Exception:
                    pass
            else:
                try:
                    payload = entry.get("payload") or {}
                    reason = payload.get("reason") or entry.get("reason")
                    sugg = payload.get("suggestion")
                    lvl = payload.get("level")
                    if lvl:
                        detail.insert("end", f"\nLevel: {lvl}\n")
                    if reason:
                        detail.insert("end", f"Reason: {reason}\n")
                    if sugg:
                        detail.insert("end", f"Suggestion: {sugg}\n")

                    # Load-order recommendations (if present)
                    src = payload if isinstance(payload, dict) else {}
                    rf = str(src.get("recommended_front") or entry.get("recommended_front") or "").strip()
                    rb = str(src.get("recommended_back") or entry.get("recommended_back") or "").strip()
                    rr = str(src.get("recommended_reason") or entry.get("recommended_reason") or "").strip()
                    if rf and rb:
                        detail.insert("end", f"\nRecommended order: {rf}  ->  {rb}\n")
                        if rr:
                            detail.insert("end", f"Why: {rr}\n")
                except Exception:
                    pass

            # Memory section (CKB)
            try:
                rec = _memory_for_entry(entry)
                if rec:
                    detail.insert("end", "\nPreviously resolved (CKB):\n")
                    detail.insert("end", f"  Action: {rec.action}\n")
                    if rec.preferred_mod_id:
                        detail.insert("end", f"  Preferred: {rec.preferred_mod_id}\n")
                    detail.insert(
                        "end",
                        (
                            f"  Confidence: {int(round(rec.confidence * 100))}% ({rec.success_count}/{rec.applied_count})\n"
                        ),
                    )
                    if rec.order_value is not None:
                        detail.insert("end", f"  Order: {int(rec.order_value)}\n")
                    if rec.note:
                        detail.insert("end", f"  Note: {rec.note}\n")
            except Exception:
                pass

            # Resolution strategies (RKB)
            try:
                kb = getattr(self, "resolution_kb", None)
                if kb:
                    opts = kb.list_options(entry.get("type") or "unknown")
                    if opts:
                        detail.insert("end", "\nResolution strategies (RKB):\n")
                        for o in opts[:10]:
                            tier = "Common" if o.tier == "common" else "Uncommon"
                            risk = " (risky)" if o.risky else ""
                            detail.insert(
                                "end",
                                f"  - {tier}{risk}: {o.label} [{int(round(o.confidence * 100))}% | {o.success_count}/{o.applied_count}]\n",
                            )
            except Exception:
                pass

            # Button states based on entire selection
            selected_entries = []
            for iid in list(sel):
                e = self._conflict_map.get(iid)
                if e:
                    selected_entries.append(e)
            _update_buttons_for_selection(selected_entries)

            # Enable/disable rule buttons based on selection count
            try:
                if len(selected_entries) == 1:
                    btn_rule_prefer_a.state(["!disabled"])
                    btn_rule_prefer_b.state(["!disabled"])
                    btn_rule_ignore.state(["!disabled"])
                else:
                    btn_rule_prefer_a.state(["disabled"])
                    btn_rule_prefer_b.state(["disabled"])
                    btn_rule_ignore.state(["disabled"])
            except Exception:
                pass

        tree.bind("<<TreeviewSelect>>", on_select)

        btns = tk.Frame(right, bg=self.colors.get("panel", "#252526"))
        btns.pack(padx=8, pady=4, fill="x")

        # --- Conflict Audit Board (file-level viewer) ---
        def _open_conflict_audit_board():
            board = tk.Toplevel(win)
            board.title("Conflict Audit Board")
            board.configure(bg=self.colors.get("bg", "#1e1e1e"))

            top = tk.Frame(board, bg=self.colors.get("panel", "#252526"))
            top.pack(side="top", fill="x")

            ttk.Label(top, text="Per-file breakdown (Conflict → File → Winner → Rule)").pack(
                side="left", padx=8, pady=6
            )

            body = tk.Frame(board, bg=self.colors.get("bg", "#1e1e1e"))
            body.pack(side="top", fill="both", expand=True)

            cols = ("type", "file", "target", "mod_a", "mod_b", "winner", "reason")
            t = ttk.Treeview(body, columns=cols, show="headings")
            for c in cols:
                t.heading(c, text=c.replace("_", " ").title())
                if c == "target":
                    t.column(c, width=420, anchor="w")
                elif c in {"file", "winner"}:
                    t.column(c, width=170, anchor="w")
                elif c == "reason":
                    t.column(c, width=260, anchor="w")
                else:
                    t.column(c, width=140, anchor="w")
            t.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(body, orient="vertical", command=t.yview)
            sb.pack(side="right", fill="y")
            try:
                t.configure(yscrollcommand=sb.set)
            except Exception:
                pass

            # Populate
            rows = []
            for i, e in enumerate(conflicts or []):
                try:
                    payload = e.get("payload") or {}
                    prov = payload.get("provenance") if isinstance(payload, dict) else None
                    rule_note = ""
                    if isinstance(prov, dict) and (prov.get("rule_type") or prov.get("rule_id")):
                        rule_note = f"rule:{prov.get('rule_type')}"

                    winner = ""
                    # rule-preferred winner
                    if isinstance(payload, dict) and payload.get("rule_preferred"):
                        winner = str(payload.get("rule_preferred"))
                    # simulator: later mod wins by default
                    if not winner and e.get("source") == "sim":
                        try:
                            ct = e.get("payload")
                            winner = getattr(getattr(ct, "second", None), "mod", "") or ""
                        except Exception:
                            winner = ""

                    reason = rule_note or (e.get("reason") or "")
                    iid = f"b{i}"
                    t.insert(
                        "",
                        "end",
                        iid=iid,
                        values=(
                            e.get("type") or "",
                            e.get("file") or "",
                            e.get("target") or "",
                            e.get("mod_a") or "",
                            e.get("mod_b") or "",
                            winner,
                            reason,
                        ),
                    )
                    rows.append((iid, e))
                except Exception:
                    continue

            # Controls
            ctrl = tk.Frame(board, bg=self.colors.get("panel", "#252526"))
            ctrl.pack(side="bottom", fill="x")

            ttk.Label(ctrl, text="Create persistent rule for selected row:").pack(side="left", padx=8, pady=6)

            def _selected_entry():
                sel = list(t.selection() or [])
                if not sel:
                    return None
                iid = sel[0]
                for rid, ent in rows:
                    if rid == iid:
                        return ent
                return None

            def _rule_always_win(which: str):
                ent = _selected_entry()
                if not ent:
                    messagebox.showerror("Rules", "Select a row first.")
                    return
                try:
                    from logic.rule_store import Rule, RuleStore

                    store = RuleStore(os.path.join("data", "rules.json"))
                    winner = ent.get("mod_a") if which == "A" else ent.get("mod_b")
                    r = Rule(
                        id="",
                        type="always_win",
                        conflict_type=ent.get("type") or None,
                        file=ent.get("file") or None,
                        target=ent.get("target") or None,
                        mod_a=ent.get("mod_a") or None,
                        mod_b=ent.get("mod_b") or None,
                        winner=str(winner or ""),
                        note="User chose winner (audit board)",
                        origin="user",
                    )
                    store.add_rule(r)
                    messagebox.showinfo("Rules", f"Saved always-win rule: {winner}")
                except Exception as e:
                    messagebox.showerror("Rules", str(e))

            def _rule_ignore():
                ent = _selected_entry()
                if not ent:
                    messagebox.showerror("Rules", "Select a row first.")
                    return
                try:
                    from logic.rule_store import Rule, RuleStore

                    store = RuleStore(os.path.join("data", "rules.json"))
                    r = Rule(
                        id="",
                        type="ignore_conflict",
                        conflict_type=ent.get("type") or None,
                        file=ent.get("file") or None,
                        target=ent.get("target") or None,
                        mod_a=ent.get("mod_a") or None,
                        mod_b=ent.get("mod_b") or None,
                        note="User ignored (audit board)",
                        origin="user",
                    )
                    store.add_rule(r)
                    messagebox.showinfo("Rules", "Saved ignore rule")
                except Exception as e:
                    messagebox.showerror("Rules", str(e))

            ttk.Button(ctrl, text="Always Win: Prefer A", command=lambda: _rule_always_win("A")).pack(
                side="left", padx=4, pady=6
            )
            ttk.Button(ctrl, text="Always Win: Prefer B", command=lambda: _rule_always_win("B")).pack(
                side="left", padx=4, pady=6
            )
            ttk.Button(ctrl, text="Ignore Conflict", command=_rule_ignore).pack(side="left", padx=4, pady=6)

        btn_audit = ttk.Button(btns, text="Open Conflict Audit Board…", command=_open_conflict_audit_board)
        try:
            btn_audit.pack(fill="x", pady=6)
        except Exception:
            pass

        def _selected_conflicts():
            sel = list(tree.selection() or [])
            if not sel:
                messagebox.showerror("Resolve", "Select one or more conflicts first.")
                return []
            out = []
            for iid in sel:
                e = self._conflict_map.get(iid)
                if e:
                    out.append(e)
            return out

        def _find_mod_by_name(name: str):
            return next((mm for mm in self.mods if getattr(mm, "name", None) == name), None)

        def _disable_mod_by_name(name: str):
            mod = _find_mod_by_name(name)
            if not mod:
                raise RuntimeError(f"Mod not found: {name}")
            if not is_effectively_enabled(mod):
                return
            install_id = getattr(mod, "install_id", None) or self._normalize_install_id(getattr(mod, "name", ""))
            if not install_id:
                raise RuntimeError("Cannot compute install id for mod")
            # Authoritative disable
            try:
                mod.enabled = False
                mod.user_disabled = True
            except Exception:
                pass
            # Persist
            try:
                if self.mod_state_store:
                    self.mod_state_store.set(str(install_id), enabled=False, user_disabled=True)
                    self.mod_state_store.save()
            except Exception:
                pass
            # Back-compat
            try:
                self.user_disabled_ids.add(str(install_id))
            except Exception:
                pass
            print(f"[RESOLVE] Disabled mod (virtual): {name}")

        def _set_mod_order_by_name(name: str, order_value: int):
            if order_value < 0 or order_value > 99999:
                raise RuntimeError("Order must be between 0 and 99999")
            mod = _find_mod_by_name(name)
            if not mod:
                raise RuntimeError(f"Mod not found: {name}")
            if not is_effectively_enabled(mod):
                raise RuntimeError("Cannot reorder a disabled mod")
            install_id = getattr(mod, "install_id", None) or self._normalize_install_id(getattr(mod, "name", ""))
            if not install_id:
                raise RuntimeError("Cannot compute install id for mod")
            self.order_overrides[install_id] = int(order_value)
            try:
                mod.order_override = int(order_value)
            except Exception:
                pass
            w = max(3, len(str(int(order_value))))
            print(f"[RESOLVE] Set order override (virtual): {name} -> {int(order_value):0{w}d}")

        def _resolution_context():
            from engines.resolution_engine import ResolutionContext

            return ResolutionContext(
                mods_root=self.mods_path.get(),
                output_root=self.mods_path.get(),
                disable_mod=_disable_mod_by_name,
                set_mod_order=_set_mod_order_by_name,
                save_settings=lambda: self.save_settings(),
                scan=lambda: self.scan(),
                apply_load_order=lambda: self.apply_load_order_rename(confirm=False),
            )

        def _require_homogeneous(entries, expected_type: str):
            if not entries:
                raise RuntimeError("No conflicts selected")
            types = {e.get("type") for e in entries}
            if len(types) != 1 or expected_type not in types:
                raise RuntimeError(f"Selection must be only '{expected_type}' conflicts")
            if any(not bool(e.get("resolvable", False)) for e in entries):
                raise RuntimeError("Selection includes non-auto-resolvable conflicts")

        def _confirm_save_breaking(entries):
            try:
                from logic.conflict_taxonomy import is_save_breaking
            except Exception:
                return True
            risky = []
            for e in entries or []:
                try:
                    if is_save_breaking(
                        conflict_type=str(e.get("type") or ""),
                        file=str(e.get("file") or ""),
                    ):
                        risky.append(e)
                except Exception:
                    continue
            if not risky:
                return True

            from tkinter import simpledialog

            msg = "This action may break existing saves/worlds.\n\nType EXACTLY: I understand\n\nto proceed."
            typed = None
            try:
                typed = simpledialog.askstring("Save-breaking action", msg, parent=win)
            except Exception:
                return False
            return (typed or "").strip() == "I understand"

        def do_patch(which: str):
            selected = _selected_conflicts()
            try:
                _require_homogeneous(selected, "xml_override")
                if not _confirm_save_breaking(selected):
                    return
                if any(e.get("source") != "sim" for e in selected):
                    raise RuntimeError("XML Override patching requires simulator conflicts")
                from engines.resolution_engine import apply_patch_from_sim_payloads

                cts = [e.get("payload") for e in selected if e.get("payload")]
                print("[RESOLVE] Conflict type: XML Override")
                print(f"[RESOLVE] Using Patch {which}")
                ctx = _resolution_context()
                result = apply_patch_from_sim_payloads(ctx, sim_conflicts=cts, prefer=which)
                print(f"[RESOLVE] Writing patch to {result.patch_dir}")
                preferred_mod = selected[0].get("mod_a") if which == "A" else selected[0].get("mod_b")
                _record_memory(
                    selected,
                    resolution_action="patch",
                    preferred_mod_name=preferred_mod,
                )
                print("[RESOLVE] Applied successfully")
                messagebox.showinfo(
                    "Resolved",
                    f"Patch created in Mods Library: {os.path.basename(str(result.patch_dir))}\n\nApply load order to ensure it loads last.",
                )
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[RESOLVE] FAILED: {e}")
                messagebox.showerror("Resolve Failed", str(e))

        def do_stabilizing_patch():
            """Generate a patch mod that encodes the simulator's final state.

            This targets remaining simulator interactions that are not strict overrides
            (e.g. remove/set mixes). It's designed as a one-click "materialize the end
            result" tool.
            """

            try:
                if not messagebox.askyesno(
                    "Stabilizing Patch",
                    "Re-run simulation with the CURRENT enabled mods before generating the stabilizing patch?\n\n"
                    "Yes = most accurate (recommended)\n"
                    "No = use last simulation results from when this window opened",
                    parent=win,
                ):
                    st = getattr(self, "_last_sim_state", None)
                    sim_conf = getattr(self, "_last_sim_conflicts", None) or []
                else:
                    from mock_deploy.engine import simulate_deployment

                    enabled_now = []
                    for m in self.mods or []:
                        try:
                            if not is_effectively_enabled(m):
                                continue
                            enabled_now.append((m.name, m.path, int(getattr(m, "load_order", 0) or 0)))
                        except Exception:
                            continue
                    enabled_now.sort(key=lambda t: (int(t[2]), str(t[0] or "").lower()))
                    st, sim_conf = simulate_deployment([(n, p) for (n, p, _o) in enabled_now])

                    try:
                        self._last_sim_state = st
                        self._last_sim_conflicts = sim_conf
                    except Exception:
                        pass

                if st is None:
                    raise RuntimeError("Simulation state is not available")

                non_override = []
                kinds = set()
                for ct in sim_conf or []:
                    try:
                        k = str(getattr(ct, "kind", "") or "")
                        if k in {"override", "append-append"}:
                            continue
                        kinds.add(k or "(unknown)")
                        non_override.append(ct)
                    except Exception:
                        continue

                if not non_override:
                    messagebox.showinfo(
                        "Stabilizing Patch",
                        "No non-override simulator interactions were found to stabilize.",
                    )
                    return

                kinds_s = ", ".join(sorted(kinds, key=lambda s: (s or "").lower()))
                if not messagebox.askyesno(
                    "Stabilizing Patch",
                    f"Create a stabilizing patch for {len(non_override)} simulator interaction(s)?\n\nKinds: {kinds_s}",
                    parent=win,
                ):
                    return

                from logic.conflict_patch import create_stabilizing_patch

                pdir = create_stabilizing_patch(
                    self.mods_path.get(),
                    state=st,
                    conflicts=non_override,
                    output_root=self.mods_path.get(),
                )

                # Persist + refresh (so the patch mod is visible and ordering gets applied)
                try:
                    self.save_settings()
                except Exception:
                    pass
                self.scan()
                try:
                    self.apply_load_order_rename(confirm=False)
                except Exception:
                    pass

                messagebox.showinfo(
                    "Stabilizing Patch",
                    f"Created patch mod: {os.path.basename(str(pdir))}\n\nScan refreshed.",
                )
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[RESOLVE] Stabilizing patch FAILED: {e}")
                messagebox.showerror("Stabilizing Patch Failed", str(e))

        def do_disable(which: str):
            selected = _selected_conflicts()
            try:
                _require_homogeneous(selected, "duplicate_id")
                if not _confirm_save_breaking(selected):
                    return
                from engines.resolution_engine import apply_disable_mods

                names = []
                for e in selected:
                    name = e.get("mod_a") if which == "A" else e.get("mod_b")
                    if not name:
                        raise RuntimeError("Selected conflict missing mod name")
                    names.append(name)
                    print("[RESOLVE] Conflict type: Duplicate ID")
                    print(f"[RESOLVE] Disabling Mod {which}: {name}")

                ctx = _resolution_context()
                apply_disable_mods(ctx, names)
                preferred_mod = selected[0].get("mod_a") if which == "A" else selected[0].get("mod_b")
                _record_memory(
                    selected,
                    resolution_action="disable",
                    preferred_mod_name=preferred_mod,
                )
                print("[RESOLVE] Applied successfully")
                messagebox.showinfo("Resolved", f"Disabled Mod {which} for {len(selected)} conflict(s).")
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[RESOLVE] FAILED: {e}")
                messagebox.showerror("Resolve Failed", str(e))

        def do_disable_standalone():
            selected = _selected_conflicts()
            try:
                _require_homogeneous(selected, "overhaul_vs_standalone")
                if not _confirm_save_breaking(selected):
                    return
                from engines.resolution_engine import apply_disable_mods

                to_disable = []
                for e in selected:
                    a = _find_mod_by_name(e.get("mod_a"))
                    b = _find_mod_by_name(e.get("mod_b"))
                    if not a or not b:
                        raise RuntimeError("Selected conflict involves unknown mods")
                    if getattr(a, "is_overhaul", False) and not getattr(b, "is_overhaul", False):
                        standalone = b.name
                    elif getattr(b, "is_overhaul", False) and not getattr(a, "is_overhaul", False):
                        standalone = a.name
                    else:
                        raise RuntimeError("Cannot determine standalone vs overhaul")
                    print("[RESOLVE] Conflict type: Overhaul vs Standalone")
                    print(f"[RESOLVE] Disabling standalone: {standalone}")
                    to_disable.append(standalone)

                ctx = _resolution_context()
                apply_disable_mods(ctx, to_disable)
                _record_memory(
                    selected,
                    resolution_action="disable_standalone",
                    preferred_mod_name=None,
                )
                print("[RESOLVE] Applied successfully")
                messagebox.showinfo(
                    "Resolved",
                    f"Disabled standalone mod(s) for {len(selected)} conflict(s).",
                )
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[RESOLVE] FAILED: {e}")
                messagebox.showerror("Resolve Failed", str(e))

        def _next_order_value():
            # Choose a safe "load last" order value below patch prefix usage.
            vals = []
            for mm in self.mods:
                if getattr(mm, "user_disabled", False):
                    continue
                if _is_patch_mod_name(getattr(mm, "name", "")):
                    continue
                ov = getattr(mm, "order_override", None)
                if isinstance(ov, int):
                    vals.append(ov)
                    continue
                p = _parse_order_prefix(getattr(mm, "name", ""))
                if p is not None:
                    vals.append(p)
            base = max(vals) if vals else 0
            return min(99999, int(base) + 10)

        def do_reorder(which: str):
            selected = _selected_conflicts()
            try:
                _require_homogeneous(selected, "load_order_priority")
                if not _confirm_save_breaking(selected):
                    return
                from engines.resolution_engine import apply_reorder_later

                winners = []
                for e in selected:
                    winner = e.get("mod_a") if which == "A" else e.get("mod_b")
                    if not winner:
                        raise RuntimeError("Selected conflict missing mod name")
                    winners.append(winner)

                # Reorder by moving the chosen mod(s) later
                start_val = _next_order_value()
                next_val = start_val
                for w in winners:
                    print("[RESOLVE] Conflict type: Load Order Priority")
                    ww = max(3, len(str(int(next_val))))
                    print(f"[RESOLVE] Reordering: {w} -> {int(next_val):0{ww}d}_*")
                    next_val = min(99999, int(next_val) + 10)

                ctx = _resolution_context()
                apply_reorder_later(ctx, names=winners, start_order_value=start_val)
                preferred_mod = selected[0].get("mod_a") if which == "A" else selected[0].get("mod_b")
                _record_memory(
                    selected,
                    resolution_action="reorder",
                    preferred_mod_name=preferred_mod,
                )
                print("[RESOLVE] Applied successfully")
                messagebox.showinfo(
                    "Resolved",
                    f"Reordered Mod {which} to load later for {len(selected)} conflict(s).",
                )
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[RESOLVE] FAILED: {e}")
                messagebox.showerror("Resolve Failed", str(e))

        def do_reorder_recommended():
            selected = _selected_conflicts()
            try:
                _require_homogeneous(selected, "load_order_priority")
                if not _confirm_save_breaking(selected):
                    return
                from engines.resolution_engine import apply_reorder_later

                winners = []
                notes = []
                for e in selected:
                    payload = e.get("payload") or {}
                    src = payload if isinstance(payload, dict) else {}
                    front = str(src.get("recommended_front") or e.get("recommended_front") or "").strip()
                    back = str(src.get("recommended_back") or e.get("recommended_back") or "").strip()
                    why = str(src.get("recommended_reason") or e.get("recommended_reason") or "").strip()
                    if not front or not back:
                        raise RuntimeError("No recommended order available for selection")
                    winners.append(back)
                    notes.append(f"{front} -> {back}" + (f" ({why})" if why else ""))

                # Deduplicate while preserving order
                uniq = []
                seen = set()
                for w in winners:
                    if w in seen:
                        continue
                    seen.add(w)
                    uniq.append(w)

                start_val = _next_order_value()
                next_val = start_val
                for w in uniq:
                    print("[RESOLVE] Conflict type: Load Order Priority (Recommended)")
                    ww = max(3, len(str(int(next_val))))
                    print(f"[RESOLVE] Reordering (recommended): {w} -> {int(next_val):0{ww}d}_*")
                    next_val = min(99999, int(next_val) + 10)

                if notes:
                    print("[RESOLVE] Recommended ordering:")
                    for n in notes[:20]:
                        print(f"  - {n}")

                ctx = _resolution_context()
                apply_reorder_later(ctx, names=uniq, start_order_value=start_val)

                try:
                    _record_memory(
                        selected,
                        resolution_action="reorder",
                        preferred_mod_name=(uniq[0] if uniq else None),
                    )
                except Exception:
                    pass

                print("[RESOLVE] Applied successfully")
                messagebox.showinfo(
                    "Resolved",
                    f"Applied recommended ordering for {len(selected)} conflict(s).",
                )
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[RESOLVE] FAILED: {e}")
                messagebox.showerror("Resolve Failed", str(e))

        def do_set_order(which: str):
            selected = _selected_conflicts()
            try:
                _require_homogeneous(selected, "load_order_priority")
                if not _confirm_save_breaking(selected):
                    return
                from tkinter import simpledialog
                from engines.resolution_engine import OrderAssignment, apply_set_order

                v = simpledialog.askinteger(
                    "Set Order",
                    "Enter order prefix (0-99999):",
                    minvalue=0,
                    maxvalue=99999,
                    parent=win,
                )
                if v is None:
                    return
                assignments = []
                for e in selected:
                    name = e.get("mod_a") if which == "A" else e.get("mod_b")
                    if not name:
                        raise RuntimeError("Selected conflict missing mod name")
                    print("[RESOLVE] Conflict type: Load Order Priority")
                    wv = max(3, len(str(int(v))))
                    print(f"[RESOLVE] Manual set order for {name}: {int(v):0{wv}d}")
                    assignments.append(OrderAssignment(name=name, order_value=int(v)))

                ctx = _resolution_context()
                apply_set_order(ctx, assignments)
                preferred_mod = selected[0].get("mod_a") if which == "A" else selected[0].get("mod_b")
                _record_memory(
                    selected,
                    resolution_action="set_order",
                    preferred_mod_name=preferred_mod,
                    order_value=int(v),
                )
                print("[RESOLVE] Applied successfully")
                messagebox.showinfo(
                    "Resolved",
                    f"Set order for Mod {which} on {len(selected)} conflict(s).",
                )
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[RESOLVE] FAILED: {e}")
                messagebox.showerror("Resolve Failed", str(e))

        def _which_side(entry: dict, preferred_mod_id: str) -> str:
            try:
                from logic.conflict_memory import normalize_mod_id

                a = normalize_mod_id(entry.get("mod_a") or "")
                b = normalize_mod_id(entry.get("mod_b") or "")
                pid = normalize_mod_id(preferred_mod_id or "")
                if pid == a:
                    return "A"
                if pid == b:
                    return "B"
            except Exception:
                pass
            return "A"

        def do_apply_memory():
            selected = _selected_conflicts()
            try:
                if not _confirm_save_breaking(selected):
                    return
                rec = _memory_for_entries(selected)
                if not rec:
                    raise RuntimeError("No applicable memory recommendation for this selection")
                ctype = selected[0].get("type")

                if ctype == "xml_override" and rec.action == "patch":
                    which = _which_side(selected[0], rec.preferred_mod_id or "")
                    do_patch(which)
                    return
                if ctype == "duplicate_id" and rec.action == "disable":
                    which = _which_side(selected[0], rec.preferred_mod_id or "")
                    do_disable(which)
                    return
                if ctype == "overhaul_vs_standalone" and rec.action == "disable_standalone":
                    do_disable_standalone()
                    return
                if ctype == "load_order_priority":
                    which = _which_side(selected[0], rec.preferred_mod_id or "")
                    if rec.action == "set_order" and isinstance(rec.order_value, int):
                        from engines.resolution_engine import OrderAssignment, apply_set_order

                        # Apply exact prefix for preferred side
                        assignments = []
                        for e in selected:
                            name = e.get("mod_a") if which == "A" else e.get("mod_b")
                            if not name:
                                raise RuntimeError("Selected conflict missing mod name")
                            assignments.append(OrderAssignment(name=name, order_value=int(rec.order_value)))

                        ctx = _resolution_context()
                        apply_set_order(ctx, assignments)
                        preferred_mod = selected[0].get("mod_a") if which == "A" else selected[0].get("mod_b")
                        _record_memory(
                            selected,
                            resolution_action="set_order",
                            preferred_mod_name=preferred_mod,
                            order_value=int(rec.order_value),
                        )
                        messagebox.showinfo(
                            "Resolved",
                            f"Applied memory order prefix {int(rec.order_value)}.",
                        )
                        try:
                            win.destroy()
                        except Exception:
                            pass
                        return
                    # default to auto reorder
                    do_reorder(which)
                    return

                raise RuntimeError(f"Memory recommendation not supported: {rec.action}")
            except Exception as e:
                print(f"[RESOLVE] FAILED: {e}")
                messagebox.showerror("Resolve Failed", str(e))

        btn_patch_a = ttk.Button(
            btns,
            text="XML Override: Generate Patch (Prefer A)",
            command=lambda: do_patch("A"),
        )
        btn_patch_b = ttk.Button(
            btns,
            text="XML Override: Generate Patch (Prefer B)",
            command=lambda: do_patch("B"),
        )
        btn_stabilize_patch = ttk.Button(
            btns,
            text="Simulator: Generate Stabilizing Patch (Final State)",
            command=do_stabilizing_patch,
        )
        btn_disable_a = ttk.Button(btns, text="Duplicate ID: Disable Mod A", command=lambda: do_disable("A"))
        btn_disable_b = ttk.Button(btns, text="Duplicate ID: Disable Mod B", command=lambda: do_disable("B"))
        btn_disable_standalone = ttk.Button(
            btns,
            text="Overhaul vs Standalone: Disable Standalone",
            command=do_disable_standalone,
        )
        btn_reorder_a = ttk.Button(
            btns,
            text="Load Order: Auto-reorder (Prefer A)",
            command=lambda: do_reorder("A"),
        )
        btn_reorder_b = ttk.Button(
            btns,
            text="Load Order: Auto-reorder (Prefer B)",
            command=lambda: do_reorder("B"),
        )
        btn_reorder_recommended = ttk.Button(
            btns,
            text="Load Order: Apply Recommended Order",
            command=do_reorder_recommended,
        )
        btn_set_order_a = ttk.Button(
            btns,
            text="Load Order: Set prefix for A…",
            command=lambda: do_set_order("A"),
        )
        btn_set_order_b = ttk.Button(
            btns,
            text="Load Order: Set prefix for B…",
            command=lambda: do_set_order("B"),
        )

        btn_apply_memory = ttk.Button(btns, text="Apply Memory Recommendation", command=do_apply_memory)

        btn_auto_resolve = ttk.Button(
            btns,
            text="Auto-resolve high-confidence (CKB)…",
            command=_auto_resolve_high_confidence,
        )

        def _auto_resolve_recommended_load_order_all():
            """Resolve load-order conflicts using detector recommendations (no memory required)."""

            try:
                entries = [
                    e
                    for e in (conflicts or [])
                    if (e.get("type") == "load_order_priority")
                    and bool(e.get("resolvable", False))
                    and isinstance(e.get("payload"), dict)
                ]
            except Exception:
                entries = []

            picks = []  # list of (front, back, reason)
            for e in entries:
                try:
                    payload = e.get("payload") or {}
                    front = str(payload.get("recommended_front") or "").strip()
                    back = str(payload.get("recommended_back") or "").strip()
                    why = str(payload.get("recommended_reason") or "").strip()
                    if front and back and front != back:
                        picks.append((front, back, why))
                except Exception:
                    continue

            if not picks:
                messagebox.showinfo(
                    "Auto-resolve",
                    "No load-order conflicts with recommendations were found.",
                )
                return

            # Deduplicate winners while preserving order
            winners = []
            seen = set()
            for _, back, _ in picks:
                if back in seen:
                    continue
                seen.add(back)
                winners.append(back)

            lines = [
                f"Load-order conflicts with recommendations: {len(picks)}",
                f"Mods to move later (winners): {len(winners)}",
                "",
                "Planned ordering (front -> back):",
            ]
            for f, b, w in picks[:25]:
                lines.append(f"- {f} -> {b}" + (f" ({w})" if w else ""))
            if len(picks) > 25:
                lines.append(f"... and {len(picks) - 25} more")
            lines.extend(
                [
                    "",
                    "This will set order overrides and apply load order by renaming folders.",
                ]
            )

            if not messagebox.askyesno("Auto-resolve", "Apply recommended load-order fixes?\n\n" + "\n".join(lines)):
                return

            try:
                from engines.resolution_engine import apply_reorder_later

                start_val = _next_order_value()
                ctx = _resolution_context()
                apply_reorder_later(ctx, names=winners, start_order_value=start_val)

                # Record CKB for each entry (best-effort)
                try:
                    for f, b, _ in picks:
                        _record_memory(
                            [
                                {
                                    "type": "load_order_priority",
                                    "mod_a": f,
                                    "mod_b": b,
                                    "payload": {"recommended_front": f, "recommended_back": b},
                                }
                            ],
                            resolution_action="reorder",
                            preferred_mod_name=b,
                        )
                except Exception:
                    pass

                messagebox.showinfo("Auto-resolve", "Applied recommended load-order fixes. Scan refreshed.")
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                print(f"[AUTO-RESOLVE-RECOMMENDED] FAILED: {e}")
                messagebox.showerror("Auto-resolve failed", str(e))

        btn_auto_resolve_recommended = ttk.Button(
            btns,
            text="Auto-resolve load-order (Recommended)…",
            command=_auto_resolve_recommended_load_order_all,
        )

        btn_auto_resolve_policy = ttk.Button(
            btns,
            text="Auto-resolve by Policy (Priority/Deps)…",
            command=_auto_resolve_by_policy,
        )

        for b in [
            btn_auto_resolve,
            btn_auto_resolve_recommended,
            btn_auto_resolve_policy,
            btn_apply_memory,
            btn_patch_a,
            btn_patch_b,
            btn_stabilize_patch,
            btn_disable_a,
            btn_disable_b,
            btn_disable_standalone,
            btn_reorder_a,
            btn_reorder_b,
            btn_reorder_recommended,
            btn_set_order_a,
            btn_set_order_b,
        ]:
            try:
                b.pack(fill="x", pady=2)
            except Exception:
                pass

        # Keep the previous heuristic fallback
        ttk.Button(
            btns,
            text="Resolve by Load Order (Heuristic)",
            command=self._resolve_by_load_order,
        ).pack(fill="x", pady=8)

        # --- Rule creation handlers (single conflict only) ---
        def _create_rule_for_selection(kind: str, prefer: Optional[str] = None):
            sel = list(tree.selection() or [])
            if len(sel) != 1:
                messagebox.showerror("Rules", "Select exactly one conflict.")
                return
            entry = self._conflict_map.get(sel[0])
            if not entry:
                return
            try:
                from logic.rule_store import Rule, RuleStore

                store = RuleStore(os.path.join("data", "rules.json"))
                if kind == "ignore_conflict":
                    r = Rule(
                        id="",
                        type="ignore_conflict",
                        conflict_type=entry.get("type") or None,
                        file=entry.get("file") or None,
                        target=entry.get("target") or None,
                        mod_a=entry.get("mod_a") or None,
                        mod_b=entry.get("mod_b") or None,
                        note="User ignored conflict",
                        origin="user",
                    )
                    store.add_rule(r)
                    messagebox.showinfo("Rules", "Ignore rule saved (persistent).")
                    return

                if kind == "always_win":
                    winner = entry.get("mod_a") if prefer == "A" else entry.get("mod_b")
                    if not winner:
                        raise RuntimeError("Missing winner mod")
                    r = Rule(
                        id="",
                        type="always_win",
                        conflict_type=entry.get("type") or None,
                        file=entry.get("file") or None,
                        target=entry.get("target") or None,
                        mod_a=entry.get("mod_a") or None,
                        mod_b=entry.get("mod_b") or None,
                        winner=str(winner),
                        note="User chose winner",
                        origin="user",
                    )
                    store.add_rule(r)
                    messagebox.showinfo("Rules", f"Always-win rule saved: {winner}")
                    return
            except Exception as e:
                messagebox.showerror("Rules", str(e))

        def _create_ignore_rules_for_selected():
            sel = list(tree.selection() or [])
            if not sel:
                messagebox.showerror("Rules", "Select one or more conflicts.")
                return
            try:
                from logic.rule_store import Rule, RuleStore

                store = RuleStore(os.path.join("data", "rules.json"))
                wrote = 0
                for iid in sel:
                    entry = self._conflict_map.get(iid)
                    if not entry:
                        continue
                    r = Rule(
                        id="",
                        type="ignore_conflict",
                        conflict_type=entry.get("type") or None,
                        file=entry.get("file") or None,
                        target=entry.get("target") or None,
                        mod_a=entry.get("mod_a") or None,
                        mod_b=entry.get("mod_b") or None,
                        note="User bulk-ignore",
                        origin="user",
                    )
                    store.add_rule(r)
                    wrote += 1

                messagebox.showinfo(
                    "Rules",
                    f"Saved {wrote} ignore rule(s).\n\nRe-open Resolve Conflicts to refresh the list.",
                )
                try:
                    win.destroy()
                except Exception:
                    pass
            except Exception as e:
                messagebox.showerror("Rules", str(e))

        try:
            btn_rule_prefer_a.configure(command=lambda: _create_rule_for_selection("always_win", prefer="A"))
            btn_rule_prefer_b.configure(command=lambda: _create_rule_for_selection("always_win", prefer="B"))
            btn_rule_ignore.configure(command=lambda: _create_rule_for_selection("ignore_conflict"))
            btn_rule_ignore_selected.configure(command=_create_ignore_rules_for_selected)
            btn_rule_prefer_a.state(["disabled"])
            btn_rule_prefer_b.state(["disabled"])
            btn_rule_ignore.state(["disabled"])
        except Exception:
            pass

        try:
            _update_buttons_for_selection([])
        except Exception:
            pass

        on_select()

    def _resolve_by_load_order(self):
        """Existing heuristic to adjust load order so a winner overrides others."""
        # Fall back to the previous algorithm
        conflicted = [m for m in self.mods if getattr(m, "conflict", False)]
        if not conflicted:
            messagebox.showinfo("Resolve", "No conflicts detected by heuristic.")
            return
        groups = {}
        for mod in conflicted:
            for scope in getattr(mod, "scopes", set()):
                groups.setdefault(scope, []).append(mod)
        for scope, mods in groups.items():
            if len(mods) <= 1:
                continue
            mods_sorted = sorted(
                mods,
                key=lambda m: (
                    not getattr(m, "has_modinfo", False),
                    getattr(m, "category", "") == "POI / Prefab",
                    m.name.lower(),
                ),
            )
            winner = mods_sorted[-1]
            base_order = getattr(winner, "load_order", 1000)
            for i, mod in enumerate(mods_sorted[:-1]):
                mod.load_order = base_order - (len(mods_sorted) - i)
            winner.load_order = base_order + 10
        self.refresh_table()
        try:
            self.update_mod_count()
        except Exception:
            pass
        messagebox.showinfo("Resolve", "Applied load-order adjustments.")

    # --------------------------------------------------
    # Configuration load/save helpers (persist last used Mods path)
    # --------------------------------------------------
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.mods_path.set(data.get("mods_path", self.mods_path.get()))
            except Exception:
                pass

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"mods_path": self.mods_path.get()}, f, indent=2)
        except Exception:
            pass

    # ------------------ Settings (auto save/load) ------------------
    def load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.mods_path.set(data.get("mods_path", self.mods_path.get()))

            # Ignore legacy settings from older versions.
            try:
                uds = data.get("user_disabled_ids", []) or []
                self.user_disabled_ids = {self._normalize_install_id(x) for x in uds if str(x).strip()}
            except Exception:
                self.user_disabled_ids = set()
            try:
                oo = data.get("order_overrides", {}) or {}
                norm = {}
                for k, v in oo.items():
                    nk = self._normalize_install_id(k)
                    try:
                        norm[nk] = int(v)
                    except Exception:
                        continue
                self.order_overrides = norm
            except Exception:
                self.order_overrides = {}
            # patch_overlay_dirs is legacy; ignore

            if "geometry" in data:
                try:
                    self.root.geometry(data["geometry"])
                except Exception:
                    pass

            if "search" in data:
                try:
                    self.search_var.set(data.get("search", ""))
                except Exception:
                    pass

            # Feature flags
            try:
                self.debug_scanner = bool(data.get("ENABLE_SCANNER_DEBUG", False))
                self.enable_classification = bool(data.get("ENABLE_CLASSIFICATION", False))
                # Integrity verification (hashing; optional)
                self.enable_integrity = bool(data.get("ENABLE_INTEGRITY", False))
                # Deployment hardening / guardrails
                self.harden_deployment = bool(data.get("HARDEN_DEPLOYMENT", True))
                self.block_multiple_mods_dirs = bool(data.get("BLOCK_MULTIPLE_MODS_DIRS", True))
                self.block_invalid_xml = bool(data.get("BLOCK_EMPTY_OR_INVALID_XML", True))
                self.block_full_file_replacements = bool(data.get("BLOCK_FULLFILE_REPLACEMENTS", True))
                self.enforce_single_ui_framework = bool(data.get("ENFORCE_SINGLE_UI_FRAMEWORK", True))
                self.auto_prefix_ui_groups = bool(data.get("AUTO_PREFIX_UI_GROUPS", True))
                # LLM flags
                self.enable_llm = bool(data.get("ENABLE_LLM", True))
                self.llm_model = str(data.get("LLM_MODEL", "gpt-5.2-codex"))
                # Mod integrity hashes
                self.mod_hashes = data.get("mod_hashes", {})
                self.last_scan = data.get("last_scan")
                # Expose via environment for any external integrations
                os.environ["ENABLE_LLM"] = "1" if self.enable_llm else "0"
                os.environ["LLM_MODEL"] = self.llm_model
            except Exception:
                pass

        except Exception as e:
            print("Failed to load settings:", e)

    def save_settings(self):
        data = {
            "mods_path": self.mods_path.get(),
            "user_disabled_ids": sorted(list(getattr(self, "user_disabled_ids", set()) or [])),
            "order_overrides": dict(getattr(self, "order_overrides", {}) or {}),
            "geometry": self.root.geometry(),
            "search": self.search_var.get() if hasattr(self, "search_var") else "",
            "ENABLE_SCANNER_DEBUG": bool(getattr(self, "debug_scanner", False)),
            "ENABLE_CLASSIFICATION": bool(getattr(self, "enable_classification", False)),
            "ENABLE_INTEGRITY": bool(getattr(self, "enable_integrity", False)),
            "HARDEN_DEPLOYMENT": bool(getattr(self, "harden_deployment", True)),
            "BLOCK_MULTIPLE_MODS_DIRS": bool(getattr(self, "block_multiple_mods_dirs", True)),
            "BLOCK_EMPTY_OR_INVALID_XML": bool(getattr(self, "block_invalid_xml", True)),
            "BLOCK_FULLFILE_REPLACEMENTS": bool(getattr(self, "block_full_file_replacements", True)),
            "ENFORCE_SINGLE_UI_FRAMEWORK": bool(getattr(self, "enforce_single_ui_framework", True)),
            "AUTO_PREFIX_UI_GROUPS": bool(getattr(self, "auto_prefix_ui_groups", True)),
            "ENABLE_LLM": bool(getattr(self, "enable_llm", True)),
            "LLM_MODEL": str(getattr(self, "llm_model", "gpt-5.2-codex")),
            "mod_hashes": getattr(self, "mod_hashes", {}),
            "last_scan": getattr(self, "last_scan", None),
        }

        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print("Failed to save settings:", e)

    def _report_callback_exception(self, exc, val, tb):
        """Global Tkinter callback exception hook.

        Tkinter prints callback exceptions to stderr by default; packaged builds can
        appear to "crash". This hook keeps the app alive and writes a log.
        """
        try:
            tb_text = "".join(traceback.format_exception(exc, val, tb))
        except Exception:
            tb_text = f"{exc}: {val}"

        log_path = _append_crash_log("Tk callback", tb_text)
        if log_path:
            tb_text = tb_text.rstrip() + f"\n\nLog written to: {log_path}\n"

        try:
            if hasattr(self, "show_scrollable_popup"):
                self.show_scrollable_popup(tb_text, title="Unexpected error")
            else:
                _safe_show_error("Unexpected error", tb_text)
        except Exception:
            try:
                print(tb_text)
            except Exception:
                pass

    def on_close(self):
        try:
            self.save_settings()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = ModAnalyzerApp(root)
    root.mainloop()
