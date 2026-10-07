import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Headphones, LoaderCircle, Pencil, Play, Square } from 'lucide-react';
import { request } from '../api';
import { useAudioFocus } from '../audioFocus';
import { playReviewAudio, playReviewNotes } from '../reviewPlayback';
import { reviewAudioUrl, reviewGeometry, reviewLabels, reviewLayers, reviewPitchLabel, reviewWindow,
  unsupportedReviewVoices, type ReviewData, type ReviewEvent, type ReviewLayer } from '../transcriptionReview';
import type { Job, Stem } from '../types';
import ReviewCaseWorkbench from './ReviewCaseWorkbench';

type Props = { job: Job; stem: Stem; disabled: boolean; onEdit: (sourceSeconds: number) => void; onDirty?: (dirty: boolean) => void };
const ignoreDirty = () => {};
type Selection = { layer: ReviewLayer; event: ReviewEvent };

export function ReviewTimeline({ data, position, onSelect }: { data: ReviewData; position: number | null; onSelect: (selection: Selection) => void }) {
  const pitches = [...new Set(reviewLayers.flatMap(layer => data.layers[layer].events.map(event => event.pitch)))].sort((a, b) => b - a);
  if (!pitches.length) return <p className="review-empty">이 구간에는 표시할 음표가 없어요. 실제 소리가 있는지는 원음으로 확인해주세요.</p>;
  const left = 155, width = 640, top = 34, row = 33, height = top + pitches.length * row + 12;
  const x = (seconds: number) => left + (seconds - data.window.start) / (data.window.end - data.window.start) * width;
  return <div className="review-timeline-scroll" tabIndex={0} aria-label="인식·자동·현재 악보의 음표 비교 타임라인. 음표를 선택하면 시각을 확인할 수 있어요.">
    <svg viewBox={`0 0 ${left + width + 12} ${height}`} className="review-timeline" role="group" aria-label="같은 원음 시각 기준 음표 비교">
      {Array.from({ length: 5 }, (_, i) => {
        const time = data.window.start + (data.window.end - data.window.start) * i / 4;
        return <g key={i}><line x1={x(time)} x2={x(time)} y1={top - 7} y2={height - 5} className="review-time-grid" /><text x={x(time)} y={18} textAnchor={i === 4 ? 'end' : 'start'}>{time.toFixed(2)}s</text></g>;
      })}
      {pitches.map((pitch, index) => <g key={pitch}>
        <text x={4} y={top + index * row + 17} className="review-pitch-label">{reviewPitchLabel(data.instrument, pitch)}</text>
        <line x1={left} x2={left + width} y1={top + (index + 1) * row - 2} y2={top + (index + 1) * row - 2} className="review-pitch-grid" />
      </g>)}
      {reviewLayers.map((layer, lane) => data.layers[layer].events.map(event => {
        const geometry = reviewGeometry(event, data.window);
        if (!geometry) return null;
        const label = `${reviewLabels[layer]}, ${reviewPitchLabel(data.instrument, event.pitch)}, ${event.start.toFixed(3)}초부터 ${event.end.toFixed(3)}초`;
        return <rect key={`${layer}:${event.id}`} className={`review-note review-${layer}`} x={left + geometry.left * width}
          y={top + pitches.indexOf(event.pitch) * row + lane * 9} width={Math.max(1.5, geometry.width * width)} height={7} rx={2}
          role="button" tabIndex={0} aria-label={label} onClick={() => onSelect({ layer, event })}
          onKeyDown={eventKey => { if (eventKey.key === 'Enter' || eventKey.key === ' ') { eventKey.preventDefault(); onSelect({ layer, event }); } }}><title>{label}</title></rect>;
      }))}
      {position !== null && position >= data.window.start && position <= data.window.end && <line className="review-cursor" x1={x(position)} x2={x(position)} y1={top - 8} y2={height} />}
    </svg>
  </div>;
}

