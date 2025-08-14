#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import venv
import shutil
import subprocess
from pathlib import Path
from collections import deque

# ------------------------------------------------------------------------------
# Paths & constants
# ------------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
BIN_DIR      = PROJECT_ROOT / "bin"
BUILD_DIR    = BIN_DIR / "build"
VENV_DIR     = BIN_DIR / "venv"
CACHE_FILE   = BIN_DIR / "paths_cache.json"

MANUAL_CALC  = PROJECT_ROOT / "Manual_Calculator.py"

REQUIREMENTS = PROJECT_ROOT / "requirements.txt"

# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
def _log(level: str, msg: str) -> None:
    print(f"{level} | {msg}")

def info(msg: str) -> None:
    _log("INFO", msg)

def warn(msg: str) -> None:
    _log("WARNING", msg)

def err(msg: str) -> None:
    _log("ERROR", msg)

def exit_err(msg: str) -> None:
    err(msg)
    sys.exit(1)

# ------------------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------------------
def run_cmd(cmd, shell=False, **kw):
    """
    Run a command and raise a clean error on failure.
    """
    try:
        subprocess.run(cmd, shell=shell, check=True, **kw)
    except subprocess.CalledProcessError as e:
        exit_err(f"Command failed ({cmd}): {e}")

def online(url="https://pypi.org", timeout=5):
    try:
        import urllib.request
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False

def get_search_dirs():
    """Windows helper: broader search roots for exe discovery if needed."""
    if sys.platform != "win32":
        return []
    keys = ["PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"]
    res = []
    for k in keys:
        v = os.environ.get(k)
        if v and os.path.isdir(v):
            res.append(v)
    return res

def find_in_path(names):
    """Return first existing executable in PATH from a list of candidates."""
    for n in names:
        p = shutil.which(n)
        if p:
            return Path(p)
    return None

# ------------------------------------------------------------------------------
# Cache helpers
# ------------------------------------------------------------------------------
def save_cache(data: dict) -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            warn(f"Cache load warning: {e}")
    return {}

def get_cache() -> dict:
    cache = load_cache()
    defaults = {
        "script_path": str(Path(__file__).resolve()),
        "cache_dir":   str(BIN_DIR.resolve()),
        "build_dir":   str(BUILD_DIR.resolve()),
        "venv_path":   str(VENV_DIR.resolve())
    }
    changed = False
    for k, v in defaults.items():
        if k not in cache:
            cache[k] = v
            changed = True
    if changed:
        save_cache(cache)
    return cache

# ------------------------------------------------------------------------------
# Virtual environment
# ------------------------------------------------------------------------------
def detect_venv_python() -> Path:
    """
    Return the Python executable inside the venv across platforms and runtimes.
    Windows (CPython):  .../Scripts/python.exe
    Windows (PyPy):     .../Scripts/pypy3.exe
    *nix (CPython):     .../bin/python
    *nix (PyPy):        .../bin/pypy3
    """
    if sys.platform == "win32":
        candidates = [VENV_DIR / "Scripts" / "python.exe",
                      VENV_DIR / "Scripts" / "pypy3.exe",
                      VENV_DIR / "Scripts" / "python3.exe"]
    else:
        candidates = [VENV_DIR / "bin" / "python",
                      VENV_DIR / "bin" / "pypy3",
                      VENV_DIR / "bin" / "python3"]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # default as a helpful pointer in error

def ensure_venv():
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    if not VENV_DIR.exists():
        info(f"Creating virtual environment at {VENV_DIR} ...")
        try:
            venv.create(VENV_DIR, with_pip=True)
        except Exception as e:
            exit_err(f"Venv creation failed: {e}")
    else:
        info("Virtual environment already set up.")

    py_exe = detect_venv_python()
    if not py_exe.exists():
        exit_err(f"Python executable not found in venv: {py_exe}")

    # Keep pip/wheel fresh; avoid pinning unless needed.
    run_cmd([str(py_exe), "-m", "pip", "install", "--upgrade", "pip", "wheel"])

    # If requirements.txt exists, install it. Do NOT force any URL wheels.
    if REQUIREMENTS.exists():
        if online():
            info("Installing dependencies from requirements.txt ...")
            # Prefer wheels where available, but don't hard fail if an sdist slips in.
            run_cmd([str(py_exe), "-m", "pip", "install", "-r", str(REQUIREMENTS), "--upgrade"])
        else:
            warn("Offline: skipping requirements install.")

    return py_exe

# ------------------------------------------------------------------------------
# Data discovery & building
# ------------------------------------------------------------------------------
def find_locations():
    """
    Breadth-first scan starting from parent of project to find:
      Directories: Easy, Normal, Hard
      Files: Gear.csv, Stats.txt
    """
    results = {k: "" for k in ["Easy", "Normal", "Hard", "Gear", "Stats"]}
    targets_dirs  = set(["Easy", "Normal", "Hard"])
    targets_files = set(["Gear.csv", "Stats.txt"])

    # Start from parent so sibling dirs are considered.
    base_dir = PROJECT_ROOT.parent if PROJECT_ROOT.parent != PROJECT_ROOT else PROJECT_ROOT
    queue = deque([base_dir])
    visited = {str(base_dir.resolve())}

    while queue and (targets_dirs or targets_files):
        curr = queue.popleft()
        try:
            for entry in os.scandir(curr):
                try:
                    p = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in targets_dirs:
                            results[entry.name] = str(p.resolve())
                            targets_dirs.remove(entry.name)
                        r = str(p.resolve())
                        if r not in visited:
                            visited.add(r)
                            queue.append(p)
                    elif entry.is_file(follow_symlinks=False):
                        if entry.name in targets_files:
                            key = entry.name.split('.')[0]
                            results[key] = str(p.resolve())
                            targets_files.remove(entry.name)
                    if not targets_dirs and not targets_files:
                        break
                except Exception:
                    continue
        except Exception:
            continue
    return results

