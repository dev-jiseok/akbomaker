export const instruments = ['vocal', 'bass', 'drums', 'synthesizer', 'guitar', 'piano'] as const;
export type Instrument = typeof instruments[number];
export type DrumEngine = 'auto' | 'neural' | 'spectral' | 'hybrid' | 'consensus';
export type PitchedEngine = 'standard' | 'adaptive';
export type SeparationStrategy = 'sequential' | 'independent';
export type Stem = {
  id: Instrument;
  label: string;
  status: 'pending' | 'running' | 'ready';
  score_status: 'pending' | 'running' | 'ready' | 'error';
  waveform: number[];
  audio_url?: string;
  score_url?: string;
  score_source_url?: string;
  midi_url?: string;
  score_error?: string;
  score_warning?: string;
  score_bpm?: number;
  score_audio_offset?: number;
  score_meters?: ScoreMeter[];
  score_revision?: string;
  score_edited?: boolean;
  score_title?: string;
  score_layout?: ScoreLayout;
  score_tab_mode?: 'staff' | 'both' | 'tab';
  score_tab_unassigned?: number;
  score_keyboard?: KeyboardSettings | null;
  score_transcription?: TranscriptionInfo | null;
  note_count?: number;
  quiet?: boolean;
};
export type ScorePreset = 'practice' | 'standard' | 'large';
export type ScoreLayout = { preset: ScorePreset; measures_per_line: 2 | 4; show_numbers: boolean; beam_group?: 'beat' | 'half'; system_breaks?: number[]; page_breaks?: number[] };
export type ScoreNote = { id: string; start: number; length: number; pitch: number; velocity: number; string?: number | null; fret?: number | null; articulation?: 'none' | 'accent' | 'staccato' | 'tenuto'; muted?: boolean; bend?: number; hand?: 'auto' | 'right' | 'left' };
export type KeyboardSettings = { mode: 'grand' | 'single'; split_pitch: number };
export type TranscriptionInfo = { engine: string; profile: string; warning: string; timing_reviewed?: boolean;
  source?: 'stem' | 'original'; raw_midi?: boolean; raw_events?: boolean; mapping?: string; device?: string; elapsed_seconds?: number;
  recovery?: { method: string; candidate_count: number; added_count: number; suppression_seconds: number };
  context_passes?: number;
  pitched_review?: boolean;
  pitched_postprocessing?: { method: string; baseline_count: number; output_count: number; octave_overlap_count: number; octave_overlap_truncated?: boolean };
  consensus?: { method: string; passes: number; minimum_votes: number; accepted_count: number; rejected_count: number; agreement_is_confidence: boolean };
  conditioning?: { normalization: string; gain: number; input_peak: number; input_rms: number; silent_input: boolean };
  review?: { raw_note_count: number; kit_note_count: number; unsupported_count: number; merged_count: number; simplified_counts: Record<string, number> };
};
export type TabSettings = { mode: 'staff' | 'both' | 'tab'; tuning: number[]; order?: 'tab-first' | 'staff-first'; capo?: number };
export type ScoreLyric = { id: string; start: number; text: string };
export type ScoreAnnotation = { measure: number; section: string; cue: string };
export type ScoreMeter = { measure: number; beats: number; beat_type: number };
export type ScoreDocument = {
  version: 1; instrument: Instrument; title: string; bpm: number; ticks: number;
  revision: string; edited: boolean; notes: ScoreNote[]; annotations: ScoreAnnotation[]; layout: ScoreLayout;
  timing_bpm?: number; audio_offset?: number; tab?: TabSettings | null; lyrics?: ScoreLyric[];
  meters?: ScoreMeter[];
  keyboard?: KeyboardSettings | null;
  transcription?: TranscriptionInfo;
};
export type LyricCue = { id: string; start: number; end: number; text: string };
export type LyricCandidate = { revision: string; cues: LyricCue[]; language?: string; source?: 'original' | 'vocal'; warning?: string };
export type RhythmEvidence = {
  method: string; metrics_are_confidence: false; downbeat_known: false;
  grid_fit: { status: 'stable_fit' | 'review_required'; fitted_bpm: number; candidate_bpm: number | null;
    first_pulse_seconds: number; p95_deviation_seconds: number; baseline_end_drift_seconds: number;
    beat_count: number; interval_consistent: boolean; downbeat_known: false; automatically_applied: false };
  tempo_candidates: { bpm: number; periodicity: number }[];
  tempo_ambiguous: boolean;
  local_tempo: { status: 'variation_requires_review' | 'no_large_variation_detected' | 'insufficient_evidence';
    range_bpm: [number, number] | null;
    segments: { start_seconds: number; end_seconds: number; bpm: number | null; periodicity: number }[] };
  warnings: string[];
};
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
  separation_strategy?: SeparationStrategy;
  separation?: { strategy: SeparationStrategy; input_mode: string; instrument_order: Instrument[];
    accuracy_evaluated: false; stems_may_overlap: boolean; additive_residual: boolean };
  audio_preprocessing?: {
    method: string;
    input_channels: number | null;
    used_channel_fallback: boolean;
    selected_channel: number | null;
    diagnostic_status: string;
  };
  analysis_error?: string | null;
  rhythm_analysis?: { bpm: number; first_beat_seconds: number; beat_times: number[]; regularity: number; analyzed_seconds: number; alternatives: number[]; warning: string; evidence?: RhythmEvidence };
  lyric_candidate?: LyricCandidate;
  lyric_guide?: LyricCandidate;
};
export type Health = {
  ok: boolean;
  ready?: boolean;
  engine: { available: boolean; model: string; device: string; issues: string[]; transcription_available: boolean };
  limits: { max_upload_mb: number; max_audio_seconds: number };
  lyrics?: { available: boolean; model: string; device: string; compute_type: string };
  drum_engine?: { configured: boolean; paths_ready: boolean; device: string; warning: string };
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
