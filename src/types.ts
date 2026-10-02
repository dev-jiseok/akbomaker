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
  note_count?: number;
  quiet?: boolean;
};
export type Job = {
  id: string;
  title: string;
  source_type: string;
  demo: boolean;
  status: 'queued' | 'running' | 'separated' | 'transcribing' | 'completed' | 'error' | 'cancelled';
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
};
export type Health = {
  ok: boolean;
  engine: { available: boolean; model: string; device: string; issues: string[]; transcription_available: boolean };
  limits: { max_upload_mb: number; max_audio_seconds: number };
};

export function isProcessing(job: Job | null): boolean {
  return !!job && ['queued', 'running', 'transcribing'].includes(job.status);
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
