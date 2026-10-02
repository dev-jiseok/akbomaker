import type { ScoreDocument, ScoreMeter } from './types';

export const meterOptions = ['2/4', '3/4', '4/4', '6/8', '9/8', '12/8'];
export const defaultMeters: ScoreMeter[] = [{ measure: 1, beats: 4, beat_type: 4 }];
export type ScoreBar = ScoreMeter & { number: number; start: number; end: number };

export function measureMap(doc: Pick<ScoreDocument, 'ticks' | 'meters'>, exact = true): ScoreBar[] {
  const meters = doc.meters || defaultMeters;
  if (!Number.isFinite(doc.ticks) || doc.ticks <= 0 || !meters.length || meters[0].measure !== 1) throw new Error('악보 길이와 첫 박자표를 확인해주세요.');
  meters.forEach((m, i) => {
    if (!Number.isInteger(m.measure) || m.measure < 1 || m.measure > 600 || (i > 0 && m.measure <= meters[i - 1].measure) || !meterOptions.includes(`${m.beats}/${m.beat_type}`)) throw new Error('지원하는 박자와 중복 없는 변경 마디를 지정해주세요.');
  });
  const bars: ScoreBar[] = [];
  let start = 0, index = 0;
  while (start < doc.ticks) {
    const number = bars.length + 1;
    if (number > 600) throw new Error('최대 600마디까지 지원해요. 짧은 구간으로 나눠주세요.');
    if (index + 1 < meters.length && meters[index + 1].measure === number) index++;
    const m = meters[index], end = start + m.beats * 16 / m.beat_type;
    bars.push({ ...m, number, start, end }); start = end;
  }
  if (meters[meters.length - 1].measure > bars.length) throw new Error('박자 변경 마디가 악보 길이를 벗어나요.');
  if (exact && start !== doc.ticks) throw new Error('악보 길이가 완전한 마디 단위가 아니에요.');
  return bars;
}
export function barAtTick(bars: ScoreBar[], tick: number): ScoreBar | undefined {
  return bars.find(b => b.start <= tick && tick < b.end);
}
export function beatTicks(bar: ScoreBar) { return bar.beat_type === 8 ? 6 : 4; }
export function gridLabel(bar: ScoreBar, tick: number) {
  return bar.beat_type === 8 ? tick % 2 ? '&' : String(tick / 2 + 1) : tick % 4 ? ['e', '&', 'a'][tick % 4 - 1] : String(tick / 4 + 1);
}
export function meterSummary(meters?: ScoreMeter[]) {
  const values = meters || defaultMeters;
  return values.length > 1 ? `${values[0].beats}/${values[0].beat_type} · 변박 ${values.length - 1}회` : `${values[0].beats}/${values[0].beat_type}`;
}

export function rebar(doc: ScoreDocument, meters: ScoreMeter[]): ScoreDocument {
  const old = measureMap(doc), bars = measureMap({ ticks: doc.ticks, meters }, false);
  const mapNumber = (number: number) => {
    const source = old[number - 1];
    if (!source) throw new Error('기존 마디 표시의 위치가 올바르지 않아요.');
    return barAtTick(bars, source.start)!.number;
  };
  const annotations = new Map<number, ScoreDocument['annotations'][number]>();
  for (const a of doc.annotations) {
    const number = mapNumber(a.measure), existing = annotations.get(number);
    const next = { ...a, measure: number, section: [existing?.section, a.section].filter(Boolean).join(' / '), cue: [existing?.cue, a.cue].filter(Boolean).join(' / ') };
    if (next.section.length > 24 || next.cue.length > 100) throw new Error('박자 변경으로 구간 메모가 한 마디에 너무 길게 모여요. 메모를 먼저 정리해주세요.');
    annotations.set(number, next);
  }
  const breaks = (values: number[] = []) => [...new Set(values.map(mapNumber).filter(n => n > 1))].sort((a, b) => a - b);
  return { ...doc, meters, ticks: bars[bars.length - 1].end, annotations: [...annotations.values()], layout: { ...doc.layout, system_breaks: breaks(doc.layout.system_breaks), page_breaks: breaks(doc.layout.page_breaks) } };
}
export function setMeter(doc: ScoreDocument, measure: number, value: string): ScoreDocument {
  if (!Number.isInteger(measure) || measure < 1 || measure > measureMap(doc).length || !meterOptions.includes(value)) throw new Error('박자 변경 위치를 확인해주세요.');
  const [beats, beat_type] = value.split('/').map(Number);
  const changes = [...(doc.meters || defaultMeters).filter(m => m.measure !== measure), { measure, beats, beat_type }].sort((a, b) => a.measure - b.measure);
  const meters = changes.filter((m, i) => i === 0 || m.beats !== changes[i - 1].beats || m.beat_type !== changes[i - 1].beat_type);
  return rebar(doc, meters);
}
