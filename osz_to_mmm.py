#!/usr/bin/env python3
"""
OSZ -> Monday Morning Misery chart converter

Supports osu!mania 4-key beatmaps (.osz or .osu).
Writes the JSON structure described in chart_format.txt.

Usage:
  python osz_to_mmm.py "song.osz"
  python osz_to_mmm.py "song.osz" --difficulty 2
  python osz_to_mmm.py "song.osz" --side opponent
  python osz_to_mmm.py "song.osz" --speed 2 --player1 bfhappy --player2 flippy
  python osz_to_mmm.py "map.osu" --output "My_Song.json"

Lane mapping for 4K:
  osu lane 1 -> Left, 2 -> Down, 3 -> Up, 4 -> Right
  player:   0,1,2,3
  opponent: 4,5,6,7

Notes are kept at their absolute millisecond timestamps. Sections are
containers of 16 steps, based on the initial BPM; BPM changes are marked
at the section in which they occur. This matches the supplied target schema.
"""

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path


def parse_osu(path_or_text):
    """Parse the relevant fields from an .osu file."""
    if isinstance(path_or_text, (str, Path)) and os.path.isfile(path_or_text):
        text = Path(path_or_text).read_text(encoding="utf-8-sig", errors="replace")
    else:
        text = str(path_or_text)

    data = {
        "general": {},
        "metadata": {},
        "difficulty": {},
        "timing_points": [],
        "hit_objects": [],
    }

    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue

        if section in ("General", "Metadata", "Difficulty"):
            if ":" in line:
                key, value = line.split(":", 1)
                target = {
                    "General": "general",
                    "Metadata": "metadata",
                    "Difficulty": "difficulty",
                }[section]
                data[target][key.strip()] = value.strip()

        elif section == "TimingPoints":
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    offset = float(parts[0])
                    beat_length = float(parts[1])
                    uninherited = int(parts[6]) if len(parts) > 6 else 1
                    if beat_length > 0 and uninherited == 1:
                        data["timing_points"].append((offset, 60000.0 / beat_length))
                except (ValueError, IndexError):
                    pass

        elif section == "HitObjects":
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                x = int(parts[0])
                time_ms = int(parts[2])
                object_type = int(parts[3])
                hit_sound = int(parts[4])

                # Mania hold note: type bit 128; end time is before the first colon.
                hold_ms = 0
                if object_type & 128 and len(parts) >= 6:
                    end_text = parts[5].split(":", 1)[0]
                    end_time = int(end_text)
                    hold_ms = max(0, end_time - time_ms)

                # Ignore spinners and other non-note objects.
                if object_type & (1 | 128):
                    data["hit_objects"].append({
                        "x": x,
                        "time": time_ms,
                        "hold": hold_ms,
                        "type": object_type,
                        "sound": hit_sound,
                    })
            except (ValueError, IndexError):
                continue

    data["timing_points"].sort(key=lambda p: p[0])
    data["hit_objects"].sort(key=lambda n: n["time"])
    return data


def active_bpm(timing_points, timestamp, fallback):
    bpm = fallback
    for offset, point_bpm in timing_points:
        if offset > timestamp:
            break
        bpm = point_bpm
    return bpm


