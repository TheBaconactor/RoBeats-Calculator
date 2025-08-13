#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Bootstrapper.py

- Prefers PyPy; will relaunch under PyPy on Windows (Chocolatey) if available.
- Creates a venv in bin/venv and installs requirements.txt.
- (Optional) Scans Data/* for song files and builds TSVs (accepts even 1 file).
- Runs ONLY the entry script you specify (no hardcoded candidates, no optimizer).

How to use:
  # one-off via CLI
  python Bootstrapper.py --entry Manual_Calculator.py -v

  # or via config.ini in project root:
  [Run]
  entry_point = Manual_Calculator.py
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
# Paths & constants
# --------------------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent
BIN_DIR: Path = PROJECT_ROOT / "bin"
BUILD_DIR: Path = BIN_DIR / "build"
VENV_DIR: Path = BIN_DIR / "venv"
CACHE_FILE: Path = BIN_DIR / "paths_cache.json"
CONFIG_FILE: Path = PROJECT_ROOT / "config.ini"

# expected data folders/files (for optional scan/build step)
TARGET_DIRS = {"Easy", "Normal", "Hard"}
TARGET_FILES = {"Gear.csv", "Stats.txt"}

# required keys in individual song .txt files
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

# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------

def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s | %(message)s")

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Helpers / errors
# --------------------------------------------------------------------------------------

class BootstrapError(RuntimeError):
    pass

def run_cmd(cmd: Iterable[str] | str, *, shell: bool = False, check: bool = True, **kw) -> None:
    """Run a subprocess and raise a clean error if it fails."""
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
    """Re-exec under a different interpreter."""
    env = os.environ.copy()
    env.update(extra_env)
    args = [executable, str(script), *sys.argv[1:]]
    log.info("Re-launching with %s", executable)
    run_cmd(args, shell=False, env=env)
    sys.exit(0)

# --------------------------------------------------------------------------------------
# Windows admin / Chocolatey / PyPy
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
            sys.exit(0)
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
    for name in ("pypy3.exe", "pypy3"):
        p = which(name)
        if p:
            return p
    if is_windows():
        for envk in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(envk)
            if not base or not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                if "pypy3.exe" in files:
                    return os.path.join(root, "pypy3.exe")
    return None

def ensure_pypy_installed() -> str:
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
# Config
# --------------------------------------------------------------------------------------

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        cfg["Run"] = {"entry_point": ""}
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            cfg.write(f)
        log.info("Created default config at %s", CONFIG_FILE)
    else:
        cfg.read(CONFIG_FILE)
        if "Run" not in cfg:
            cfg["Run"] = {"entry_point": ""}
    return cfg

# --------------------------------------------------------------------------------------
# Virtualenv (PyPy-aware)
# --------------------------------------------------------------------------------------

def venv_bin_dir() -> Path:
    return VENV_DIR / ("Scripts" if is_windows() else "bin")

def venv_candidates() -> List[Path]:
    b = venv_bin_dir()
    return [
        b / ("python.exe" if is_windows() else "python"),
        b / ("pypy3.exe" if is_windows() else "pypy3"),
        b / ("pypy.exe" if is_windows() else "pypy"),
    ]

def venv_python() -> Path:
    for cand in venv_candidates():
        if cand.exists():
            return cand
    return venv_candidates()[0]  # for error text

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
        contents = ", ".join(p.name for p in venv_bin_dir().glob("*"))
        raise BootstrapError(
            f"Python executable not found in venv: {py}\n"
            f"Found in {venv_bin_dir()}: {contents or '(empty)'}"
        )

    # Upgrade base tooling
    run_cmd([str(py), "-m", "pip", "install", "--upgrade", "pip", "wheel"], shell=False)

    # psutil/numpy nuances with PyPy
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
# Optional data scan/build (accepts 1+ song files)
# --------------------------------------------------------------------------------------

def find_locations() -> Dict[str, str]:
    """
    Locate target directories/files starting from parent of project root
    (so sibling folders also count). This will find Data/Normal, Data/Hard, etc.
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
    try:
        text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines = [ln.lstrip("\ufeff").strip() for ln in text_lines if ln.strip()]
        return all(any(ln.startswith(k) for ln in lines) for k in SONG_REQUIRED_KEYS)
    except Exception:
        return False

def parse_song(path: Path) -> Dict[str, str]:
    result = {f: "0" for f in SONG_REQUIRED_KEYS}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                for field in SONG_REQUIRED_KEYS:
                    if line.startswith(field):
                        result[field] = line[len(field):].strip(" :\t")
                        break
    except Exception:
        pass
    return result

def tsv_table(header: Iterable[str], rows: Iterable[Iterable[str]]) -> str:
    out = ["\t".join(map(str, header))]
    out += ["\t".join(map(str, r)) for r in rows]
    return "\n".join(out) + "\n"

def build_songs_list(key: str, folder: Path, build_folder: Path, cache: Dict[str, str]) -> None:
    marker = build_folder / f".songs_build_{key}.done"
    if marker.exists():
        log.info("Songs build for '%s' already done.", key)
        return

    txts = sorted(folder.glob("*.txt"))
    header = [
        "Difficulty", "Song Name", "Primary Color", "Secondary Color",
        "Total Notes", "Last Note Time", "Fever Fill", "Fever Time", "Long Notes",
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
        rows.append([
            info["Difficulty"], info["Song Name"], info["Primary Color"], info["Secondary Color"],
            info["Total Notes"], info["Last Note Time"], info["Fever Fill"], info["Fever Time"], info["Long Notes"],
        ])

    if not rows:
        log.info("No valid song files found in '%s' for key '%s'.", folder, key)
        return

    build_folder.mkdir(parents=True, exist_ok=True)
    out_file = build_folder / f"Songs_Optimizer_Build_{key}.txt"
    out_file.write_text(tsv_table(header, rows), encoding="utf-8")
    log.info("Built %s with %d entr%s.", out_file, len(rows), "y" if len(rows) == 1 else "ies")

    cache[f"Build_{key}"] = str(out_file.resolve())
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    marker.touch()

def ensure_cache_defaults() -> Dict[str, str]:
    if not CACHE_FILE.exists():
        return {
            "script_path": str(Path(__file__).resolve()),
            "cache_dir": str(BIN_DIR.resolve()),
            "build_dir": str(BUILD_DIR.resolve()),
            "venv_path": str(VENV_DIR.resolve()),
        }
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def build_all_songs(cache: Dict[str, str]) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    for k in ("Easy", "Normal", "Hard"):
        fp = cache.get(k, "")
        if fp and Path(fp).is_dir():
            build_songs_list(k, Path(fp), BUILD_DIR, cache)
        else:
            log.debug("Folder for %s not found in cache/disk.", k)

def cache_data_paths(*, force_rescan: bool = False) -> Dict[str, str]:
    marker = BUILD_DIR / ".data_paths_cached"
    cache = ensure_cache_defaults()

    if marker.exists() and not force_rescan and all(cache.get(k, "") for k in ["Easy", "Normal", "Hard", "Gear", "Stats"]):
        log.info("Data paths already cached; skipping scan.")
        return cache

    log.info("Scanning for data paths...")
    locs = find_locations()
    cache.update(locs)
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    build_all_songs(cache)
    marker.touch()
    return cache

# --------------------------------------------------------------------------------------
# Entry resolution & run (no hardcoded candidates)
# --------------------------------------------------------------------------------------

def auto_discover_entry() -> Optional[Path]:
    """If exactly one .py (other than this bootstrapper) exists in root, use it."""
    self_name = Path(__file__).name.lower()
    py_files = [p for p in PROJECT_ROOT.glob("*.py") if p.name.lower() != self_name]
    if len(py_files) == 1:
        log.info("Auto-detected entry script: %s", py_files[0].name)
        return py_files[0]
    return None

def resolve_entry_point(args: argparse.Namespace, cfg: configparser.ConfigParser) -> Path:
    # CLI wins
    if args.entry:
        p = Path(args.entry)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p.resolve()

    # config.ini [Run] entry_point
    entry_cfg = cfg.get("Run", "entry_point", fallback="").strip()
    if entry_cfg:
        p = Path(entry_cfg)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p.resolve()

    # heuristic (no hardcoded names): exactly one .py besides this file
    autod = auto_discover_entry()
    if autod:
        return autod.resolve()

    raise BootstrapError(
        "No entry script specified. Use --entry <script.py> or set [Run] entry_point in config.ini."
    )

def run_entry(py_exe: Path, entry_script: Path) -> None:
    if not entry_script.exists():
        raise BootstrapError(f"Entry script not found: {entry_script}")
    log.info("Running %s with %s", entry_script.name, py_exe)
    run_cmd([str(py_exe), str(entry_script)], shell=False)

# --------------------------------------------------------------------------------------
# PyPy relaunch
# --------------------------------------------------------------------------------------

def ensure_run_on_pypy(*, allow_cpython: bool) -> None:
    if is_pypy() or allow_cpython:
        return
    pypy = find_pypy_executable() or ensure_pypy_installed()
    relaunch_with_env(pypy, Path(__file__).resolve(), {"LAUNCHED_WITH_PYPY": "1"})

# --------------------------------------------------------------------------------------
# CLI & main
# --------------------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap environment and run an entry script.")
    p.add_argument("--entry", help="Path to the entry script to run (relative or absolute).")
    p.add_argument("--allow-cpython", action="store_true", help="Skip PyPy relaunch and run with current interpreter.")
    p.add_argument("--force-venv", action="store_true", help="Recreate/repair the virtual environment.")
    p.add_argument("--skip-scan", action="store_true", help="Skip data scan/build step.")
    p.add_argument("--force-rescan", action="store_true", help="Force rescan/build even if cached.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return p.parse_args()

def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    log.info("Interpreter: %s | %s %s", sys.executable, sys.implementation.name, platform.python_version())

    ensure_run_on_pypy(allow_cpython=args.allow_cpython or bool(os.environ.get("LAUNCHED_WITH_PYPY")))

    ensure_venv(force=args.force_venv)

    if not args.skip_scan and (PROJECT_ROOT / "Data").exists():
        cache = cache_data_paths(force_rescan=args.force_rescan)
        log.debug("Cache snapshot: %s", json.dumps(cache, indent=2))
    else:
        log.debug("Skipping data scan/build.")

    cfg = load_config()
    entry = resolve_entry_point(args, cfg)
    run_entry(venv_python(), entry)

if __name__ == "__main__":
    try:
        main()
    except BootstrapError as e:
        log.error("%s", e)
        sys.exit(2)
    except KeyboardInterrupt:
        log.error("Interrupted.")
        sys.exit(130)
