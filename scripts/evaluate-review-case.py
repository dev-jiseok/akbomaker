#!/usr/bin/env python3
"""Compare candidate note artifacts against one explicitly reviewed frozen clip."""
import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Snapshot project sources before importing the evaluator. The evaluator checks
# this again before work and after calculation; no models are imported here.
_ROOT = Path(__file__).resolve().parents[1]
_CODE_FILES = ("backend/evaluation.py", "backend/review_case_evaluation.py", "backend/review_case_regression.py",
               "backend/transcription_review.py", "backend/note_artifacts.py", "backend/config.py",
               "scripts/evaluate-review-case.py")
_STARTUP_CODE = {name: hashlib.sha256((_ROOT / name).read_bytes()).hexdigest() for name in _CODE_FILES}

from backend.review_case_regression import evaluate_regression, write_new_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Same-source reviewed-clip regression; no models, downloads or overwrite.")
    parser.add_argument("--case", required=True, help="Reviewed akbo.review-case-export JSON")
    parser.add_argument("--prediction", required=True, help="Candidate instrument.notes.json, before quantization")
    parser.add_argument("--audio", required=True, help="The exact local source WAV shared by baseline and candidate")
    parser.add_argument("--output", required=True, help="New report path; never overwrites an existing file")
    args = parser.parse_args(argv)
    try:
        report = evaluate_regression(args.case, args.prediction, args.audio, expected_code_hashes=_STARTUP_CODE)
        write_new_report(args.output, report)
    except (ValueError, OSError) as error:
        print(f"검토 구간 평가 실패: {error}", file=sys.stderr)
        return 2
    print("검수한 동일입력 구간의 비교 보고서를 저장했어요. 곡 전체 정확도나 우승 모델 판정은 아닙니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
