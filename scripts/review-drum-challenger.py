"""Create independent hat REVIEW suggestions without rewriting any score/audio.

Requires a frozen baseline-run record with input_sha256/cached_sha256, as used
by benchmark runs. A baseline without input provenance cannot be compared by
silently assuming that two similarly named files contain the same audio.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.drum_challenger import METHOD as CHALLENGER_METHOD, sha256
from backend.drum_challenger_review import propose_rejected_hihats

MAX_JSON_BYTES = 64 * 1024 * 1024
IMPLEMENTATION_FILES = ('scripts/review-drum-challenger.py',
                        'backend/drum_challenger_review.py', 'backend/drum_challenger.py')


def json_snapshot(path):
    """Bind parsed data to the exact bounded bytes read, never a later read."""
    with path.open('rb') as stream:
        content = stream.read(MAX_JSON_BYTES + 1)
    if len(content) > MAX_JSON_BYTES:
        raise ValueError('Review JSON exceeds the 64 MiB size limit')
    return json.loads(content), hashlib.sha256(content).hexdigest()


def verify_unchanged(review, audio, baseline_path, challenger_path, run_record_path):
    provenance = review['provenance']
    files = {audio: provenance['audio_sha256'], baseline_path: provenance['baseline_sha256'],
             challenger_path: provenance['challenger_sha256'], run_record_path: provenance['baseline_record_sha256']}
    files.update({ROOT / name: digest for name, digest in provenance['implementation_sha256'].items()})
    for path, expected in files.items():
        if sha256(path) != expected:
            raise ValueError(f'Review source changed during execution: {path.name}')


def build_review(audio, baseline_path, challenger_path, run_record_path, case_name):
    implementation_sha = {name: sha256(ROOT / name) for name in IMPLEMENTATION_FILES}
    audio_sha = sha256(audio)
    baseline, baseline_sha = json_snapshot(baseline_path)
    challenger, challenger_sha = json_snapshot(challenger_path)
    record_doc, record_sha = json_snapshot(run_record_path)
    records = record_doc.get('cases', [record_doc])
    matches = [row for row in records if row.get('name') == case_name]
    if len(matches) != 1:
        raise ValueError('Frozen baseline case must exist exactly once')
    record = matches[0]
    if (record.get('input_sha256') != audio_sha or record.get('cached_sha256') != baseline_sha
            or challenger.get('audio_sha256') != audio_sha):
        raise ValueError('Original audio or baseline provenance mismatch')
    if challenger.get('method') != CHALLENGER_METHOD or challenger.get('research_only') is not True:
        raise ValueError('Unsupported independent challenger')
    consensus = baseline['runtime']['consensus']
    if consensus.get('passes') != 3 or consensus.get('minimum_votes') != 2:
        raise ValueError('This review requires original three-pass/two-vote ADT consensus')
    review = propose_rejected_hihats(baseline['events'], consensus['candidates'], challenger['events'])
    review['provenance'] = {'audio_sha256': audio_sha, 'baseline_sha256': baseline_sha,
                            'challenger_sha256': challenger_sha, 'baseline_record_sha256': record_sha,
                            'baseline_record_case': case_name,
                            'review_implementation_sha256': implementation_sha['backend/drum_challenger_review.py'],
                            'implementation_sha256': implementation_sha}
    verify_unchanged(review, audio, baseline_path, challenger_path, run_record_path)
    return review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--challenger', type=Path, required=True)
    parser.add_argument('--baseline-run-record', type=Path, required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite an existing file')
    review = build_review(args.audio, args.baseline, args.challenger, args.baseline_run_record, args.case)
    content = json.dumps(review, indent=2, ensure_ascii=False, allow_nan=False)
    verify_unchanged(review, args.audio, args.baseline, args.challenger, args.baseline_run_record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as target:
        target.write(content)
    print(f"{len(review['suggestions'])} review-only suggestions; original score unchanged")


if __name__ == '__main__':
    main()
