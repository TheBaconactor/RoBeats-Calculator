#!/usr/bin/env python3
"""
Forked version of optimizer.py that retains basic logic for loading paths, reading tables,
parsing gear CSV/configuration, parsing song files, and now also performing score calculation
using integrated logic from another script.

New Features Added:
  - After loading gear info and the stats table, the script builds lookup arrays for the key
    stats (Perfect Points, Combo Multiplier, Fever Multiplier, Fever Fill Rate, Fever Time)
    using your lookup logic.
  - The song data is transformed into a calculation-friendly format.
  - The calculation logic (base value, combo and fever scoring, etc.) is adapted from the
    other person’s script but adjusted to use your lookup mechanism.
  - Finally, the calculated score blocks are summed and the total is printed.
    
Lookup Stats Example:
  Perfect Points: 25-table row. It is -135 from top. Looked-up value: 308.0
  Combo Multiplier: 77-table row. It is -83 from top. Looked-up value: 2.59780292
  Fever Multiplier: 66-table row. It is -94 from top. Looked-up value: 5.14399549
  Fever Fill Rate: 75-table row. It is -85 from top. Looked-up value: 0.3062201924
  Fever Time: 15-table row. It is -145 from top. Looked-up value: 1.638874544
"""

import os, re, csv, json, configparser, logging
from io import StringIO
import numpy as np
from math import floor, ceil

# --- Helper Conversion Functions ---
def safe_int(val, default=0):
    try:
        return int(val) if val not in (None, "") else default
    except Exception:
        return default

def safe_float(val, default=0.0):
    try:
        return float(val) if val not in (None, "") else default
    except Exception:
        return default

# --- Setup Directories and Logging ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(SCRIPT_DIR, "bin")
os.makedirs(BIN_DIR, exist_ok=True)
log_file_path = os.path.join(BIN_DIR, "error.log")
logging.basicConfig(filename=log_file_path,
                    level=logging.WARNING,
                    format="%(asctime)s %(levelname)s: %(message)s")

