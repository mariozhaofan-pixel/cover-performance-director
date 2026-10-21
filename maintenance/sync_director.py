"""Synchronize the canonical core into a director source tree, or check equality."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_files(root):
    result = {}
    for path in sorted((root / "core").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            if path.is_symlink():
                raise ValueError(f"linked core file: {path.name}")
            result[path.relative_to(root / "core").as_posix()] = path.read_bytes()
    if "COVER_PERFORMANCE.md" not in result:
        raise ValueError("canonical cover core is missing")
    return result


def sync(source, director, check=False):
    source, director = source.resolve(), director.resolve()
    if not (director / "SKILL.md").is_file():
        raise ValueError("director must be an existing source skill")
    files = canonical_files(source)
    target = director / "capabilities" / "cover-performance"
    for part in (director / "capabilities", target):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("linked destination is unsupported")
    version = re.search(r"^core-version:\s*(\S+)", files["COVER_PERFORMANCE.md"].decode("utf-8-sig"), re.M)
    if not version:
        raise ValueError("canonical core version is missing")
    record = {
        "source_skill": "cover-performance-director", "core_version": version.group(1),
        "files": [{"path": k, "bytes": len(v), "sha256": digest(v)} for k, v in files.items()],
    }
    files["shared-core.json"] = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()
              and "__pycache__" not in p.parts and p.suffix != ".pyc"} if target.exists() else set()
    if actual - set(files):
        raise ValueError(f"unexpected destination files; review before removing: {sorted(actual - set(files))}")
    for name, data in files.items():
        destination = target / name
        if not destination.resolve().is_relative_to(target.resolve()):
            raise ValueError("destination escapes core")
        for part in [destination, *destination.parents]:
            if part == target:
                break
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise ValueError("linked destination file path is unsupported")
        if check:
            if not destination.is_file() or destination.read_bytes() != data:
                raise ValueError(f"shared core differs: {name}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or destination.read_bytes() != data:
                destination.write_bytes(data)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--director", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    record = sync(Path(__file__).resolve().parents[1], args.director, args.check)
    print(json.dumps({"mode": "check" if args.check else "sync", "core_files": len(record["files"]),
                      "core_version": record["core_version"]}))


if __name__ == "__main__":
    main()
