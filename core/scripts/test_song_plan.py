import copy
import unittest

from check_song_plan import check_plan


def sample():
    return {
        "audio": {"id": "synthetic-final-v1", "role": "final", "duration": 12},
        "offset": 1, "video_duration": 14, "max_segment_duration": 8,
        "require_verified_cues": True, "require_final_track": True,
        "lyrics": [{"id": "L1", "text": "风经过窗前"}, {"id": "L2", "text": "风经过窗前"}],
        "cues": [{"lyric_id": "L1", "start": 2, "end": 5, "evidence": "user-confirmed"},
                 {"lyric_id": "L2", "start": 7, "end": 10, "evidence": "audited"}],
        "segments": [{"id": "S1", "video_start": 0, "video_end": 7},
                     {"id": "S2", "video_start": 7, "video_end": 14}],
    }


class SongPlanTests(unittest.TestCase):
    def test_repeated_lyrics_and_intro_outro_mapping(self):
        result = check_plan(sample())
        self.assertTrue(result["arithmetic_ok"])
        self.assertEqual(result["mapped_cues"][1]["video"], [8, 11])
        self.assertEqual(result["segment_cues"][1]["cues"][0]["retained_local"], [1, 4])
        self.assertEqual(result["additional_outro"], 1)
        self.assertEqual(result["last_vocal_end_video"], 11)
        self.assertEqual(result["repeated_text_occurrences"]["风经过窗前"], 2)

    def test_negative_offset_cannot_drop_intro(self):
        plan = sample()
        plan["offset"] = -1
        self.assertFalse(check_plan(plan)["arithmetic_ok"])

    def test_explicit_selected_range_allows_onset_alignment(self):
        plan = sample()
        plan["source_range"] = [1, 12]
        plan["offset"] = -1
        result = check_plan(plan)
        self.assertTrue(result["arithmetic_ok"])
        self.assertEqual(result["mapped_cues"][0]["video"][0], 1)

    def test_reference_not_final_track(self):
        plan = sample()
        plan["audio"]["role"] = "reference"
        self.assertFalse(check_plan(plan)["arithmetic_ok"])

    def test_candidate_not_promoted_by_offset(self):
        plan = sample()
        plan["cues"][0]["evidence"] = "candidate"
        self.assertFalse(check_plan(plan)["arithmetic_ok"])
        plan["require_verified_cues"] = False
        result = check_plan(plan)
        self.assertTrue(result["arithmetic_ok"])
        self.assertEqual(result["declared_timing_evidence"], "candidate")

    def test_audio_cannot_be_truncated_by_video(self):
        plan = sample()
        plan["video_duration"] = 10
        self.assertFalse(check_plan(plan)["arithmetic_ok"])

    def test_repeat_cannot_be_deduplicated(self):
        plan = sample()
        plan["cues"].pop()
        self.assertFalse(check_plan(plan)["arithmetic_ok"])

    def test_retained_segments_no_gap_or_overlap(self):
        for start in (6.5, 7.5):
            with self.subTest(start=start):
                plan = sample()
                plan["segments"][1]["video_start"] = start
                self.assertFalse(check_plan(plan)["arithmetic_ok"])

    def test_generation_overlap_checks_full_generated_length(self):
        plan = sample()
        plan["segments"][1]["generate_start"] = 6
        self.assertTrue(check_plan(plan)["arithmetic_ok"])
        plan["segments"][1]["generate_start"] = 5
        self.assertFalse(check_plan(plan)["arithmetic_ok"])

    def test_cross_phrase_split_warns(self):
        plan = sample()
        plan["segments"][0]["video_end"] = 5
        plan["segments"][1]["video_start"] = 5
        plan["max_segment_duration"] = 10
        result = check_plan(plan)
        self.assertTrue(result["arithmetic_ok"])
        self.assertTrue(result["warnings"])

    def test_pure_instrumental_no_lyrics(self):
        plan = sample()
        plan["lyrics"], plan["cues"] = [], []
        result = check_plan(plan)
        self.assertTrue(result["arithmetic_ok"])
        self.assertIsNone(result["last_vocal_end_video"])

    def test_bad_numbers_and_structure(self):
        for value in (True, "12", float("nan"), float("inf"), None):
            with self.subTest(value=value):
                plan = sample()
                plan["audio"]["duration"] = value
                self.assertFalse(check_plan(plan)["arithmetic_ok"])
        self.assertFalse(check_plan([])["arithmetic_ok"])

    def test_input_is_not_modified(self):
        plan = sample()
        original = copy.deepcopy(plan)
        check_plan(plan)
        self.assertEqual(plan, original)


if __name__ == "__main__":
    unittest.main()
