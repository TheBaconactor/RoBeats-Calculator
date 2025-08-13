#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Bootstrap & Runner (bootstrap.py)

- Prepares a reproducible Python environment (prefers PyPy).
- Discovers project data paths and builds TSV song lists.
- Runs Manual_Calculator.py exclusively.
- CLI flags for CI/dev usage and verbosity.

Usage:
    python bootstrap.py [-v] [--allow-cpython] [--force-venv] [--force-rescan] [--skip-optimizer] [--profile]

Author: Refactor provided by ChatGPT
"""

from __future__ import annotations

import argparse
import configparser
import ctypes
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import venv
from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# --------------------------------------------------------------------------------------
# Paths & Constants
# --------------------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent
BIN_DIR: Path = PROJECT_ROOT / "bin"
BUILD_DIR: Path = BIN_DIR / "build"
VENV_DIR: Path = BIN_DIR / "venv"
CACHE_FILE: Path = BIN_DIR / "paths_cache.json"
RUN_CONFIG_FILE: Path = PROJECT_ROOT / "run_config.ini"
PROFILE_FILE: Path = BIN_DIR / "profile.prof"

TARGET_DIRS = {"Easy", "Normal", "Hard"}
TARGET_FILES = {"Gear.csv", "Stats.txt"}

SONG_REQUIRED_KEYS = [
    "Song Name",
    "Difficulty",
    "Primary Color",
    "Secondary Color",
    "Last Note Time",
    "Total Notes",
    "Fever Fill",
    "Fever Time",
    "Long Notes",
]

OPTIMIZER_CANDIDATES = [
    "Manual_Calculator.py",
    "Optimizer - Latest.py",
    "Optimizer-Latest.py",
    "optimizer - latest.py",
    "optimizer-latest.py",
    "Optimizer.py",
    "optimizer.py",
]

# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------

def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s | %(message)s",
    )


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# Errors & Utilities
# --------------------------------------------------------------------------------------

class BootstrapError(RuntimeError):
    """Fatal bootstrap/setup error."""


def run_cmd(cmd: Iterable[str] | str, *, shell: bool = False, check: bool = True, **kw) -> None:
    """Run a subprocess and raise BootstrapError on failure."""
    try:
        subprocess.run(cmd, shell=shell, check=check, **kw)
    except subprocess.CalledProcessError as e:
        raise BootstrapError(f"Command failed ({cmd!r}): {e}") from e


def online(url: str = "https://www.google.com", timeout: int = 5) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)  # simple reachability check
        return True
    except Exception:
        return False


def is_windows() -> bool:
    return sys.platform.startswith("win32")


def is_pypy() -> bool:
    return sys.implementation.name.lower() == "pypy"


def which(exe: str) -> Optional[str]:
    return shutil.which(exe)


def relaunch_with_env(executable: str, script: Path, extra_env: Dict[str, str]) -> None:
    """Re-exec under a different Python interpreter."""
    env = os.environ.copy()
    env.update(extra_env)
    args = [executable, str(script), *sys.argv[1:]]
    log.info("Re-launching with %s", executable)
    run_cmd(args, shell=False, env=env)
    sys.exit(0)


# --------------------------------------------------------------------------------------
# Windows helpers (Chocolatey / admin)
# --------------------------------------------------------------------------------------

def is_admin_windows() -> bool:
    if not is_windows():
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0  # type: ignore[attr-defined]
    except Exception:
        return False


def ensure_chocolatey() -> None:
    """Install Chocolatey if missing (Windows only). Requires admin."""
    if not is_windows():
        return

    if which("choco.exe"):
        log.debug("Chocolatey found.")
        return

    if not is_admin_windows():
        params = " ".join(f'"{arg}"' for arg in sys.argv)
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", sys.executable, params, None, 1
            )
            if int(ret) <= 32:
                raise RuntimeError(f"ShellExecuteW returned {ret}")
            sys.exit(0)  # parent exits; elevated child will continue
        except Exception as e:
            raise BootstrapError(f"Administrator elevation failed: {e}") from e

    if not online():
        raise BootstrapError("No Internet connection. Cannot install Chocolatey.")

    log.info("Installing Chocolatey...")
    ps = (
        "powershell -NoProfile -ExecutionPolicy Bypass -Command "
        "\"iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))\""
    )
    run_cmd(ps, shell=True)
    time.sleep(5)
    if not which("choco.exe"):
        raise BootstrapError("Chocolatey installation failed.")
    log.info("Chocolatey installed.")


def find_pypy_executable() -> Optional[str]:
    """Find the PyPy3 executable on this system."""
    candidates = ["pypy3.exe", "pypy3"]
    for c in candidates:
        p = which(c)
        if p:
            return p
    if is_windows():
        env_candidates = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)")]
        env_candidates = [p for p in env_candidates if p and os.path.isdir(p)]
        for base in env_candidates:
            for root, _, files in os.walk(base):
                if "pypy3.exe" in files:
                    return os.path.join(root, "pypy3.exe")
    return None


def ensure_pypy_installed() -> str:
    """Ensure PyPy exists; on Windows install via Chocolatey if needed."""
    pypy = find_pypy_executable()
    if pypy:
        return pypy

    if is_windows():
        if not online():
            raise BootstrapError("No Internet connection. Cannot install PyPy3.")
        ensure_chocolatey()
        log.info("Installing PyPy3 via Chocolatey...")
        run_cmd(["choco", "install", "pypy3", "-y"], shell=False)
        time.sleep(5)
        pypy = find_pypy_executable()
        if not pypy:
            raise BootstrapError("PyPy3 installation failed.")
        log.info("PyPy3 installed.")
        return pypy

    raise BootstrapError("PyPy3 not found. Please install pypy3 and re-run.")


# --------------------------------------------------------------------------------------
# Config & Cache
# --------------------------------------------------------------------------------------

def load_run_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not RUN_CONFIG_FILE.exists():
        cfg["Profiling"] = {"optimizer_profiling": "False"}
        with RUN_CONFIG_FILE.open("w", encoding="utf-8") as f:
            cfg.write(f)
        log.info("Created default run config at %s", RUN_CONFIG_FILE)
    else:
        cfg.read(RUN_CONFIG_FILE)
        if "Profiling" not in cfg:
            cfg["Profiling"] = {"optimizer_profiling": "False"}
    return cfg


def save_cache(data: Dict[str, str]) -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_cache() -> Dict[str, str]:
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Failed to load cache (%s): %s", CACHE_FILE, e)
        return {}


def ensure_cache_defaults() -> Dict[str, str]:
    cache = load_cache()
    cache.setdefault("script_path", str(Path(__file__).resolve()))
    cache.setdefault("cache_dir", str(BIN_DIR.resolve()))
    cache.setdefault("build_dir", str(BUILD_DIR.resolve()))
    cache.setdefault("venv_path", str(VENV_DIR.resolve()))
    save_cache(cache)
    return cache


# --------------------------------------------------------------------------------------
# Virtual environment
# --------------------------------------------------------------------------------------

def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if is_windows() else "bin/python")


def ensure_venv(*, force: bool = False) -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    VENV_DIR.mkdir(parents=True, exist_ok=True)
    marker = BUILD_DIR / ".venv_setup_done"

    if marker.exists() and VENV_DIR.exists() and not force:
        log.info("Virtual environment already set up.")
        return

    if not online():
        raise BootstrapError("No Internet connection. Cannot set up virtual environment.")

    log.info("Creating virtual environment at %s ...", VENV_DIR)
    try:
        venv.create(VENV_DIR, with_pip=True)
    except Exception as e:
        raise BootstrapError(f"Failed to create venv: {e}") from e

    py = venv_python()
    if not py.exists():
        raise BootstrapError(f"Python executable not found in venv: {py}")

    run_cmd([str(py), "-m", "pip", "install", "--upgrade", "pip", "wheel"], shell=False)

    if is_pypy() and is_windows():
        run_cmd(
            [str(py), "-m", "pip", "install", "--no-cache-dir",
             "https://files.pythonhosted.org/packages/2a/4a/psutil-5.9.7-pypy37_pp73-win_amd64.whl", "numpy"],
            shell=False,
        )
    elif is_pypy():
        run_cmd([str(py), "-m", "pip", "install", "--no-cache-dir", "psutil==5.9.7", "numpy"], shell=False)
    else:
        run_cmd([str(py), "-m", "pip", "install", "--no-cache-dir", "--only-binary", ":all:", "psutil==5.9.7", "numpy"], shell=False)

    req = PROJECT_ROOT / "requirements.txt"
    if req.exists():
        run_cmd([str(py), "-m", "pip", "install", "-r", str(req), "--upgrade", "--only-binary", ":all:"], shell=False)

    marker.touch()
    log.info("Virtual environment setup complete.")


# --------------------------------------------------------------------------------------
# Data scanning & building
# --------------------------------------------------------------------------------------

def sys_search_dirs() -> List[str]:
    """Windows-only: reasonable places to search; return [] elsewhere."""
    if not is_windows():
        return []
    out = []
    for k in ("PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        v = os.environ.get(k)
        if v and os.path.isdir(v):
            out.append(v)
    return out


def find_locations() -> Dict[str, str]:
    """
    Locate target directories/files starting from the parent of PROJECT_ROOT
    (so sibling folders are included).
    """
    results: Dict[str, str] = {k: "" for k in ["Easy", "Normal", "Hard", "Gear", "Stats"]}
    targets_dirs = set(TARGET_DIRS)
    targets_files = set(TARGET_FILES)

    base_dir = PROJECT_ROOT.parent if PROJECT_ROOT.parent != PROJECT_ROOT else PROJECT_ROOT
    q: deque[Path] = deque([base_dir.resolve()])
    visited = {str(base_dir.resolve())}

    while q and (targets_dirs or targets_files):
        cur = q.popleft()
        try:
            for entry in os.scandir(cur):
                p = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in targets_dirs:
                        results[entry.name] = str(p.resolve())
                        targets_dirs.remove(entry.name)
                    rp = str(p.resolve())
                    if rp not in visited:
                        visited.add(rp)
                        q.append(p)
                elif entry.is_file(follow_symlinks=False):
                    if entry.name in targets_files:
                        key = entry.name.split(".")[0]
                        results[key] = str(p.resolve())
                        targets_files.remove(entry.name)
        except Exception:
            continue
    return results


def song_file_format_ok(path: Path) -> bool:
    """Quick validation: required keys appear line-started, ignoring BOM/spaces."""
    try:
        text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines = [ln.lstrip("\ufeff").strip() for ln in text_lines if ln.strip()]
        return all(any(ln.startswith(k) for ln in lines) for k in SONG_REQUIRED_KEYS)
    except Exception:
        return False


def parse_song(path: Path) -> Dict[str, str]:
    fields = SONG_REQUIRED_KEYS
    result = {f: "0" for f in fields}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                for field in fields:
                    if line.startswith(field):
                        result[field] = line[len(field) :].strip(" :\t")
                        break
    except Exception:
        pass
    return result


def tsv_table(header: Iterable[str], rows: Iterable[Iterable[str]]) -> str:
    out = ["\t".join(map(str, header))]
    out += ["\t".join(map(str, r)) for r in rows]
    return "\n".join(out) + "\n"


def build_songs_list(key: str, folder: Path, build_folder: Path, cache: Dict[str, str]) -> None:
    """
    Build a TSV of all valid .txt song files in `folder` and write to build_folder.
    Accepts 1 or more valid song files (no longer requires >=25).
    """
    marker = build_folder / f".songs_build_{key}.done"
    if marker.exists():
        log.info("Songs build for '%s' already done.", key)
        return

    txts = sorted(folder.glob("*.txt"))
    header = [
        "Difficulty",
        "Song Name",
        "Primary Color",
        "Secondary Color",
        "Total Notes",
        "Last Note Time",
        "Fever Fill",
        "Fever Time",
        "Long Notes",
    ]

    rows: List[List[str]] = []
    for txt in txts:
        if not song_file_format_ok(txt):
            log.debug("Skipping %s (format issue).", txt)
            continue
        info = parse_song(txt)
        if info["Song Name"] == "???" or info["Difficulty"] == "???":
            log.debug("Skipping %s (missing fields).", txt)
            continue
        rows.append(
            [
                info["Difficulty"],
                info["Song Name"],
                info["Primary Color"],
                info["Secondary Color"],
                info["Total Notes"],
                info["Last Note Time"],
                info["Fever Fill"],
                info["Fever Time"],
                info["Long Notes"],
            ]
        )

    if not rows:
        log.info("No valid song files found in '%s' for key '%s'.", folder, key)
        return

    build_folder.mkdir(parents=True, exist_ok=True)
    out_file = build_folder / f"Songs_Optimizer_Build_{key}.txt"
    out_file.write_text(tsv_table(header, rows), encoding="utf-8")
    log.info("Built %s with %d entr%s.", out_file, len(rows), "y" if len(rows) == 1 else "ies")

    cache[f"Build_{key}"] = str(out_file.resolve())
    save_cache(cache)
    marker.touch()


def build_all_songs(cache: Dict[str, str]) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    for k in ("Easy", "Normal", "Hard"):
        fp = cache.get(k, "")
        if fp and Path(fp).is_dir():
            build_songs_list(k, Path(fp), BUILD_DIR, cache)
        else:
            log.debug("Folder for %s not found in cache/disk.", k)


def cache_data_paths(*, force_rescan: bool = False) -> Dict[str, str]:
    """
    Populate cache with discovered data paths and build song lists.
    Respects a marker to avoid re-scanning unless forced.
    """
    marker = BUILD_DIR / ".data_paths_cached"
    cache = ensure_cache_defaults()

    if marker.exists() and not force_rescan and all(cache.get(k, "") for k in ["Easy", "Normal", "Hard", "Gear", "Stats"]):
        log.info("Data paths already cached; skipping scan.")
        return cache

    log.info("Scanning for data paths...")
    locs = find_locations()
    cache.update(locs)
    save_cache(cache)

    build_all_songs(cache)
    marker.touch()
    return cache


# --------------------------------------------------------------------------------------
# Optimizer (Manual_Calculator.py exclusively)
# --------------------------------------------------------------------------------------

def find_optimizer() -> Optional[Path]:
    """
    Prefer Manual_Calculator.py exclusively; fall back to other candidates if present.
    """
    for name in OPTIMIZER_CANDIDATES:
        candidate = PROJECT_ROOT / name
        if candidate.exists():
            return candidate
    return None


def run_optimizer(py_exe: Path, *, enable_profiling: bool) -> None:
    script = find_optimizer()
    if not script:
        raise BootstrapError("Manual_Calculator.py (or other candidate) not found in project root.")
    args = [str(py_exe), str(script)]
    if enable_profiling:
        args.append("--profile")
    log.info("Running %s with %s", script.name, py_exe)
    run_cmd(args, shell=False)


# --------------------------------------------------------------------------------------
# PyPy relaunch
# --------------------------------------------------------------------------------------

def ensure_run_on_pypy(*, allow_cpython: bool) -> None:
    """
    Relaunch this script under PyPy unless:
    - already running under PyPy, or
    - allow_cpython is True.
    """
    if is_pypy() or allow_cpython:
        return

    pypy = find_pypy_executable() or ensure_pypy_installed()
    relaunch_with_env(pypy, Path(__file__).resolve(), {"LAUNCHED_WITH_PYPY": "1"})


# --------------------------------------------------------------------------------------
# CLI & Main
# --------------------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap environment and run Manual_Calculator.py.")
    p.add_argument("--allow-cpython", action="store_true", help="Skip PyPy relaunch and run with current interpreter.")
    p.add_argument("--force-venv", action="store_true", help="Recreate/repair the virtual environment.")
    p.add_argument("--force-rescan", action="store_true", help="Force rescanning of data paths.")
    p.add_argument("--skip-optimizer", action="store_true", help="Do not run the optimizer after setup.")
    p.add_argument("--profile", action="store_true", help="Enable optimizer profiling (overrides run_config.ini).")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    log.info("Interpreter: %s | %s %s", sys.executable, sys.implementation.name, platform.python_version())

    ensure_run_on_pypy(allow_cpython=args.allow_cpython or bool(os.environ.get("LAUNCHED_WITH_PYPY")))

    ensure_venv(force=args.force_venv)

    cache = cache_data_paths(force_rescan=args.force_rescan)
    log.debug("Cache snapshot: %s", json.dumps(cache, indent=2))

    if not args.skip_optimizer:
        cfg = load_run_config()
        enable_prof = args.profile or cfg.getboolean("Profiling", "optimizer_profiling", fallback=False)
        run_optimizer(venv_python(), enable_profiling=enable_prof)

        if enable_prof and PROFILE_FILE.exists():
            import pstats  # local import
            log.info("Profiling results (top 20 cumulative):")
            pstats.Stats(str(PROFILE_FILE)).strip_dirs().sort_stats("cumulative").print_stats(20)
        elif enable_prof:
            log.warning("Profiling enabled but no profile produced at %s", PROFILE_FILE)


if __name__ == "__main__":
    try:
        main()
    except BootstrapError as e:
        log.error("%s", e)
        sys.exit(2)
    except KeyboardInterrupt:
        log.error("Interrupted.")
        sys.exit(130)
