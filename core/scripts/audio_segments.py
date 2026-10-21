"""Deterministic lyric/pause/beat-aware partitioning, with a strict sample cap."""
from __future__ import annotations

import bisect
import math


def plan_segments(total_samples, sample_rate, phrases, silences, beats, max_seconds=30.0):
    if total_samples <= 0 or sample_rate <= 0 or not 0 < max_seconds <= 30:
        raise ValueError("positive audio size/rate and a maximum in (0, 30] are required")
    cap = int(math.floor(max_seconds * sample_rate))
    if cap < 1:
        raise ValueError("segment cap must contain at least one sample")
    duration = total_samples / sample_rate
    candidates = {0: (0.0, "start"), total_samples: (0.0, "end")}
    rank = {"silence": 0.0, "phrase": 1.5, "word-gap": 2.5, "beat": 4.0, "forced": 9.0}

    def add(t, kind):
        pos = min(total_samples, max(0, round(float(t) * sample_rate)))
        if 0 < pos < total_samples and (pos not in candidates or rank[kind] < candidates[pos][0]):
            candidates[pos] = (rank[kind], kind)

    # A deterministic lattice makes a solution possible even during a long held note.
    step = max(1, min(round(sample_rate * 0.5), cap))
    for pos in range(step, total_samples, step):
        add(pos / sample_rate, "forced")
    words = []
    phrase_windows = []
    for phrase in phrases:
        a, b = float(phrase["start"]), float(phrase["end"])
        phrase_windows.append((a, b))
        add(b, "phrase")
        current = phrase.get("words", [])
        words.extend((float(w["start"]), float(w["end"])) for w in current if w["end"] > w["start"])
        for left, right in zip(current, current[1:]):
            if right["start"] - left["end"] >= 0.08:
                add((right["start"] + left["end"]) / 2, "word-gap")
    for silence in silences:
        a, b = float(silence[0]), float(silence[1])
        if b - a >= 0.12:
            add((a + b) / 2, "silence")
            # Long instrumental passages may need more than one visual segment.
            for sec in range(math.ceil(a), math.floor(b) + 1):
                add(sec, "silence")
    for t in beats:
        add(t, "beat")

    points = sorted(candidates)
    boundary_costs, boundary_flags = [], []
    for pos in points:
        t = pos / sample_rate
        kind = candidates[pos][1]
        inside_word = any(a + 0.025 < t < b - 0.025 for a, b in words)
        inside_phrase = any(a + 0.025 < t < b - 0.025 for a, b in phrase_windows)
        silent = any(a <= t <= b for a, b in silences)
        cost = candidates[pos][0]
        if pos not in (0, total_samples):
            cost += 40 if inside_word else (8 if inside_phrase and not silent else 0)
        boundary_costs.append(cost)
        boundary_flags.append(inside_word or (inside_phrase and not silent) or kind == "forced")

    costs = [math.inf] * len(points)
    previous = [-1] * len(points)
    costs[0] = 0.0
    target = min(26.0, max_seconds)
    for j in range(1, len(points)):
        first = bisect.bisect_left(points, points[j] - cap)
        for i in range(first, j):
            length = (points[j] - points[i]) / sample_rate
            short_cost = 12 * max(0, min(6, max_seconds / 2) - length) if duration > max_seconds else 0
            cost = costs[i] + 3 + ((target - length) / max(target, 1)) ** 2 + short_cost + boundary_costs[j]
            if cost < costs[j]:
                costs[j], previous[j] = cost, i
    if previous[-1] < 0:
        raise ValueError("cannot partition the selected range")
    cuts = [len(points) - 1]
    while cuts[-1] != 0:
        cuts.append(previous[cuts[-1]])
    cuts.reverse()
    result = []
    for index, (i, j) in enumerate(zip(cuts, cuts[1:]), 1):
        start, end = points[i], points[j]
        result.append({"id": f"S{index:03d}", "start_sample": start, "end_sample": end,
                       "start": start / sample_rate, "end": end / sample_rate,
                       "duration": (end - start) / sample_rate,
                       "cut_reason": candidates[end][1],
                       "boundary_review": bool(boundary_flags[j]) if end < total_samples else False})
    return result


def segment_lyrics(segment, phrases):
    """Return occurrence-aware fragments; never restart a cross-boundary syllable."""
    start, end = segment["start"], segment["end"]
    result = []
    for phrase in phrases:
        a, b = phrase["start"], phrase["end"]
        if a >= end or b <= start:
            continue
        words = [w for w in phrase.get("words", []) if w["start"] < end and w["end"] > start]
        partial = a < start or b > end
        text = "".join(w["word"] for w in words).strip() if words else (phrase["text"] if not partial else None)
        result.append({"occurrence_id": phrase["id"], "text": text,
                       "full_phrase_context": phrase["text"],
                       "source_start": max(a, start), "source_end": min(b, end),
                       "start": max(a, start) - start, "end": min(b, end) - start,
                       "words": [{"word": w["word"], "start": max(w["start"], start) - start,
                                  "end": min(w["end"], end) - start,
                                  "source_start": w["start"], "source_end": w["end"],
                                  "probability": w.get("probability")} for w in words],
                       "continuation_from_previous": a < start, "continues_to_next": b > end,
                       "crosses_word": any(w["start"] < start < w["end"] or w["start"] < end < w["end"] for w in words),
                       "evidence": "ASR candidate", "flags": phrase.get("flags", [])})
    return result
