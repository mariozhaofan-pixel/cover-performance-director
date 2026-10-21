"""Build standalone + both director editions and verify byte-identical cover cores."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from sync_director import canonical_files, sync

PACKAGE = "cover-performance-director"
LINK = re.compile(r"\[[^\]\n]+\]\(([^)\n]+)\)")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def collect(source):
    files = {}
    for path in sorted(source.rglob("*")):
        name = path.relative_to(source).as_posix()
        if path.is_file() and (name in ("SKILL.md", "INSTALL.md") or
                              name.startswith(("agents/", "core/", "references/"))):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError(f"linked source file: {name}")
            files[name] = path.read_bytes()
    for name, data in files.items():
        if name.endswith((".md", ".yaml", ".py")):
            text = data.decode("utf-8-sig")
            if "\ufffd" in text:
                raise ValueError(f"encoding damage: {name}")
            if name.endswith(".md"):
                for link in LINK.findall(text):
                    if link.startswith("#") or urlsplit(link).scheme:
                        continue
                    target = ((source / name).parent / unquote(link.split("#", 1)[0])).resolve()
                    if not target.is_relative_to(source) or target.relative_to(source).as_posix() not in files:
                        raise ValueError(f"unbundled link: {name} -> {link}")
    if [name for name in files if PurePosixPath(name).name == "SKILL.md"] != ["SKILL.md"]:
        raise ValueError("standalone must have exactly one root skill")
    return files


def package_standalone(source, stage, version):
    files = collect(source)
    manifest = {"package": PACKAGE, "version": version, "path_base": "package-root",
                "files": [{"path": k, "bytes": len(v), "sha256": sha(v)} for k, v in files.items()]}
    all_files = dict(files)
    all_files["package-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path = stage / f"{PACKAGE}-{version}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in all_files.items():
            entry = zipfile.ZipInfo(f"{PACKAGE}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("standalone zip CRC failure")
        if set(archive.namelist()) != {f"{PACKAGE}/{name}" for name in all_files}:
            raise ValueError("standalone archive inventory mismatch")
        for name, data in all_files.items():
            if archive.read(f"{PACKAGE}/{name}") != data:
                raise ValueError(f"standalone archive content mismatch: {name}")
    (stage / (path.name + ".sha256")).write_text(f"{sha(path.read_bytes())}  {path.name}\n", encoding="utf-8")
    return path, files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--director", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--director-version", required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    director, output = args.director.resolve(), args.output_dir.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", args.version):
        raise ValueError("invalid standalone release identifier")
    if output.is_relative_to(source) or output.is_relative_to(director):
        raise ValueError("release must be outside both source trees")
    record = sync(source, director, check=True)
    spec = importlib.util.spec_from_file_location("director_builder", director / "scripts/build_editions.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory(prefix="cover-release-") as temporary:
        stage = Path(temporary)
        standalone_zip, snapshot = package_standalone(source, stage, args.version)
        director_proof = builder.build(director, stage, args.director_version)
        core = canonical_files(source)
        for edition in director_proof["editions"]:
            with zipfile.ZipFile(stage / edition["archive"]) as archive:
                for name, data in core.items():
                    if archive.read(f"director-seedance-prompt/capabilities/cover-performance/{name}") != data:
                        raise ValueError(f"core mismatch in {edition['edition']}: {name}")
        if collect(source) != snapshot:
            raise ValueError("standalone source changed during build")
        proof = {
            "standalone_archive": standalone_zip.name,
            "director_archives": [item["archive"] for item in director_proof["editions"]],
            "shared_core": record,
            "byte_identical_in_all_three_archives": True,
            "source_links_and_archive_hashes_verified": True,
            "media_generation_tested": False,
        }
        (stage / f"cover-director-{args.director_version}-proof.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        names = sorted(p.name for p in stage.iterdir())
        output.mkdir(parents=True, exist_ok=True)
        if any((output / name).exists() for name in names):
            raise ValueError("release artifact already exists; choose a new version")
        for name in names:
            with (output / name).open("xb") as dest, (stage / name).open("rb") as origin:
                shutil.copyfileobj(origin, dest)
        print(json.dumps({"output": str(output), "files": names, "shared_core_files": len(core)},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
