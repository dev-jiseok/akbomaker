"""Explicitly download one pinned Apache-2.0 YourMT3+ challenger (~563 MB).

Only author-published source and weights are fetched. No credentials or user
audio are sent. Run in the main Python environment, then install the separate
requirements-yourmt3.txt into an isolated worker environment.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import ssl
import sys
import urllib.request

import certifi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.yourmt3_challenger import (
    CHECKPOINT_RELATIVE, CHECKPOINT_SHA256, REVISION, SOURCE_MANIFEST_SHA256, verify_runtime,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / ".data/yourmt3-runtime")
    args = parser.parse_args(argv)
    if (args.runtime / "manifest.json").exists():
        verify_runtime(args.runtime)
        print("Pinned YourMT3 runtime already present and verified; no downloads")
        return
    context = ssl.create_default_context(cafile=certifi.where())
    base = f"https://huggingface.co/spaces/mimbres/YourMT3/resolve/{REVISION}/"
    with urllib.request.urlopen(
        f"https://huggingface.co/api/spaces/mimbres/YourMT3/tree/{REVISION}?recursive=true&limit=1000",
        timeout=30, context=context,
    ) as stream:
        files = json.load(stream)
    selected = [item for item in files if item["type"] == "file" and
                ((item["path"].startswith("amt/src/") and item["path"].endswith(".py") and "/tests/" not in item["path"])
                 or item["path"] in {"README.md", "model_helper.py", CHECKPOINT_RELATIVE})]
    if len(selected) != 85 or sum(item["size"] for item in selected) > 600_000_000:
        raise ValueError("Unexpected author snapshot file list or size")
    source = args.runtime / "source"

    def download(item):
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe remote file path")
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("Runtime destination escapes source folder")
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".download")
            # Never replace a file already present, even after interrupted work.
            with urllib.request.urlopen(base + item["path"], timeout=120, context=context) as stream, temporary.open("xb") as output:
                total = 0
                while block := stream.read(1024 * 1024):
                    total += len(block)
                    if total > item["size"]:
                        raise ValueError("Download exceeds declared size")
                    output.write(block)
            if path.exists():
                raise FileExistsError(path)
            temporary.rename(path)
        digest = hashlib.sha256()
        git_digest = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
                git_digest.update(block)
        if path.stat().st_size != item["size"]:
            raise ValueError(f"Downloaded size mismatch: {relative}")
        if item["path"] == CHECKPOINT_RELATIVE:
            if digest.hexdigest() != CHECKPOINT_SHA256:
                raise ValueError("Checkpoint SHA256 differs from pinned author weights")
        elif "lfs" in item or git_digest.hexdigest() != item["oid"]:
            raise ValueError(f"Author source Git blob hash mismatch: {relative}")
        print(f"Verified {relative}", flush=True)
        return {"path": item["path"], "sha256": digest.hexdigest(), "bytes": path.stat().st_size}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(download, selected))
    canonical = json.dumps(sorted(results, key=lambda item: item["path"]), sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != SOURCE_MANIFEST_SHA256:
        raise ValueError("Downloaded source list differs from pinned author snapshot")
    manifest = {"source": "https://huggingface.co/spaces/mimbres/YourMT3", "revision": REVISION,
                "license": "Apache-2.0", "checkpoint": CHECKPOINT_RELATIVE,
                "checkpoint_sha256": CHECKPOINT_SHA256, "files": results}
    with (args.runtime / "manifest.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
    verify_runtime(args.runtime)
    print(f"Verified {len(results)} files, {sum(item['bytes'] for item in results)} bytes")


if __name__ == "__main__":
    main()
