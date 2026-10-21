"""Install an isolated, reproducible CPU audio runtime. No song is uploaded."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

# audio-separator imports audioread directly; librosa 1.x no longer installs it transitively.
PACKAGES = ["audio-separator[cpu]==0.47.1", "faster-whisper==1.2.1", "imageio-ffmpeg==0.6.0", "audioread==3.1.0"]


def runtime_root():
    return Path(os.environ.get("COVER_AUDIO_RUNTIME", Path.home() / ".codex/runtimes/cover-audio")).expanduser()


def runtime_python(root=None):
    root = Path(root or runtime_root())
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def check(root=None):
    executable = runtime_python(root)
    if not executable.is_file():
        return False
    result = subprocess.run([str(executable), "-c", "from audio_separator.separator import Separator; from faster_whisper import WhisperModel; import soundfile, librosa, numpy, onnxruntime"], capture_output=True)
    return result.returncode == 0


def install(root=None):
    root = Path(root or runtime_root()).resolve()
    if not (root / "pyvenv.cfg").exists():
        if root.exists() and any(root.iterdir()):
            raise RuntimeError("runtime target is not an empty directory or an existing venv")
        venv.EnvBuilder(with_pip=True).create(root)
    executable = runtime_python(root)
    # Keep CUDA driver/toolkit and the user's global Python environment unchanged.
    commands = [
        [str(executable), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--timeout", "60", "--retries", "2",
         "torch==2.8.0", "torchvision==0.23.0", "--index-url", "https://download.pytorch.org/whl/cpu"],
        [str(executable), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--timeout", "60", "--retries", "2", *PACKAGES],
    ]
    for command in commands:
        subprocess.run(command, check=True)
    if not check(root):
        raise RuntimeError("runtime imports failed after installation")
    frozen = subprocess.check_output([str(executable), "-m", "pip", "freeze"], text=True, encoding="utf-8")
    (root / "installed-requirements.txt").write_text(frozen, encoding="utf-8")
    return executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    if args.check:
        ready = check(args.runtime)
        print(json.dumps({"ready": ready, "python": str(runtime_python(args.runtime))}))
        return 0 if ready else 1
    print(install(args.runtime))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
