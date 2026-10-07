"""Explicitly download pinned STRUM research assets; never called by the app.

Source README and HF model card label MIT. This fetches only V14 + V12c,
not the full game-chart system, Demucs, datasets, or any user audio. Company
deployment still needs normal license/dependency review. Inference is offline.
"""
import argparse
import json
from pathlib import Path
import ssl
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.drum_challenger import SOURCE_REVISION, MODEL_REVISION, SOURCE_FILES, WEIGHTS, sha256


def download(url, path, expected=None, size=None):
    if path.exists():
        if expected and sha256(path) != expected:
            raise ValueError(f'Existing asset checksum mismatch; refusing to overwrite {path.name}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + '.partial')
    # Certifi is optional: never disable certificate verification.
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    with urllib.request.urlopen(url, context=context, timeout=120) as source, partial.open('xb') as target:
        count = 0
        while block := source.read(1024*1024):
            count += len(block)
            if count > (size if size is not None else 1024*1024):
                raise ValueError('Download exceeded pinned size bound')
            target.write(block)
    if size is not None and partial.stat().st_size != size:
        raise ValueError('Incomplete checkpoint download')
    if expected and sha256(partial) != expected:
        raise ValueError('Downloaded asset checksum mismatch')
    partial.rename(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.data/strum-runtime')
    args = parser.parse_args()
    for name, digest in SOURCE_FILES.items():
        download(f'https://raw.githubusercontent.com/opria123/strum/{SOURCE_REVISION}/{name}',
                 args.output / 'source' / name, digest)
    download(f'https://raw.githubusercontent.com/opria123/strum/{SOURCE_REVISION}/README.md',
             args.output / 'source/README.md')
    download(f'https://huggingface.co/opria123/strum/raw/{MODEL_REVISION}/README.md',
             args.output / 'model-card.md')
    for name, (remote, size, digest) in WEIGHTS.items():
        download(f'https://huggingface.co/opria123/strum/resolve/{MODEL_REVISION}/{remote}',
                 args.output / 'weights' / name, digest, size)
    manifest = args.output / 'manifest.json'
    if manifest.exists():
        raise ValueError('Existing manifest preserved; assets have been verified but not rewritten')
    manifest.write_text(json.dumps({'source_revision': SOURCE_REVISION, 'model_revision': MODEL_REVISION,
                                   'license': 'MIT declaration in pinned source README and model card',
                                   'files': {str(p.relative_to(args.output)): sha256(p) for p in args.output.rglob('*') if p.is_file()}}, indent=2))
    print(f'Prepared optional research assets: {args.output}')


if __name__ == '__main__':
    main()
