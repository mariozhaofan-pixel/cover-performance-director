import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from audio_segments import plan_segments, segment_lyrics
import prepare_song
from prepare_song import digest, reuse_audio, stages, write_json


class SegmentationTests(unittest.TestCase):
    def assert_partition(self, segments, samples, rate, cap=30):
        self.assertEqual(segments[0]["start_sample"], 0)
        self.assertEqual(segments[-1]["end_sample"], samples)
        for i, part in enumerate(segments):
            self.assertGreater(part["duration"], 0)
            self.assertLessEqual(part["end_sample"] - part["start_sample"], int(rate * cap))
            if i:
                self.assertEqual(part["start_sample"], segments[i - 1]["end_sample"])

    def test_short_audio_stays_complete(self):
        result = plan_segments(12345, 1000, [], [], [], 30)
        self.assertEqual(len(result), 1)
        self.assert_partition(result, 12345, 1000)

    def test_hard_cap_for_long_held_vowel(self):
        phrases = [{"id": "L1", "start": 0, "end": 71.123, "text": "啊", "words": [{"word": "啊", "start": 0, "end": 71.123}]}]
        result = plan_segments(71123, 1000, phrases, [], [t for t in range(1, 71)])
        self.assert_partition(result, 71123, 1000)
        self.assertTrue(any(p["boundary_review"] for p in result))

    def test_prefers_real_pauses_over_equal_thirty_second_slices(self):
        result = plan_segments(70000, 1000, [], [[23, 24], [46, 47]], [t for t in range(70)])
        self.assert_partition(result, 70000, 1000)
        self.assertTrue(23 <= result[0]["end"] <= 24)
        self.assertTrue(46 <= result[1]["end"] <= 47)

    def test_short_tail_does_not_create_padding_or_lose_samples(self):
        for samples in (30001, 60001, 61573, 301234):
            with self.subTest(samples=samples):
                result = plan_segments(samples, 1000, [], [], [])
                self.assert_partition(result, samples, 1000)

    def test_smaller_model_cap(self):
        result = plan_segments(41123, 1000, [], [[13, 14], [27, 28]], [], 15)
        self.assert_partition(result, 41123, 1000, 15)

    def test_invalid_caps(self):
        for cap in (0, -1, 30.1):
            with self.assertRaises(ValueError):
                plan_segments(1000, 1000, [], [], [], cap)

    def test_repeated_text_preserves_occurrences(self):
        phrases = [{"id": "L1", "start": 1, "end": 3, "text": "又见晴天", "words": []},
                   {"id": "L2", "start": 8, "end": 10, "text": "又见晴天", "words": []}]
        items = segment_lyrics({"start": 0, "end": 12}, phrases)
        self.assertEqual([p["occurrence_id"] for p in items], ["L1", "L2"])
        self.assertEqual([p["text"] for p in items], ["又见晴天", "又见晴天"])

    def test_cross_boundary_syllable_is_continuation(self):
        phrases = [{"id": "L1", "start": 28, "end": 34, "text": "晴天", "words": [
            {"word": "晴", "start": 28, "end": 29}, {"word": "天", "start": 29, "end": 34}]}]
        left = segment_lyrics({"start": 0, "end": 30}, phrases)[0]
        right = segment_lyrics({"start": 30, "end": 40}, phrases)[0]
        self.assertEqual(left["text"], "晴天")
        self.assertEqual(right["text"], "天")
        self.assertTrue(right["continuation_from_previous"])
        self.assertTrue(right["crosses_word"])
        self.assertEqual(right["words"][0]["start"], 0)
        self.assertEqual(right["words"][0]["end"], 4)
        self.assertEqual(right["source_start"], 30)

    def test_missing_word_alignment_does_not_invent_fragment(self):
        phrases = [{"id": "L1", "start": 28, "end": 34, "text": "风经过窗前", "words": []}]
        item = segment_lyrics({"start": 30, "end": 40}, phrases)[0]
        self.assertIsNone(item["text"])
        self.assertEqual(item["full_phrase_context"], "风经过窗前")

    def test_no_input_mutation(self):
        phrases = [{"start": 2, "end": 5, "text": "测试", "words": []}]
        saved = copy.deepcopy(phrases)
        plan_segments(40000, 1000, phrases, [[6, 7]], [5])
        self.assertEqual(phrases, saved)