export default function TranscriptionReview({ job, stem, disabled, onEdit, onDirty = ignoreDirty }: Props) {
  const [opened, setOpened] = useState(false);
  const [startText, setStartText] = useState('0');
  const [seconds, setSeconds] = useState(8);
  const [range, setRange] = useState({ start: 0, seconds: 8, nonce: 0 });
  const [payload, setPayload] = useState<ReviewData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [speed, setSpeed] = useState(1);
  const [loop, setLoop] = useState(true);
  const [playing, setPlaying] = useState<string | null>(null);
  const [position, setPosition] = useState<number | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const stop = useRef<() => void>(() => {});
  const playbackGeneration = useRef(0);
  const available = !job.demo && job.source_type !== 'musicxml' && stem.score_transcription?.raw_events === true && !!stem.score_url;
  const ready = available && stem.score_status === 'ready';
  const data = ready && payload?.instrument === stem.id && payload.score.revision === stem.score_revision ? payload : null;
  const duration = job.duration || 0;
  const stopPlaying = () => { playbackGeneration.current++; stop.current(); stop.current = () => {}; setPlaying(null); setPosition(null); };
  const claimAudioFocus = useAudioFocus(stopPlaying);

  useEffect(() => {
    stopPlaying(); setPayload(null); setSelection(null); setError('');
    if (!opened || !ready || disabled) { setLoading(false); return; }
    const controller = new AbortController(); let current = true;
    setLoading(true);
    request<ReviewData>(`/api/jobs/${job.id}/transcription-review/${stem.id}?start=${range.start}&seconds=${range.seconds}`, { signal: controller.signal })
      .then(result => { if (current) setPayload(result); })
      .catch(reason => { if (current && !controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; controller.abort(); stopPlaying(); };
  }, [opened, ready, disabled, job.id, stem.id, stem.score_revision, range]);
  useEffect(() => { stopPlaying(); }, [speed, loop]);
  useEffect(() => () => { playbackGeneration.current++; stop.current(); }, []);

  function loadWindow(start = Number(startText)) {
    try {
      reviewWindow(start, seconds, duration);
      setStartText(String(Math.round(start * 1000) / 1000));
      setRange(previous => ({ start, seconds, nonce: previous.nonce + 1 }));
    } catch (reason) { setError((reason as Error).message); }
  }
  function audition(kind: 'original' | 'stem' | ReviewLayer) {
    if (!data || disabled) return;
    const toggleOff = playing === kind;
    stopPlaying(); if (toggleOff) return;
    claimAudioFocus(); setError('');
    const generation = ++playbackGeneration.current;
    const options = { window: data.window, speed, loop,
      onPosition: (value: number | null) => { if (generation === playbackGeneration.current) setPosition(value); },
      onStop: () => { if (generation === playbackGeneration.current) { setPlaying(null); setPosition(null); } },
      onError: (message: string) => { if (generation === playbackGeneration.current) setError(message); },
    };
    try {
      setPlaying(kind);
      if (kind === 'original' || kind === 'stem') {
        const url = reviewAudioUrl(data.audio[kind === 'original' ? 'original_url' : 'stem_url'], job.id);
        if (!url) throw new Error('이 프로젝트의 비교 음원이 준비되지 않았어요.');
        stop.current = playReviewAudio(url, options);
      } else stop.current = playReviewNotes(data.layers[kind].events, stem.id, options);
    } catch (reason) { stopPlaying(); setError((reason as Error).message); }
  }
  function editHere() {
    stopPlaying(); onEdit(selection?.event.start ?? data?.window.start ?? range.start);
  }

  if (job.demo || job.source_type === 'musicxml') return null;
  return <><section className="transcription-review" aria-label={`${stem.label} 정확성 검토`}>
    <div className="review-heading"><div><span className="eyebrow">TRACE THE NOTES</span><h3>소리에서 악보까지, 같은 구간 비교</h3><p>분리·음표 인식·박자 정리 중 어디에서 달라졌는지 직접 확인해요.</p></div>
      <button className="secondary small" disabled={!available || disabled || stem.score_status !== 'ready'} aria-expanded={opened} onClick={() => setOpened(!opened)}><Headphones size={15} />{opened ? '검토 접기' : '구간 비교 열기'}</button></div>
    {!available && <p className="editor-help">채보 당시 음표 기록이 있는 실제 음원 프로젝트에서 사용할 수 있어요. 이전 프로젝트는 파일을 보관한 뒤 다시 채보해야 기록이 생깁니다.</p>}
    {available && stem.score_status === 'error' && <p className="editor-help">이전 악보는 보관되어 있어요. 채보 실패 뒤 서로 다른 시점의 기록이 섞이지 않도록 비교는 잠갔어요. 다시 채보가 성공하면 열 수 있습니다.</p>}
    {opened && <div className="review-content">
      <div className="review-range-controls"><label>원곡 시작 (초)<input aria-label="검토 구간 시작 초" type="number" step="0.1" min="0" max={Math.max(0, duration - .001)} value={startText} onChange={event => setStartText(event.target.value)} disabled={disabled} /></label>
        <label>구간 길이<select aria-label="검토 구간 길이" value={seconds} onChange={event => setSeconds(Number(event.target.value))} disabled={disabled}>{[4, 8, 16, 30].map(value => <option key={value} value={value}>{value}초</option>)}</select></label>
        <button className="secondary small" onClick={() => loadWindow()} disabled={disabled || loading}>{loading ? <LoaderCircle size={14} className="spin" /> : null}구간 불러오기</button>
        <button className="icon-button" aria-label="이전 검토 구간" disabled={disabled || loading || range.start === 0} onClick={() => loadWindow(Math.max(0, range.start - range.seconds))}><ChevronLeft size={17} /></button>
        <button className="icon-button" aria-label="다음 검토 구간" disabled={disabled || loading || range.start + range.seconds >= duration} onClick={() => loadWindow(range.start + range.seconds)}><ChevronRight size={17} /></button>
      </div>
      {error && <p className="inline-alert" role="alert">{error}</p>}
      {loading && <p role="status" className="review-loading">입력 음원과 채보 기록을 확인하고 있어요…</p>}
      {data && !loading && <>
        <div className="review-source"><strong>{data.window.start.toFixed(2)}–{data.window.end.toFixed(2)}초</strong><span>채보 입력: {data.source.kind === 'original' ? '원본 전체 음원' : `분리된 ${stem.label}`}</span><span>조회 시 입력 해시 일치 · 정확도 인증 아님</span></div>
        <div className="review-transport"><div className="review-audio-buttons">
          <button className="secondary small" aria-pressed={playing === 'original'} disabled={!data.audio.original_url || disabled} onClick={() => audition('original')}>{playing === 'original' ? <Square size={14} /> : <Play size={14} />}원곡 듣기</button>
          <button className="secondary small" aria-pressed={playing === 'stem'} disabled={!data.audio.stem_url || disabled} onClick={() => audition('stem')}>{playing === 'stem' ? <Square size={14} /> : <Play size={14} />}분리음 듣기</button>
        </div><label>속도<select aria-label="검토 재생 속도" value={speed} onChange={event => setSpeed(Number(event.target.value))}>{[.5, .75, 1].map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
          <label className="check-label"><input type="checkbox" checked={loop} onChange={event => setLoop(event.target.checked)} />구간 반복</label>
          <button className="text-button" disabled={!playing} onClick={stopPlaying}><Square size={13} />정지</button>
          <output className="review-position">{position === null ? '같은 시작점에서 비교' : `${position.toFixed(2)}초`}</output>
        </div>
        <div className="review-legend">{reviewLayers.map(layer => <div className={`review-stage review-${layer}`} key={layer}>
          <strong><i />{reviewLabels[layer]}</strong><span>구간 내 {data.layers[layer].in_window}개</span>
          <button className="text-button" disabled={disabled || !data.layers[layer].events.length} aria-pressed={playing === layer} onClick={() => audition(layer)}>{playing === layer ? <Square size={13} /> : <Play size={13} />}음표 듣기</button>
        </div>)}</div>
        <p className="review-help">위→아래: 인식 / 자동 / 현재. 색 차이는 오류 표시가 아니에요. 음표를 누르면 시각을 볼 수 있어요. 음표 듣기는 음정·시점 확인용 간이 합성음이며 실제 악기 음색이 아닙니다.</p>
        <ReviewTimeline data={data} position={position} onSelect={setSelection} />
        {selection && <div className="review-selection" role="status"><strong>{reviewLabels[selection.layer]} · {reviewPitchLabel(stem.id, selection.event.pitch)}</strong><span>시작 {selection.event.start.toFixed(3)}초 · 끝 {selection.event.end.toFixed(3)}초 · 길이 {(selection.event.end - selection.event.start).toFixed(3)}초</span><small>강도 {selection.event.amplitude.toFixed(3)} · 신뢰도/정답 확률 아님</small></div>}
        {stem.id === 'drums' && reviewLayers.some(layer => unsupportedReviewVoices(data.layers[layer].events, stem.id) > 0) && <p className="score-warning">간이 합성음에서 지원하지 않는 GM 타격이 있어요. 타임라인에는 보존하지만 임의의 킥 소리로 대신 재생하지 않습니다. 원음을 함께 확인해주세요.</p>}
        <div className="review-footer"><p>원음 기준 {data.score.timing_bpm} BPM · 시작 오프셋 {data.score.audio_offset}초{data.score.edited ? ' · 현재 악보는 수정본' : ''}{stem.id === 'drums' && <><br />드럼 음표 길이는 기보용 길이이며 타격 소리의 실제 지속시간과 다를 수 있어요.</>}</p>
          <button className="secondary small" onClick={editHere} disabled={disabled}><Pencil size={14} />이 위치의 악보 수정</button></div>
        {data.warnings.map((warning, index) => <p key={index} className="review-help">{warning}</p>)}
      </>}
    </div>}
  </section><ReviewCaseWorkbench key={`${job.id}:${stem.id}`} jobId={job.id} instrument={stem.id} data={data} selection={selection} disabled={disabled} onDirty={onDirty} /></>;
}