# --- Load Cached Paths ---
def load_paths_cache():
    pc = os.path.join(SCRIPT_DIR, "bin", "paths_cache.json")
    if os.path.exists(pc):
        with open(pc, "r", encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError("paths_cache.json not found in bin folder.")

# --- Read Stats Table ---
def read_table(fp):
    try:
        with open(fp, "r") as f:
            lines = f.read().splitlines()
        if not lines:
            return []
        table = []
        # Skip header line
        for line in lines[1:]:
            parts = line.split()
            if parts:
                try:
                    row = [float(x) for x in parts]
                    table.append(row)
                except Exception as e:
                    logging.error(f"Error parsing row in table {fp}: {e}")
        return table
    except Exception as e:
        logging.error(f"Error reading {fp}: {e}")
        return []

# --- Read Gear CSV ---
def read_gear_csv(fp):
    gear_data = {}
    try:
        with open(fp, "r", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            cols = ["Perfect Points", "Combo Multiplier", "Fever Multiplier",
                    "Fever Fill Rate", "Fever Time", "Chill", "Flow", "Rush", "Beat", "Vibe"]
            for row in reader:
                try:
                    stats = [safe_int(row.get(col, 0)) for col in cols]
                except Exception as e:
                    logging.error(f"Gear {row.get('Gear Name', 'Unknown')} conversion error: {e}")
                    stats = [0] * len(cols)
                gear_name = row.get("Gear Name", "").strip()
                if gear_name:
                    gear_data[gear_name] = stats
        return gear_data
    except Exception as e:
        logging.error(f"Error reading gear CSV {fp}: {e}")
        return {}

# --- File Name Sanitization ---
_SANITIZE_RE = re.compile(r'[\\/*?:"<>|.%]')
sanitize = lambda fn: _SANITIZE_RE.sub("", fn)

# --- Adjust Mini Values ---
def adjust_mini_values(vals):
    adjusted = []
    for i, v in enumerate(vals):
        factor = 4 if i < 5 else 5
        adjusted.append(v * factor)
    return adjusted

# --- Tier Colour Mapping ---
TIER_COLOUR_VALUES = {
    "None": {"Perfect Points": 0, "Colour Points": 0},
    "T1":   {"Perfect Points": 25, "Colour Points": 35},
    "T5":   {"Perfect Points": 25, "Colour Points": 30},
    "T10":  {"Perfect Points": 20, "Colour Points": 25},
    "T15":  {"Perfect Points": 15, "Colour Points": 20}
}
def get_tier_colour_values():
    return TIER_COLOUR_VALUES

COLOR_INDEX = {"Chill": 5, "Flow": 6, "Rush": 7, "Beat": 8, "Vibe": 9}

def calc_output(tier, color, tv):
    td = tv.get(tier, {"Perfect Points": 0, "Colour Points": 0})
    out = [td["Perfect Points"]] + [0] * 9
    if color in COLOR_INDEX:
        index = COLOR_INDEX[color]
        out[index] = td["Colour Points"]
    return out

# --- Load Gear Info ---
def load_gear_info(cfg, paths, tbl=None):
    keys = ["hat", "neck", "face", "shirt", "back", "pants"]
    gs = {}
    for k in keys:
        key_name = (k + "s" if k == "hat" else k)
        gs[key_name] = cfg.get("Gear", k, fallback="")
    for i in range(1, 4):
        gs[f"mini{i}"] = cfg.get("Gear", f"mini{i}", fallback="")
    gs["tier"] = cfg.get("Gear", "tier", fallback="None")
    gs["color"] = cfg.get("Gear", "color", fallback="Chill")
    
    gear_csv_path = paths.get("Gear", "")
    gd = read_gear_csv(gear_csv_path)
    
    sel = {}
    for k in keys:
        gear_key = gs[k + "s"] if k == "hat" else gs[k]
        sel[k] = list(map(safe_int, gd.get(gear_key, [0] * 10)))
    for i in range(1, 4):
        mini_key = gs.get(f"mini{i}", "")
        sel[f"mini{i}"] = adjust_mini_values(list(map(safe_int, gd.get(mini_key, [0] * 10))))
    
    tv = get_tier_colour_values()
    tier_out = calc_output(gs["tier"], gs["color"], tv)
    
    # Calculate gear sum manually
    gear_sum = [0] * 10
    for vals in sel.values():
        for i in range(10):
            gear_sum[i] += vals[i]
    for i in [0, 5, 6, 7, 8, 9]:
        gear_sum[i] += tier_out[i]
    
    if cfg.getboolean("InputValues", "ignore_selected_gear_stats", fallback=False):
        keys_override = ["perfect_points", "combo_multiplier", "fever_multiplier", 
                         "fever_fill", "fever_time", "chill", "flow", "rush", "beat", "vibe"]
        override_stats = [cfg.getint("InputValues", k, fallback=0) for k in keys_override]
        gear_sum = override_stats

    if tbl is None:
        stats_path = paths.get("Stats", "")
        tbl = read_table(stats_path)
    
    return gs, sel, tv, gear_sum, tbl

# --- Read Song File (Updated to Filter Note Lines) ---
def read_song_file(fp):
    data = {
        "song_details": {
            "Song Name": "",
            "Difficulty": "",
            "Primary Color": "",
            "Secondary Color": "",
            "Last Note Time": "",
            "Total Notes": "",
            "Fever Fill": "",
            "Fever Time": "",
            "Long Notes": ""
        },
        "timestamps": [],
        "notes": [],
        "lanes": [],
        "types": []
    }
    if not fp:
        logging.error("No song file provided.")
        return data
    try:
        with open(fp, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        marker = next((i for i, l in enumerate(lines) if l.strip() == "Song Data"), len(lines))
        # Process header lines for song details
        for l in lines[:marker]:
            parts = l.split("\t", 1)
            if len(parts) == 2 and parts[0] in data["song_details"]:
                data["song_details"][parts[0]] = parts[1].strip() or "0"
        # Filter out empty or non-numeric note lines
        note_lines = [l for l in lines[marker+1:] if l.strip() and re.match(r"^[\d.]", l.strip())]
        if note_lines:
            nd = np.loadtxt(StringIO("\n".join(note_lines)), delimiter="\t")
            if nd.size:
                nd = nd.reshape(1, -1) if nd.ndim == 1 else nd
                if nd.shape[1] == 4:
                    data["timestamps"] = nd[:, 0].tolist()
                    data["notes"] = nd[:, 1].astype(int).tolist()
                    data["lanes"] = nd[:, 2].astype(int).tolist()
                    data["types"] = nd[:, 3].astype(int).tolist()
        return data
    except Exception as e:
        logging.error(f"Error reading song file {fp}: {e}")
        return data

# --- Original Song Lookup ---
def build_song_lookup(sdir):
    lookup = {}
    for root, _, files in os.walk(sdir):
        for f in files:
            if f.lower().endswith(".txt"):
                fp = os.path.join(root, f)
                try:
                    with open(fp, "r", encoding="utf-8-sig") as infile:
                        for _ in range(5):
                            line = infile.readline()
                            if not line:
                                break
                            if line.strip() == "Song Data":
                                break
                            if line.startswith("Song Name"):
                                parts = line.split("\t", 1)
                                if len(parts) == 2:
                                    lookup[sanitize(parts[1].strip().lower())] = fp
                                    break
                except Exception as e:
                    logging.error(f"Error scanning {fp}: {e}")
    return lookup

lookup_song_file = lambda name, song_lookup: song_lookup.get(sanitize(name.lower()))

# === Integrated Calculation Logic ===
TOTAL_ROWS = 160  # maximum row value for lookup

def first_100(combo_mul, base_value):
    rows = np.arange(1, 101)
    scaled_values = base_value + ((combo_mul - 1) * base_value / 100 * rows)
    return scaled_values

def lookup_reference(value, ref_array, total_rows=TOTAL_ROWS):
    clamped = max(0, min(total_rows, int(value)))
    # In the lookup, the value comes from row (total_rows - clamped)
    return ref_array[clamped]

def get_base_value(song, stats, references, total_rows=TOTAL_ROWS):
    metadata = song["metadata"]
    primary_color = metadata.get("Primary Color", "")
    secondary_color = metadata.get("Secondary Color", "")
    base_value = 0
    try:
        base_value += float(stats.get(primary_color, 0)) * 2
    except:
        pass
    try:
        base_value += float(stats.get(secondary_color, 0))
    except:
        pass
    base_value += lookup_reference(stats["Perfect Points"], references["Perfect Points"], total_rows)
    return base_value

def calculate_fever_score(song_data, current_notes, total_notes, real_fever_time, fever_mul, fever_value, first_100_values):
    time = song_data[current_notes]["time"]
    time += real_fever_time
    print("Time increment:", real_fever_time)
    print("End Time:", time)
    if song_data[-1]["time"] < time:
        notes = total_notes - current_notes
    else:
        left, right = current_notes + 1, len(song_data) - 1
        while left < right:
            mid = (left + right) // 2
            if song_data[mid]["time"] > time:
                right = mid
            else:
                left = mid + 1
        notes = left - current_notes
    print("Notes:", notes)
    if current_notes < 100:
        if notes > 100 - current_notes:
            new_score = (
                np.sum(np.floor(first_100_values[current_notes:] * fever_mul))
                + (notes - (100 - current_notes)) * fever_value
            )
        else:
            # Fixed: Apply fever multiplier to all notes in fever
            new_score = np.sum(
                np.floor(first_100_values[current_notes: current_notes + notes] * fever_mul)
            )
    else:
        new_score = notes * fever_value
    return int(new_score), notes

def calculate_non_fever_score(current_notes, total_notes, non_fever, combo_value, first_100_values):
    notes = non_fever
    if current_notes + notes > total_notes:
        notes = total_notes - current_notes
    if current_notes < 100:
        if notes > 100 - current_notes:
            new_score = (
                np.sum(np.floor(first_100_values[current_notes:]))
                + (notes - (100 - current_notes)) * combo_value
            )
        else:
            new_score = np.sum(
                np.floor(first_100_values[current_notes: current_notes + notes])
            )
    else:
        new_score = notes * combo_value
    return int(new_score), notes

def calculate_score(song, stats, references, total_rows=TOTAL_ROWS):
    metadata = song["metadata"]
    song_data = song["song_data"]
    total_notes = int(metadata.get("Total Notes", len(song_data)))
    long_notes = int(metadata.get("Long Notes", 0))
    last_note = float(metadata.get("Last Note Time", song_data[-1]["time"] if song_data else 0))
    
    base_value = get_base_value(song, stats, references, total_rows)
    combo_mul = lookup_reference(stats["Combo Multiplier"], references["Combo Multiplier"], total_rows)
    combo_value = floor(base_value * combo_mul)
    fever_time_factor = lookup_reference(stats["Fever Time"], references["Fever Time"], total_rows)
    fever_fill = lookup_reference(stats["Fever Fill Rate"], references["Fever Fill Rate"], total_rows)
    fever_mul = lookup_reference(stats["Fever Multiplier"], references["Fever Multipler"] if "Fever Multipler" in references else references["Fever Multiplier"], total_rows)
    fever_value = floor(base_value * combo_mul * fever_mul)
    
    first_100_values = first_100(combo_mul, base_value)
    
    non_fever_cas = ceil((total_notes - long_notes) * 0.333)
    non_fever = ceil(non_fever_cas * fever_fill)
    print("non_fever:", non_fever)
    fever_time_cas = round(last_note, 3) * 0.15 + 0.15
    real_fever_time = fever_time_cas * fever_time_factor
    real_fever_time = ceil(real_fever_time * 60) / 60
    print("Real Fever Time:", real_fever_time)
    
    current_notes = 0
    fever = False
    scores = []
    loop = 0
    while current_notes < total_notes:
        if fever:
            new_score, notes = calculate_fever_score(
                song_data,
                current_notes,
                total_notes,
                real_fever_time,
                fever_mul,
                fever_value,
                first_100_values,
            )
            if loop > 1:
                new_score = new_score - combo_value + fever_value
        else:
            new_score, notes = calculate_non_fever_score(
                current_notes, total_notes, non_fever, combo_value, first_100_values
            )
        scores.append(new_score)
        current_notes += notes
        fever = not fever
        loop += 1
        print("Current Combo:", current_notes)
    return scores

# --- Main Execution ---
if __name__ == "__main__":
    try:
        # Load cached paths and config.ini
        paths = load_paths_cache()
        cfg = configparser.ConfigParser()
        cfg.read("config.ini")
        
        # Load gear info and stats table
        gs, sel, tv, gear_sum, stats_table = load_gear_info(cfg, paths)
        stat_labels = ["Perfect Points", "Combo Multiplier", "Fever Multiplier",
                       "Fever Fill Rate", "Fever Time", "Chill", "Flow", "Rush", "Beat", "Vibe"]
        labeled_gear_sum = dict(zip(stat_labels, gear_sum))
        
        print("=== Current Stuff Stats ===\n")
        print("Gear Settings:")
        for key, value in gs.items():
            print(f"  {key}: {value}")
        
        if not cfg.getboolean("InputValues", "ignore_selected_gear_stats", fallback=False):
            print("\nSelected Gear Stats:")
            for key, stats in sel.items():
                print(f"  {key}: {stats}")
        else:
            print("\nSelected Gear Stats: [Ignored per configuration override]")
        
        print("\nTier Colour Values:")
        for tier, values in tv.items():
            print(f"  {tier}: {values}")
        
        print("\nCalculated Gear Sum:")
        for stat, value in labeled_gear_sum.items():
            print(f"  {stat}: {value}")
        
        # Lookup Stats Section
        if stats_table:
            print("\nLookup Stats:")
            for i, stat in enumerate(stat_labels):
                v = labeled_gear_sum[stat]
                clamped = max(0, min(TOTAL_ROWS, int(v)))
                lookup_index = TOTAL_ROWS - clamped
                try:
                    looked_up = stats_table[lookup_index][i]
                except Exception:
                    looked_up = "N/A"
                if looked_up == "N/A":
                    continue
                print(f"  {stat}: {v}-table row. It is -{lookup_index} from top. Looked-up value: {looked_up}")
        else:
            print("\nNo stats table loaded; cannot perform lookup.")
        
        print("\nStats Table:")
        print(f"  Loaded {len(stats_table)} rows.")
        if stats_table:
            print("  Sample (first 5 rows):")
            for row in stats_table[:5]:
                print(f"    {row}")
        
        # --- Song File Loading ---
        song_file = cfg.get("General", "song_file", fallback="").strip()
        if not song_file:
            diff = cfg.get("General", "difficulty", fallback="Hard")
            build_file = paths.get(f"Build_{diff}")
            songs = []
            if build_file and os.path.exists(build_file):
                with open(build_file, "r", newline="") as f:
                    lines = f.readlines()
                if lines:
                    header_line = lines[0]
                    delimiter = "\t" if "\t" in header_line else "|" if "|" in header_line else ","
                    for line in lines[1:]:
                        line = line.strip()
                        if not line:
                            continue
                        cols = [col.strip() for col in line.split(delimiter)]
                        if len(cols) >= 9:
                            record = {"Song Name": cols[1],
                                      "Primary Color": cols[2],
                                      "Secondary Color": cols[3]}
                            songs.append(record)
            diff = cfg.get("General", "difficulty", fallback="Hard")
            sdir = paths.get(diff, SCRIPT_DIR)
            song_lookup = build_song_lookup(sdir)
            
            filter_primary = cfg.get("General", "filter_primary_color", fallback="All Colours").strip().lower()
            filter_secondary = cfg.get("General", "filter_secondary_color", fallback="All Colours").strip().lower()
            filter_search = cfg.get("General", "filter_search_text", fallback="").strip().lower()
            filtered_songs = []
            for song in songs:
                if filter_primary != "all colours" and song.get("Primary Color", "").strip().lower() != filter_primary:
                    continue
                if filter_secondary != "all colours" and song.get("Secondary Color", "").strip().lower() != filter_secondary:
                    continue
                if filter_search and filter_search not in song.get("Song Name", "").strip().lower():
                    continue
                filtered_songs.append(song)
            
            if filtered_songs:
                song_file = lookup_song_file(filtered_songs[0].get("Song Name", ""), song_lookup)
            elif songs:
                song_file = lookup_song_file(songs[0].get("Song Name", ""), song_lookup)
        else:
            if not os.path.isabs(song_file):
                diff = cfg.get("General", "difficulty", fallback="Hard")
                sdir = paths.get(diff, SCRIPT_DIR)
                song_file = os.path.join(sdir, song_file)
        
        if song_file and os.path.exists(song_file):
            song_data = read_song_file(song_file)
            print("\n=== Parsed Song Data ===")
            print("Song Details:")
            for k, v in song_data["song_details"].items():
                print(f"  {k}: {v}")
            last_note_time = song_data["song_details"].get("Last Note Time", "N/A")
            print(f"\nLast Note Time: {last_note_time}")
            print(f"Total Notes Parsed: {len(song_data['notes'])}")
        else:
            print("\nNo valid song_file found; skipping song parsing.")
        
        # --- Integrated Score Calculation ---
        calc_song = {
            "metadata": {
                "Song Name": song_data["song_details"].get("Song Name", ""),
                "Difficulty": song_data["song_details"].get("Difficulty", ""),
                "Primary Color": song_data["song_details"].get("Primary Color", ""),
                "Secondary Color": song_data["song_details"].get("Secondary Color", ""),
                "Last Note Time": song_data["song_details"].get("Last Note Time", "0"),
                "Total Notes": song_data["song_details"].get("Total Notes", str(len(song_data["notes"]))),
                "Long Notes": song_data["song_details"].get("Long Notes", "0"),
            },
            "song_data": [{"time": t} for t in song_data["timestamps"]]
        }
        
        # Build the references lookup arrays for the five key stats.
        stat_names = ["Perfect Points", "Combo Multiplier", "Fever Multiplier", "Fever Fill Rate", "Fever Time"]
        references = {}
        for i, name in enumerate(stat_names):
            references[name] = []
            for v in range(TOTAL_ROWS + 1):
                lookup_index = TOTAL_ROWS - v
                try:
                    references[name].append(stats_table[lookup_index][i])
                except Exception:
                    references[name].append(0)
        
        calc_stats = {
            "Perfect Points": labeled_gear_sum["Perfect Points"],
            "Combo Multiplier": labeled_gear_sum["Combo Multiplier"],
            "Fever Multiplier": labeled_gear_sum["Fever Multiplier"],
            "Fever Fill Rate": labeled_gear_sum["Fever Fill Rate"],
            "Fever Time": labeled_gear_sum["Fever Time"],
            "Chill": labeled_gear_sum.get("Chill", 0),
            "Flow": labeled_gear_sum.get("Flow", 0),
            "Rush": labeled_gear_sum.get("Rush", 0),
            "Beat": labeled_gear_sum.get("Beat", 0),
            "Vibe": labeled_gear_sum.get("Vibe", 0),
        }
        
        scores = calculate_score(calc_song, calc_stats, references, TOTAL_ROWS)
        print("\n=== Calculated Score Blocks ===")
        print(scores)
        total_score = sum(scores)
        print("\nTotal Score:", total_score)
        
    except Exception as e:
        logging.error(f"Error in main execution: {e}")
        print("Error occurred. Check log file for details.")