class StageTests(unittest.TestCase):
    def prepared_fixture(self, base):
        donor, target = base / "donor", base / "target"
        donor.mkdir(); target.mkdir()
        config = {"pipeline_version": "1.1.0", "separator_model": "fixture", "audio_separator_version": "test"}
        manifest = {"input_sha256": "same-source", "config": config, "stages": {}}
        for stage, names in {"normalize": ["source.wav", "audio-info.json"],
                             "separate": ["vocals.wav", "instrumental.wav", "separation.json"]}.items():
            rows = []
            for name in names:
                (donor/name).write_text(name, encoding="utf-8")
                rows.append({"path": name, "sha256": digest(donor/name)})
            manifest["stages"][stage] = {"outputs": rows}
        write_json(donor/"manifest.json", manifest)
        return donor, target, {"input_sha256": "same-source", "config": config.copy(), "stages": {}}

    def test_reuse_stems_is_verified_and_copy_is_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            donor, target, manifest = self.prepared_fixture(Path(temp))
            reuse_audio(donor, target, manifest)
            self.assertEqual(set(manifest["stages"]), {"normalize", "separate"})
            self.assertEqual((donor/"vocals.wav").read_bytes(), (target/"vocals.wav").read_bytes())
            (target/"vocals.wav").write_bytes(b"changed")
            self.assertEqual((donor/"vocals.wav").read_bytes(), b"vocals.wav")

    def test_reuse_rejects_other_source_or_separator(self):
        with tempfile.TemporaryDirectory() as temp:
            donor, target, manifest = self.prepared_fixture(Path(temp))
            manifest["input_sha256"] = "different-source"
            with self.assertRaises(ValueError):
                reuse_audio(donor, target, manifest)
            manifest["input_sha256"] = "same-source"
            manifest["config"]["separator_model"] = "different-model"
            with self.assertRaises(ValueError):
                reuse_audio(donor, target, manifest)
            self.assertFalse(list(target.iterdir()))

    def test_reuse_checks_all_hashes_before_copying(self):
        with tempfile.TemporaryDirectory() as temp:
            donor, target, manifest = self.prepared_fixture(Path(temp))
            (donor/"vocals.wav").write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                reuse_audio(donor, target, manifest)
            self.assertFalse(list(target.iterdir()))

    def test_malformed_manifest_does_not_leave_processing_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "input.wav"
            source.write_bytes(b"test input")
            args = SimpleNamespace(audio=source, output=base / "out", max_seconds=30, threads=1,
                separator_model="fixture", asr_model="small", language="en")
            with mock.patch.object(prepare_song, "ensure_runtime"), mock.patch.object(prepare_song.importlib.metadata, "version", return_value="test"), mock.patch.object(prepare_song, "normalize", side_effect=RuntimeError("expected fixture failure")):
                with self.assertRaises(RuntimeError):
                    prepare_song.run(args)
                root = next(args.output.iterdir())
                (root / "manifest.json").write_text("invalid json", encoding="utf-8")
                with self.assertRaises(json.JSONDecodeError):
                    prepare_song.run(args)
                self.assertFalse((root / ".processing.lock").exists())

    def test_cache_reuse_and_corruption_invalidates_downstream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {"stages": {}}
            calls = []
            def action():
                calls.append(1)
                (root / "source.wav").write_bytes(b"audio fixture")
                return [root / "source.wav"]
            execute = stages(manifest, root)
            execute("normalize", action)
            execute("normalize", action)
            self.assertEqual(len(calls), 1)
            manifest["stages"]["separate"] = {"outputs": []}
            (root / "source.wav").write_bytes(b"corrupt")
            execute("normalize", action)
            self.assertEqual(len(calls), 2)
            self.assertNotIn("separate", manifest["stages"])

    def test_atomic_json_is_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "nested/state.json"
            write_json(target, {"歌词": "风经过窗前"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"歌词": "风经过窗前"})
            self.assertFalse(target.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
