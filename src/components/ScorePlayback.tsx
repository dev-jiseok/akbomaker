import { useEffect, useRef, useState } from 'react';
import { Play, Square } from 'lucide-react';
import { playbackNotes, tickSeconds } from '../scorePlayback';
import { measureMap } from '../scoreRhythm';
import type { ScoreDocument } from '../types';

type Props = { document: ScoreDocument; measure: number; onTick: (tick: number | null) => void; onError: (message: string) => void; stopKey?: number };

export default function ScorePlayback({ document: doc, measure, onTick, onError, stopKey }: Props) {
  const [to, setTo] = useState(measure);
  const [loop, setLoop] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const stop = useRef<() => void>(() => {});
  const generation = useRef(0);
  const bars = measureMap(doc), endMeasure = Math.min(bars.length, Math.max(measure, to));
  useEffect(() => { setTo(measure); }, [measure]);
  useEffect(() => { stop.current(); return () => stop.current(); }, [doc, measure, to, speed, loop, stopKey]);

  async function play() {
    stop.current();
    const expected = ++generation.current;
    let context: AudioContext | undefined;
    let timer: ReturnType<typeof setInterval> | undefined;
    const finish = () => { generation.current++; clearInterval(timer); if (context && context.state !== 'closed') void context.close().catch(() => {}); setPlaying(false); onTick(null); };
    stop.current = finish;
    try {
      context = new AudioContext();
      await context.resume();
      if (generation.current !== expected) { if (context.state !== 'closed') await context.close(); return; }
      const ctx = context;
      const master = ctx.createGain(); master.gain.value = .3;
      const limiter = ctx.createDynamicsCompressor(); master.connect(limiter); limiter.connect(ctx.destination);
      const noise = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
      const data = noise.getChannelData(0);
      for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
      const start = bars[measure - 1].start, end = bars[endMeasure - 1].end;
      const notes = playbackNotes(doc, start, end, speed);
      const duration = tickSeconds(end - start, doc.bpm, speed);
      const origin = ctx.currentTime + .08;
      let cycle = 0, index = 0;
      function voice(note: typeof notes[number], when: number) {
        const drum = doc.instrument === 'drums';
        const gain = ctx.createGain();
        const length = note.muted ? Math.min(note.duration, .08) : note.articulation === 'staccato' ? note.duration * .45 : drum ? Math.min(note.duration, note.pitch === 46 || note.pitch === 49 ? .3 : .13) : note.duration;
        const amplitude = Math.min(1, note.velocity / 127 * (note.articulation === 'accent' ? 1.3 : 1)) * (drum ? .22 : .16);
        gain.gain.setValueAtTime(.0001, when); gain.gain.exponentialRampToValueAtTime(Math.max(.0002, amplitude), when + Math.min(.005, length / 4));
        gain.gain.exponentialRampToValueAtTime(.0001, when + Math.max(.006, length)); gain.connect(master);
        let source: OscillatorNode | AudioBufferSourceNode;
        let filter: BiquadFilterNode | undefined;
        if (drum && [38, 42, 46, 49, 51].includes(note.pitch)) {
          const hiss = ctx.createBufferSource(); hiss.buffer = noise; source = hiss;
          filter = ctx.createBiquadFilter(); filter.type = note.pitch === 38 ? 'bandpass' : 'highpass'; filter.frequency.value = note.pitch === 38 ? 1800 : 6500;
          source.connect(filter); filter.connect(gain);
        } else {
          const tone = ctx.createOscillator(); source = tone;
          tone.type = drum || doc.instrument === 'bass' ? 'sine' : 'triangle';
          tone.frequency.setValueAtTime(drum ? (note.pitch === 36 ? 110 : 130 + (note.pitch - 45) * 18) : 440 * 2 ** ((note.pitch - 69) / 12), when);
          if (drum) tone.frequency.exponentialRampToValueAtTime(note.pitch === 36 ? 42 : 90, when + length);
          else if (note.bend) tone.frequency.exponentialRampToValueAtTime(440 * 2 ** ((note.pitch + note.bend - 69) / 12), when + length * .8);
          source.connect(gain);
        }
        source.onended = () => { source.disconnect(); filter?.disconnect(); gain.disconnect(); };
        source.start(when); source.stop(when + Math.max(.01, length) + .01);
      }
      function pump() {
        if (generation.current !== expected) return;
        const elapsed = ctx.currentTime - origin;
        if (!loop && elapsed >= duration) { finish(); return; }
        const cutoff = ctx.currentTime + .15;
        // Schedule only a short window, even for a long score. This bounds
        // active audio nodes and keeps stop/loop responsive.
        while (true) {
          while (index < notes.length && origin + cycle * duration + notes[index].at <= cutoff) {
            const note = notes[index++]; const when = origin + cycle * duration + note.at;
            if (when >= ctx.currentTime - .03) voice(note, Math.max(ctx.currentTime, when));
          }
          if (!loop || origin + (cycle + 1) * duration > cutoff) break;
          cycle++; index = 0;
        }
        if (elapsed >= 0) onTick(start + Math.floor((loop ? elapsed % duration : elapsed) / tickSeconds(1, doc.bpm, speed)));
      }
      setPlaying(true); pump(); timer = setInterval(pump, 50);
    } catch { finish(); onError('악보 소리를 재생하지 못했어요. 브라우저의 소리 재생 허용 여부를 확인해주세요.'); }
  }
  return <div className="score-playback"><div><strong>수정한 음표 미리듣기</strong><small>간단한 합성음 · 저장 전 변경사항도 재생</small></div><label>{measure}마디부터<select value={endMeasure} onChange={e => setTo(Number(e.target.value))}>{Array.from({ length: bars.length - measure + 1 }, (_, i) => <option key={i} value={measure + i}>{measure + i}마디까지</option>)}</select></label><label>속도<select value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value={.5}>0.5×</option><option value={.75}>0.75×</option><option value={1}>1×</option><option value={1.25}>1.25×</option></select></label><label className="check-label"><input type="checkbox" checked={loop} onChange={e => setLoop(e.target.checked)} /> 반복</label><button className="secondary small" onClick={() => playing ? stop.current() : void play()}>{playing ? <Square size={14} /> : <Play size={14} />}{playing ? '정지' : '악보 듣기'}</button></div>;
}