def convert_chart(parsed, song_name=None, speed=2.0, player1="bfhappy",
                  player2="flippy", side="player", valid_score=True):
    general = parsed["general"]
    metadata = parsed["metadata"]
    difficulty = parsed["difficulty"]
    timing = parsed["timing_points"]

    mode = int(general.get("Mode", "0") or 0)
    if mode != 3:
        raise ValueError(
            f"This beatmap is mode {mode}, not osu!mania (mode 3). "
            "Choose an osu!mania difficulty."
        )

    keys = int(float(difficulty.get("CircleSize", "4") or 4))
    if keys != 4:
        raise ValueError(
            f"This beatmap has {keys} keys. This converter currently supports 4K only."
        )

    if not parsed["hit_objects"]:
        raise ValueError("No mania hit objects were found in this beatmap.")

    # Use the first uninherited timing point, or a safe fallback.
    initial_bpm = timing[0][1] if timing else 120.0
    if initial_bpm <= 0:
        initial_bpm = 120.0

    # osu!mania x coordinates divide the playfield into equal key columns.
    playfield_width = 512.0
    lane_width = playfield_width / keys
    side_offset = 4 if side == "opponent" else 0

    notes = []
    for obj in parsed["hit_objects"]:
        lane = int(obj["x"] / lane_width)
        lane = max(0, min(keys - 1, lane))
        notes.append([
            int(obj["time"]),
            lane + side_offset,
            int(obj["hold"]),
        ])

    notes.sort(key=lambda n: (n[0], n[1]))

    # Create enough sections to contain the final note. Each section is 16 steps.
    # Section timing uses the initial BPM, while absolute note times remain exact.
    section_ms = (60000.0 / (initial_bpm * 4.0)) * 16.0
    last_time = max(n[0] for n in notes)
    section_count = max(1, int(last_time // section_ms) + 1)

    sections = []
    note_index = 0
    for section_index in range(section_count):
        start_ms = section_index * section_ms
        end_ms = (section_index + 1) * section_ms
        section_notes = []

        while note_index < len(notes) and notes[note_index][0] < end_ms:
            if notes[note_index][0] >= start_ms:
                section_notes.append(notes[note_index])
            note_index += 1

        section_bpm = active_bpm(timing, start_ms, initial_bpm)
        previous_bpm = active_bpm(timing, max(0, start_ms - 1), initial_bpm)
        change_bpm = section_index > 0 and abs(section_bpm - previous_bpm) > 0.001

        sections.append({
            "mustHitSection": side == "player",
            "typeOfSection": 0,
            "lengthInSteps": 16,
            "altAnim": False,
            "changeBPM": change_bpm,
            "bpm": round(section_bpm, 6),
            "sectionNotes": section_notes,
        })

    internal_name = song_name or metadata.get("TitleUnicode") or metadata.get("Title") or "Converted_Song"
    internal_name = re.sub(r"[^A-Za-z0-9_ -]", "", internal_name).strip()
    internal_name = re.sub(r"\s+", "_", internal_name) or "Converted_Song"

    return {
        "song": {
            "song": internal_name,
            "bpm": round(initial_bpm, 6),
            "speed": float(speed),
            "player1": player1,
            "player2": player2,
            "needsVoices": False,
            "validScore": bool(valid_score),
            "sections": 0,
            "notes": sections,
        }
    }


def load_beatmaps(input_path):
    path = Path(input_path)
    if path.suffix.lower() == ".osu":
        return [(path.name, path.read_text(encoding="utf-8-sig", errors="replace"))]

    if path.suffix.lower() != ".osz":
        raise ValueError("Input must be an .osz or .osu file.")

    with zipfile.ZipFile(path, "r") as archive:
        osu_names = [
            name for name in archive.namelist()
            if name.lower().endswith(".osu") and not name.startswith("__MACOSX/")
        ]
        if not osu_names:
            raise ValueError("The OSZ archive contains no .osu beatmaps.")

        beatmaps = []
        for name in osu_names:
            raw = archive.read(name)
            text = raw.decode("utf-8-sig", errors="replace")
            beatmaps.append((name, text))
        return beatmaps



def extract_audio(input_path, osu_filename, osu_text, output_dir):
    """Extract the selected beatmap's AudioFilename from an OSZ, or copy sibling audio for .osu."""
    parsed = parse_osu(osu_text)
    audio_name = parsed["general"].get("AudioFilename", "").strip()
    if not audio_name:
        print("WARNING: No AudioFilename is specified; audio was not copied.")
        return None

    audio_rel = audio_name.replace("\\", "/").lstrip("/")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_basename = Path(audio_rel).name
    destination = output_dir / audio_basename

    if Path(input_path).suffix.lower() == ".osz":
        with zipfile.ZipFile(input_path, "r") as archive:
            names = archive.namelist()
            osu_parent = str(Path(osu_filename).parent).replace("\\", "/")
            candidates = []
            if osu_parent and osu_parent != ".":
                candidates.append(f"{osu_parent}/{audio_rel}")
            candidates.append(audio_rel)
            found = next((name for name in candidates if name in names), None)
            if found is None:
                matches = [name for name in names if Path(name).name.lower() == audio_basename.lower()]
                if len(matches) == 1:
                    found = matches[0]
            if found is None:
                print(f"WARNING: Audio file '{audio_name}' was not found in the OSZ.")
                return None
            destination.write_bytes(archive.read(found))
    else:
        source = Path(input_path).parent / audio_rel
        if not source.is_file():
            print(f"WARNING: Audio file '{audio_name}' was not found beside the .osu file.")
            return None
        shutil.copy2(source, destination)

    print(f"Audio extracted: {destination}")
    return str(destination)


def main():
    parser = argparse.ArgumentParser(
        description="Convert osu!mania 4K OSZ charts to Monday Morning Misery JSON."
    )
    parser.add_argument("input", help="Input .osz or .osu file")
    parser.add_argument("-o", "--output", help="Output JSON path")
    parser.add_argument("--difficulty", type=int, default=None,
                        help="1-based beatmap number inside the OSZ")
    parser.add_argument("--side", choices=("player", "opponent"), default="player",
                        help="Map notes to BF lanes (0-3) or opponent lanes (4-7)")
    parser.add_argument("--speed", type=float, default=2.0)
    parser.add_argument("--player1", default="bfhappy")
    parser.add_argument("--player2", default="flippy")
    parser.add_argument("--song-name", default=None)
    parser.add_argument("--audio-output", default=None, help="Folder to save the beatmap audio file")
    parser.add_argument("--invalid-score", action="store_true",
                        help="Set validScore to false")
    args = parser.parse_args()

    try:
        beatmaps = load_beatmaps(args.input)
        print("\\nBeatmaps found:")
        for i, (name, content) in enumerate(beatmaps, 1):
            parsed = parse_osu(content)
            title = parsed["metadata"].get("Version", "Unknown difficulty")
            artist = parsed["metadata"].get("Artist", "Unknown artist")
            print(f"  {i}. {Path(name).name} — {artist} [{title}]")

        if args.difficulty is not None:
            chosen = args.difficulty - 1
            if chosen < 0 or chosen >= len(beatmaps):
                raise ValueError("Difficulty number is out of range.")
        elif len(beatmaps) == 1:
            chosen = 0
        else:
            while True:
                answer = input(f"\\nChoose difficulty (1-{len(beatmaps)}): ").strip()
                try:
                    chosen = int(answer) - 1
                    if 0 <= chosen < len(beatmaps):
                        break
                except ValueError:
                    pass
                print("Invalid selection.")

        filename, content = beatmaps[chosen]
        parsed = parse_osu(content)
        chart = convert_chart(
            parsed,
            song_name=args.song_name,
            speed=args.speed,
            player1=args.player1,
            player2=args.player2,
            side=args.side,
            valid_score=not args.invalid_score,
        )

        output = args.output
        if not output:
            base = Path(args.input).stem
            difficulty_name = parsed["metadata"].get("Version", "")
            safe_diff = re.sub(r"[^A-Za-z0-9_-]+", "_", difficulty_name).strip("_")
            suffix = f"_{safe_diff}" if safe_diff else ""
            output = f"{base}{suffix}_MMM.json"

        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(chart, f, indent=4, ensure_ascii=False)

        if args.audio_output:
            extract_audio(args.input, filename, content, args.audio_output)

        total_notes = sum(len(s["sectionNotes"]) for s in chart["song"]["notes"])
        print(f"\\nConverted successfully: {output}")
        print(f"Song: {chart['song']['song']}")
        print(f"BPM: {chart['song']['bpm']}")
        print(f"Sections: {len(chart['song']['notes'])}")
        print(f"Notes: {total_notes}")
        print(f"Side: {args.side}")

    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
