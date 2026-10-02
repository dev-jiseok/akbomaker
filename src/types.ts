export const instruments = ['vocal', 'bass', 'drums', 'synthesizer', 'guitar', 'piano'] as const;
export type Instrument = typeof instruments[number];
export type Stem = {
  id: Instrument;
  label: string;
  status: 'pending' | 'running' | 'ready';
  score_status: 'pending' | 'running' | 'ready' | 'error';
  waveform: number[];
  audio_url?: string;
  score_url?: string;
  midi_url?: string;
  score_error?: string;
  score_warning?: string;
  score_bpm?: number;
  score_revision?: string;
  score_edited?: boolean;
  score_title?: string;
  score_layout?: ScoreLayout;
  score_tab_mode?: 'staff' | 'both' | 'tab';
  score_tab_unassigned?: number;
  note_count?: number;
  quiet?: boolean;
};
export type ScorePreset = 'practice' | 'standard' | 'large';
export type ScoreLayout = { preset: ScorePreset; measures_per_line: 2 | 4; show_numbers: boolean; beam_group?: 'beat' | 'half' };
export type ScoreNote = { id: string; start: number; length: number; pitch: number; velocity: number; string?: number | null; fret?: number | null };
export type TabSettings = { mode: 'staff' | 'both' | 'tab'; tuning: number[] };
export type ScoreLyric = { id: string; start: number; text: string };
export type ScoreAnnotation = { measure: number; section: string; cue: string };
export type ScoreDocument = {
  version: 1; instrument: Instrument; title: string; bpm: number; ticks: number;
  revision: string; edited: boolean; notes: ScoreNote[]; annotations: ScoreAnnotation[]; layout: ScoreLayout;
  timing_bpm?: number; audio_offset?: number; tab?: TabSettings | null; lyrics?: ScoreLyric[];
};
export type LyricCue = { id: string; start: number; end: number; text: string };
export type LyricCandidate = { revision: string; cues: LyricCue[]; language?: string; source?: 'original' | 'vocal'; warning?: string };
export type Job = {
  id: string;
  title: string;
  source_type: string;
  demo: boolean;
  status: 'queued' | 'running' | 'separated' | 'transcribing' | 'analyzing' | 'completed' | 'error' | 'cancelled';
  stage: string;
  progress: number;
  message: string;
  error: string | null;
  created_at: string;
  duration: number | null;
  bpm: number | null;
  original_url: string | null;
  residual_url: string | null;
  stems: Stem[];
  analysis_only?: boolean;
  analysis_error?: string | null;
  rhythm_analysis?: { bpm: number; first_beat_seconds: number; beat_times: number[]; regularity: number; analyzed_seconds: number; alternatives: number[]; warning: string };
  lyric_candidate?: LyricCandidate;
  lyric_guide?: LyricCandidate;
};
export type Health = {
  ok: boolean;
  engine: { available: boolean; model: string; device: string; issues: string[]; transcription_available: boolean };
  limits: { max_upload_mb: number; max_audio_seconds: number };
  lyrics?: { available: boolean; model: string; device: string; compute_type: string };
};

export function isProcessing(job: Job | null): boolean {
  return !!job && ['queued', 'running', 'transcribing', 'analyzing'].includes(job.status);
}

export function formatTime(seconds: number | null | undefined): string {
  if (!seconds || !Number.isFinite(seconds)) return '0:00';
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}

export function validateFile(file: Pick<File, 'name' | 'size'>, maxMB = 200): string | null {
  if (!/\.(mp3|wav|flac|m4a|aac|ogg|opus|mp4|mov|webm|mkv|aiff|aif)$/i.test(file.name)) return '지원하지 않는 형식이에요. 음악 또는 동영상 파일을 선택해주세요.';
  if (file.size === 0) return '빈 파일은 업로드할 수 없어요.';
  if (file.size > maxMB * 1024 * 1024) return `최대 ${maxMB}MB 파일까지 업로드할 수 있어요.`;
  return null;
}

export function isYoutubeUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) return false;
    let id = '';
    if (url.hostname === 'youtu.be') id = url.pathname.replace(/^\/+|\/+$/g, '');
    else if (['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'].includes(url.hostname)) {
      id = url.pathname === '/watch' ? url.searchParams.get('v') || '' : url.pathname.match(/^\/(?:shorts|embed|live)\/([\w-]{11})\/?$/)?.[1] || '';
    }
    return /^[\w-]{11}$/.test(id);
  } catch { return false; }
}