def file_format_ok(p: Path) -> bool:
    """
    Expect lines containing required headers somewhere in the file (BOM tolerant).
    """
    reqs = [
        "Song Name", "Difficulty", "Primary Color", "Secondary Color",
        "Last Note Time", "Total Notes", "Fever Fill", "Fever Time", "Long Notes"
    ]
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            lines = [ln.lstrip("\ufeff").strip() for ln in f if ln.strip()]
        return all(any(line.startswith(r) for line in lines) for r in reqs)
    except Exception:
        return False

def parse_song(p: Path) -> dict:
    fields = [
        "Song Name", "Difficulty", "Primary Color", "Secondary Color",
        "Last Note Time", "Total Notes", "Fever Fill", "Fever Time", "Long Notes"
    ]
    res = {f: "0" for f in fields}
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                for field in fields:
                    if line.startswith(field):
                        res[field] = line[len(field):].strip(" :\t")
                        break
    except Exception:
        pass
    return res

def fmt_table(hdr, rows):
    out = ["\t".join(str(x) for x in hdr)]
    out += ["\t".join(str(x) for x in r) for r in rows]
    return "\n".join(out) + "\n"

def build_songs_list(key: str, folder_path: str, build_folder: Path, cache: dict):
    """
    Build a TSV for a given difficulty key. No hard minimum row count:
    - 0 valid files  -> skip politely (INFO)
    - ≥1 valid files -> build file
    """
    marker = build_folder / f".songs_build_{key}.done"
    if marker.exists():
        info(f"Songs build for '{key}' already done.")
        return

    txts = list(Path(folder_path).glob("*.txt"))
    rows = []
    hdr  = ["Difficulty", "Song Name", "Primary Color", "Secondary Color",
            "Total Notes", "Last Note Time", "Fever Fill", "Fever Time", "Long Notes"]

    for txt in txts:
        if not file_format_ok(txt):
            warn(f"Skipping {txt} (format issue).")
            continue
        info_dict = parse_song(txt)
        if info_dict["Song Name"] == "???" or info_dict["Difficulty"] == "???":
            warn(f"Skipping {txt} (missing fields).")
            continue
        rows.append([
            info_dict["Difficulty"], info_dict["Song Name"],
            info_dict["Primary Color"], info_dict["Secondary Color"],
            info_dict["Total Notes"], info_dict["Last Note Time"],
            info_dict["Fever Fill"], info_dict["Fever Time"], info_dict["Long Notes"]
        ])

    if not rows:
        info(f"No valid files found in '{folder_path}' for key '{key}' – skipping build.")
        return

    table = fmt_table(hdr, rows)
    out_file = build_folder / f"Songs_Optimizer_Build_{key}.txt"
    out_file.write_text(table, encoding="utf-8")
    info(f"Built {out_file} with {len(rows)} entries.")
    cache[f"Build_{key}"] = str(out_file.resolve())
    marker.touch()

def build_all_songs(cache: dict):
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    for k in ["Easy", "Normal", "Hard"]:
        fp = cache.get(k, "")
        if fp and Path(fp).is_dir():
            build_songs_list(k, fp, BUILD_DIR, cache)
        else:
            info(f"Folder for {k} not found in cache/disk.")
    save_cache(cache)

def cache_data_paths():
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    marker = BUILD_DIR / ".data_paths_cached"
    cache = load_cache()
    need_scan = not marker.exists() and not all(cache.get(k, "") for k in ["Easy", "Normal", "Hard", "Gear", "Stats"])

    if need_scan:
        info("Scanning for data paths...")
        locs = find_locations()
        cache.update(locs)
        save_cache(cache)
        build_all_songs(cache)
        marker.touch()
    else:
        info("Data paths cached; skipping scan.")

# ------------------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------------------
def run_manual_calculator(py_exe: Path):
    if not MANUAL_CALC.exists():
        exit_err("Manual_Calculator.py not found in project root.")

    info("Launching Manual_Calculator.py ...")
    run_cmd([str(py_exe), str(MANUAL_CALC)], shell=False)

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
def main():
    # Interpreter banner
    impl = sys.implementation.name
    ver  = sys.version.split()[0]
    info(f"Interpreter: {sys.executable} | {impl} {ver}")

    # Prepare environment
    py_exe = ensure_venv()  # creates venv and returns its python (or pypy3) path
    cache_data_paths()

    # Run Manual Calculator exclusively
    run_manual_calculator(py_exe)

if __name__ == "__main__":
    main()
