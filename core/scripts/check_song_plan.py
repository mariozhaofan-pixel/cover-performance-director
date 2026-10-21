"""Read-only arithmetic checks for a declared song plan; no audio analysis."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

EPS = 1e-6


def check_plan(plan: dict) -> dict:
    errors, warnings, mapped = [], [], []

    def need(condition, message):
        if not condition:
            errors.append(message)

    def number(value, label):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{label}: finite number required")
        return float(value)

    def nonempty(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label}: nonempty string required")
        return value

    def array(value, label):
        if not isinstance(value, list):
            raise ValueError(f"{label}: array required")
        return value

    report = {
        "arithmetic_ok": False, "errors": errors, "warnings": warnings,
        "not_verified": ["audio contents", "lyric correctness", "evidence authenticity",
                         "pitch", "generated lip sync", "platform capability"],
    }
    try:
        audio = plan["audio"]
        nonempty(audio["id"], "audio.id")
        duration = number(audio["duration"], "audio.duration")
        need(duration > 0, "audio.duration must be positive")
        role = audio["role"]
        need(role in ("reference", "final"), "audio.role must be reference or final")
        selected = array(plan.get("source_range", [0, duration]), "source_range")
        if len(selected) != 2:
            raise ValueError("source_range must have two values")
        start, end = (number(v, "source_range") for v in selected)
        need(0 <= start < end <= duration, "source_range must be inside audio duration")
        offset = number(plan.get("offset", 0), "offset")
        total = number(plan["video_duration"], "video_duration")
        need(total > 0, "video_duration must be positive")
        need(start + offset >= -EPS, "selected audio starts before video zero; resolve intro/range conflict")
        need(end + offset <= total + EPS, "video ends before selected audio finishes")
        max_duration = plan.get("max_segment_duration")
        if max_duration is not None:
            max_duration = number(max_duration, "max_segment_duration")
            need(max_duration > 0, "max_segment_duration must be positive")
        lyrics = array(plan["lyrics"], "lyrics")
        ids = [nonempty(row["id"], "lyrics.id") for row in lyrics]
        texts = [nonempty(row["text"], "lyrics.text") for row in lyrics]
        need(len(set(ids)) == len(ids), "lyric occurrence ids must be unique, including repeated text")
        cues = array(plan["cues"], "cues")
        cue_ids = [nonempty(row["lyric_id"], "cues.lyric_id") for row in cues]
        need(cue_ids == ids, "cues must cover every lyric occurrence once, in declared order")
        lyric_map = dict(zip(ids, texts))
        evidence = []
        cue_ranges = []
        for row in cues:
            cid = row["lyric_id"]
            a, b = number(row["start"], f"{cid}.start"), number(row["end"], f"{cid}.end")
            need(start - EPS <= a < b <= end + EPS, f"{cid}: cue outside selected range or invalid duration")
            if cue_ranges:
                need(a + EPS >= cue_ranges[-1][0], f"{cid}: cues not ordered by onset")
            cue_ranges.append((a, b, cid))
            source = row.get("evidence", "candidate")
            need(source in ("audited", "user-confirmed", "candidate"), f"{cid}: invalid evidence class")
            evidence.append(source)
            mapped.append({"id": cid, "text": lyric_map.get(cid),
                           "source": [a, b], "video": [a + offset, b + offset],
                           "declared_evidence": source})
        verified_claim = bool(cues) and all(e in ("audited", "user-confirmed") for e in evidence)
        for flag in ("require_verified_cues", "require_final_track"):
            need(type(plan.get(flag, False)) is bool, f"{flag}: boolean required")
        if plan.get("require_verified_cues", False) and cues:
            need(verified_claim, "strict cue timing requested but candidate evidence remains")
        if plan.get("require_final_track", False):
            need(role == "final", "final-track timing requested but source is only a song reference")
        if "candidate" in evidence:
            warnings.append("Candidate timings remain candidates after arithmetic or offset conversion.")
        segments = array(plan["segments"], "segments")
        need(bool(segments), "at least one retained video segment is required")
        segment_ids = [nonempty(row["id"], "segments.id") for row in segments]
        need(len(set(segment_ids)) == len(segment_ids), "segment ids must be unique")
        previous_end = 0.0
        local_cues = []
        for row in segments:
            sid = row["id"]
            a = number(row["video_start"], f"{sid}.video_start")
            b = number(row["video_end"], f"{sid}.video_end")
            need(0 <= a < b <= total + EPS, f"{sid}: invalid retained video interval")
            need(abs(a - previous_end) <= EPS, f"{sid}: retained segments have a gap, overlap or wrong order")
            ga = number(row.get("generate_start", a), f"{sid}.generate_start")
            gb = number(row.get("generate_end", b), f"{sid}.generate_end")
            need(0 <= ga <= a < b <= gb <= total + EPS, f"{sid}: generation context must contain retained interval")
            if max_duration is not None:
                need(gb - ga <= max_duration + EPS, f"{sid}: generated interval exceeds declared model limit")
            overlaps = []
            for ca, cb, cid in cue_ranges:
                va, vb = ca + offset, cb + offset
                if va < b - EPS and vb > a + EPS:
                    overlaps.append({"id": cid, "retained_local": [max(va, a) - a, min(vb, b) - a],
                                     "generation_local": [max(va, ga) - ga, min(vb, gb) - ga]})
                if a + EPS < vb and va + EPS < a:
                    warnings.append(f"{sid} begins inside {cid}; preserve ongoing syllable/long note, do not restart lyric.")
            local_cues.append({"segment": sid, "cues": overlaps})
            previous_end = b
        need(abs(previous_end - total) <= EPS, "retained segments do not cover the complete video duration")
        report.update({
            "audio_role": role,
            "declared_timing_evidence": "no-lyrics" if not cues else ("verified-claim" if verified_claim else "candidate"),
            "selected_audio_video_window": [start + offset, end + offset],
            "additional_intro": start + offset,
            "additional_outro": total - end - offset,
            "last_vocal_end_video": max((row[1] for row in cue_ranges), default=None),
            "repeated_text_occurrences": {t: n for t, n in Counter(texts).items() if n > 1},
            "mapped_cues": mapped, "segment_cues": local_cues,
        })
        if report["last_vocal_end_video"] is not None:
            report["last_vocal_end_video"] += offset
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"Invalid plan: {exc}")
    report["arithmetic_ok"] = not errors
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        report = check_plan(json.loads(args.plan.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as exc:
        report = {"arithmetic_ok": False, "errors": [str(exc)]}
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["arithmetic_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
