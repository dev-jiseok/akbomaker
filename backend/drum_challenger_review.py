"""Review-only evidence for an existing ADT hat; never edits score events.

The independent detector does not recognize hat articulations. False positives
remain (notably Country1 mix and Disco in the development diagnostic), so these
are suggestions for listening/manual review, not an accuracy-certified repair.
"""
from bisect import bisect_left, bisect_right
import math

METHOD = 'independent-confirmed-rejected-hat-review-v1'
TOLERANCE = .05
HATS = {42, 44, 46}


def _validate_event(event):
    if len(event) != 4:
        raise ValueError('An event needs start/end/GM pitch/amplitude')
    start, end, pitch, amp = event
    if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in event)
            or start < 0 or end <= start or int(pitch) != pitch or not 35 <= pitch <= 81 or not 0 <= amp <= 1):
        raise ValueError('Invalid drum review event')


def propose_rejected_hihats(baseline, candidates, independent):
    """Return suggestions only; original objects, time, subtype stay untouched."""
    if any(len(items) > 12000 for items in (baseline, candidates, independent)):
        raise ValueError('Review excerpts have too many events')
    for event in [*baseline, *independent]:
        _validate_event(event)
    existing_times = sorted(event[0] for event in baseline if event[2] in HATS)

    def near_existing(start):
        return bisect_left(existing_times, start - TOLERANCE - 1e-12) < bisect_right(existing_times, start + TOLERANCE + 1e-12)

    # Reserve independent hats already explaining an existing baseline hat.
    hats = sorted((event[0], index, event) for index, event in enumerate(independent)
                  if event[2] == 42 and not near_existing(event[0]))
    times = [item[0] for item in hats]
    pairs = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or 'event' not in candidate:
            raise ValueError('Malformed ADT candidate')
        _validate_event(candidate['event'])
        event = candidate['event']
        if candidate.get('accepted') is not False or type(candidate.get('votes')) is not int or candidate['votes'] != 1:
            continue
        ids = candidate.get('pass_ids')
        if not isinstance(ids, list) or len(ids) != 1 or type(ids[0]) is not int or ids[0] not in (0, 1, 2):
            raise ValueError('Rejected one-vote candidate must name its one ADT pass')
        if event[2] not in HATS or near_existing(event[0]):
            continue
        left = bisect_left(times, event[0] - TOLERANCE - 1e-12)
        right = bisect_right(times, event[0] + TOLERANCE + 1e-12)
        for timestamp, independent_index, independent_event in hats[left:right]:
            pairs.append((abs(event[0]-timestamp), event[0], index, independent_index, event, independent_event))
            if len(pairs) > 100000:
                raise ValueError('Review candidates are implausibly dense')
    used_candidate, used_independent, suggestions = set(), set(), []
    for delta, _, index, independent_index, event, witness in sorted(pairs):
        if index in used_candidate or independent_index in used_independent:
            continue
        if any(abs(suggestion['event'][0] - event[0]) <= TOLERANCE + 1e-12 for suggestion in suggestions):
            continue
        used_candidate.add(index)
        used_independent.add(independent_index)
        suggestions.append({'event': list(event), 'adt_candidate_index': index,
                            'independent_event_index': independent_index, 'independent_event': list(witness),
                            'delta_seconds': delta, 'subtype_source': 'unconfirmed original ADT candidate',
                            'independent_hat_articulation': 'unspecified'})
    return {'method': METHOD, 'review_only': True, 'auto_apply': False,
            'original_event_count': len(baseline), 'suggestions': suggestions,
            'warning': '실험 후보예요. Country1 원곡·Disco 검증에서 추가 오류가 발생했어요. 열린/닫힌/페달 구분은 독립 모델이 검증하지 못하므로 듣고 확인해주세요.'}
