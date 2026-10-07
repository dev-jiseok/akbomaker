"""Explicit, pinned download for local ADT_STR evaluation (never run by the API).

Upstream code is CC-BY-SA-4.0. The HF model card has no separate weight license;
confirm deployment/distribution terms with the author before shipping the weights.
Only configs and safetensors are downloaded; no remote model code is executed.
"""
import argparse
import hashlib
import json
from pathlib import Path

REPO = "Pierfrancesco/adt-str"
REVISION = "a33c5c6b191a4ca1e0f6dc22140947485eb36ce8"
SOURCE_REVISION = "77dbef225e7029478ebfb916f20c7a00274f0f12"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".data/drum-runtime/model"))
    parser.add_argument("--variant", choices=["setting-tau-0.4", "setting-tau-0.6", "setting-tau-0.8"], default="setting-tau-0.8")
    args = parser.parse_args()
    from huggingface_hub import snapshot_download
    snapshot_download(REPO, revision=REVISION, local_dir=args.output,
                      allow_patterns=["README.md", f"{args.variant}/adt_config.yaml", f"{args.variant}/config.json", f"{args.variant}/model.safetensors"])
    model_dir = args.output / args.variant
    manifest = {"repo": REPO, "revision": REVISION, "variant": args.variant,
                "source_repo": "https://github.com/pier-maker92/ADT_STR", "source_revision": SOURCE_REVISION,
                "weight_license": "not specified in model card; deployment review required", "sha256": {}}
    for name in ("adt_config.yaml", "config.json", "model.safetensors"):
        with (model_dir / name).open("rb") as stream:
            manifest["sha256"][name] = hashlib.file_digest(stream, "sha256").hexdigest()
    (model_dir / "akbo-model.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"model_dir": str(model_dir.resolve()), **manifest}, indent=2))


if __name__ == "__main__":
    main()
