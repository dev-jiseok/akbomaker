"""Bar geometry on the existing sixteenth grid; BPM always means quarters."""
import math

SUPPORTED_METERS = {(2, 4), (3, 4), (4, 4), (6, 8), (9, 8), (12, 8)}
DEFAULT_METERS = [{"measure": 1, "beats": 4, "beat_type": 4}]
MAX_MEASURES = 600


def normalize_meters(meters=None):
    result = [dict(m) for m in (DEFAULT_METERS if meters is None else meters)]
    if not result or len(result) > MAX_MEASURES or result[0]["measure"] != 1:
        raise ValueError("박자표는 첫 마디부터 지정해주세요.")
    previous = 0
    for item in result:
        if any(type(item[k]) is not int for k in ("measure", "beats", "beat_type")) or not previous < item["measure"] <= MAX_MEASURES:
            raise ValueError("박자 변경 마디는 1~600 사이에서 중복 없이 오름차순으로 지정해주세요.")
        if (item["beats"], item["beat_type"]) not in SUPPORTED_METERS:
            raise ValueError("지원 박자는 2/4, 3/4, 4/4, 6/8, 9/8, 12/8이에요.")
        previous = item["measure"]
    return result


def measure_map(ticks, meters=None, *, exact=True):
    """Return whole bars. exact=False pads the last bar but never truncates music."""
    if not math.isfinite(ticks) or ticks <= 0:
        raise ValueError("악보 길이는 양수여야 해요.")
    changes = normalize_meters(meters)
    bars, start, index = [], 0, 0
    while start < ticks:
        number = len(bars) + 1
        if number > MAX_MEASURES:
            raise ValueError("최대 600마디까지 지원해요. 짧은 구간으로 나눠주세요.")
        if index + 1 < len(changes) and changes[index + 1]["measure"] == number:
            index += 1
        meter = changes[index]
        length = meter["beats"] * 16 // meter["beat_type"]
        bars.append({**meter, "number": number, "start": start, "end": start + length})
        start += length
    if changes[-1]["measure"] > len(bars):
        raise ValueError("박자 변경 마디가 악보 길이를 벗어났어요.")
    if exact and start != ticks:
        raise ValueError("악보 길이는 지정한 박자의 완전한 마디 단위여야 해요.")
    return bars


def grouping_ticks(bar):
    return 6 if bar["beat_type"] == 8 else 4
