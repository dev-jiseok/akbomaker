import { useEffect, useRef, useState } from 'react';
import { Download, Headphones, LoaderCircle, Pause, Play, RotateCcw, Volume2, VolumeX, Mic2, Guitar, Drum, Piano, AudioLines } from 'lucide-react';
import type { Instrument, Job } from '../types';
import { formatTime } from '../types';

export const instrumentIcons = { vocal: Mic2, bass: Guitar, drums: Drum, synthesizer: AudioLines, guitar: Guitar, piano: Piano };

type Props = { job: Job; selected: Instrument; onSelect: (id: Instrument) => void; onError: (message: string) => void };

export default function Mixer({ job, selected, onSelect, onError }: Props) {
  const players = useRef(new Map<string, HTMLAudioElement>());
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [original, setOriginal] = useState(false);
  const [solo, setSolo] = useState<Instrument | null>(null);
  const [muted, setMuted] = useState<Set<Instrument>>(new Set());
  const [volumes, setVolumes] = useState<Record<string, number>>({});
  const modeRef = useRef(original);
  const playingRef = useRef(false);
  const firstStem = job.stems.find(stem => stem.audio_url)?.id;
  const playable = !!firstStem;
  const duration = job.duration || 0;
  useEffect(() => {
    modeRef.current = original;
    for (const [id, audio] of players.current) {
      const active = id === 'original' ? original : !original && (!solo || id === solo) && !muted.has(id as Instrument);
      audio.muted = !active;
      audio.volume = volumes[id] ?? 0.8;
    }
  }, [original, solo, muted, volumes, job.stems]);
  useEffect(() => {
    const collection = players.current;
    return () => { for (const audio of collection.values()) audio.pause(); };
  }, []);
  useEffect(() => {
    // Stems can finish while the user is already listening. Join a newly ready
    // channel at the same playback position rather than leaving it paused.
    if (!playingRef.current) return;
    const reference = [...players.current.values()].find(audio => !audio.paused);
    for (const audio of players.current.values()) {
      if (audio.paused && !audio.ended) {
        audio.currentTime = reference?.currentTime || 0;
        void audio.play().catch(() => {
          for (const player of players.current.values()) player.pause();
          playingRef.current = false;
          setPlaying(false);
        });
      }
    }
  }, [job.stems]);

  function stop() {
    for (const audio of players.current.values()) audio.pause();
    playingRef.current = false;
    setPlaying(false);
  }
  async function play() {
    if (!playable) return;
    const collection = [...players.current.values()];
    try {
      playingRef.current = true;
      setPlaying(true);
      for (const audio of collection) audio.currentTime = position >= duration ? 0 : position;
      await Promise.all(collection.map(audio => audio.play()));
    } catch {
      stop();
      onError('음원을 재생하지 못했어요. 파일을 다시 불러오거나 내려받아 확인해주세요.');
    }
  }
  function seek(value: number) {
    setPosition(value);
    for (const audio of players.current.values()) if (Number.isFinite(audio.duration)) audio.currentTime = value;
  }
  function toggleMute(id: Instrument) {
    setMuted(current => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next; });
  }
  function soloStem(id: Instrument) {
    onSelect(id);
    setOriginal(false);
    setMuted(current => { const next = new Set(current); next.delete(id); return next; });
    setSolo(current => current === id ? null : id);
  }
  return <section className="card mixer-card">
    <div className="panel-heading"><div><span className="eyebrow">LISTEN CLOSELY</span><h2>음악 속 여섯 가지 소리</h2><p>따로 듣고, 함께 듣고. 나에게 필요한 소리를 찾아보세요.</p></div><span className="count-label">6 STEMS</span></div>
    <div className="transport">
      <button className="play-master" disabled={!playable} onClick={() => playing ? stop() : void play()} aria-label={playing ? '일시정지' : '음악 재생'}>{playing ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" />}</button>
      <button className="icon-button reset-play" disabled={!playable} onClick={() => seek(0)} aria-label="처음부터 듣기"><RotateCcw size={17} /></button>
      <span className="time-display">{formatTime(position)}</span>
      <input className="seek-range" aria-label="재생 위치" type="range" min="0" max={duration || 1} step="0.01" value={position} disabled={!playable} onChange={event => seek(Number(event.target.value))} />
      <span className="time-display duration">{formatTime(duration)}</span>
      <button className={`compare-button ${original ? 'active' : ''}`} disabled={!job.original_url} onClick={() => setOriginal(!original)} aria-pressed={original}>원본 비교</button>
    </div>
    <div className="stem-list">{job.stems.map((stem, index) => {
      const Icon = instrumentIcons[stem.id];
      return <div className={`stem-row tone-${stem.id} ${selected === stem.id ? 'selected' : ''} ${stem.status !== 'ready' ? 'not-ready' : ''}`} key={stem.id}>
        <button className="stem-name" onClick={() => onSelect(stem.id)} aria-pressed={selected === stem.id}><span className="stem-icon"><Icon size={19} strokeWidth={1.7} /></span><span><strong>{stem.label}</strong><small>{['VOCAL', 'BASS', 'DRUMS', 'SYNTH', 'GUITAR', 'PIANO'][index]}</small></span></button>
        <div className="stem-wave" aria-hidden="true">{stem.waveform.length ? <svg viewBox="0 0 384 36" preserveAspectRatio="none">{stem.waveform.map((value, i) => <rect key={i} x={i * 4} y={18 - Math.max(1, value * 16)} width="2" height={Math.max(2, value * 32)} rx="1" />)}</svg> : <div className="pending-wave">{stem.status === 'running' ? <><LoaderCircle className="spin" size={16} /><span>분리 중</span></> : <span>차례를 기다리고 있어요</span>}</div>}</div>
        <button className={`solo-button ${solo === stem.id && !original ? 'active' : ''}`} onClick={() => soloStem(stem.id)} disabled={stem.status !== 'ready'} aria-label={`${stem.label}만 듣기`} aria-pressed={solo === stem.id && !original}><Headphones size={17} /></button>
        <button className={`icon-button mute-button ${muted.has(stem.id) ? 'muted' : ''}`} disabled={stem.status !== 'ready'} onClick={() => toggleMute(stem.id)} aria-label={`${stem.label} ${muted.has(stem.id) ? '음소거 해제' : '음소거'}`} aria-pressed={muted.has(stem.id)}>{muted.has(stem.id) ? <VolumeX size={17} /> : <Volume2 size={17} />}</button>
        <input className="volume-range" type="range" aria-label={`${stem.label} 볼륨`} min="0" max="1" step="0.01" value={volumes[stem.id] ?? 0.8} disabled={stem.status !== 'ready'} onChange={event => setVolumes({ ...volumes, [stem.id]: Number(event.target.value) })} />
        {stem.audio_url ? <a className="icon-button stem-download" href={stem.audio_url + '?download=true'} aria-label={`${stem.label} WAV 다운로드`}><Download size={17} /></a> : <span className="stem-status-dot" />}
      </div>;
    })}</div>
    <div className="mixer-footer"><span><Headphones size={14} /> 헤드폰 버튼으로 한 악기만 들어보세요</span>{job.residual_url && <a href={job.residual_url + '?download=true'}>잔여 음원 받기 <Download size={13} /></a>}</div>
    {job.stems.filter(stem => stem.audio_url).map(stem => <audio key={stem.id} src={stem.audio_url} preload="metadata" ref={node => { if (node) players.current.set(stem.id, node); else players.current.delete(stem.id); }} onTimeUpdate={event => { if (!modeRef.current && stem.id === firstStem) setPosition(event.currentTarget.currentTime); }} onEnded={() => { if (!modeRef.current && stem.id === firstStem) { stop(); setPosition(duration); } }} />)}
    {job.original_url && <audio src={job.original_url} preload="metadata" ref={node => { if (node) players.current.set('original', node); else players.current.delete('original'); }} onTimeUpdate={event => { if (modeRef.current) setPosition(event.currentTarget.currentTime); }} onEnded={() => { if (modeRef.current) { stop(); setPosition(duration); } }} />}
  </section>;
}
