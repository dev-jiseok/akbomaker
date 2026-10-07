import type { Instrument, Job, ScoreLayout } from './types';

export type SourceScoreOperation = 'pitch' | 'fingering' | 'tab_pitch' | 'drum' | 'lyric' | 'delete' | 'insert' | 'rhythm' | 'chord' | 'lyric_add' | 'lyric_delete' | 'articulation' | 'connection';
export type SourceConnection = 'tie' | 'slur' | 'slide' | 'hammer-on' | 'pull-off';
export type SourceArticulation = 'accent' | 'staccato' | 'tenuto';
export type ScoreIntegrityIssue = { code: string; severity: 'warning' | 'info'; measure_index: number; measure_number: string; note_id?: string; message: string };
export type ScoreIntegrityReport = { scope: 'musicxml-structure-not-pdf-accuracy'; checked_measures: number; checked_notes: number; total_issues: number; truncated: boolean; issues: ScoreIntegrityIssue[] };
export type SourceNoteKind = 'pitched' | 'unpitched' | 'tab';
export type SourceNoteType = 'whole' | 'half' | 'quarter' | 'eighth' | '16th' | '32nd' | '64th';
export type SourceScoreNote = {
  id: string; measure_index: number; measure_number: string; note_index: number;
  staff: string; voice: string; kind: 'pitched' | 'unpitched' | 'rest'; description: string;
  duration: string; onset: string;
  pitch?: { step: string; alter: number; octave: number };
  fingering?: { string: number; fret: number };
  drum_id?: string;
  lyrics: { index: number; text: string; editable: boolean; reason?: string; deletable?: boolean; delete_reason?: string }[];
  editable: { pitch: boolean; fingering: boolean; drum: boolean } & Partial<Record<Exclude<SourceScoreOperation, 'lyric'>, boolean>>;
  reasons: Partial<Record<SourceScoreOperation, string>>;
  notation?: { type: string; dots: number };
  warnings?: string[];
  fingering_mismatch?: boolean;
  insert_kinds?: SourceNoteKind[]; chord_kinds?: SourceNoteKind[];
  rhythm_limit?: string;
  articulations?: SourceArticulation[];
  connections?: { mark: SourceConnection; target_note_id: string; number?: string }[];
  connection_targets?: { mark: SourceConnection; target_note_id: string }[];
};
export type SourceScoreDocument = {
  id: string; revision: string; title: string; instrument: Instrument; part_id: string;
  layout: ScoreLayout; xml: string; notes: SourceScoreNote[];
  drum_options: { id: string; name: string }[]; warnings: string[];
  source_url: string; current_url: string; original_url: string;
  source_sha256: string; working_sha256: string;
  attachments?: { name: string; url: string; sha256: string }[];
  history: { undo: boolean; redo: boolean };
  integrity_report?: ScoreIntegrityReport;
  content_check?: { style_preserves_music: boolean; music_edited: boolean; current_music_sha256: string; original_music_sha256: string; styled_music_sha256: string; scope: 'selected-part-musicxml-not-pdf-recognition' };
};
export type SourceScorePatch = { note_id: string; operation: SourceScoreOperation; step?: string; alter?: number; octave?: number; string?: number; fret?: number; drum_id?: string; lyric_index?: number; text?: string; kind?: SourceNoteKind; type?: SourceNoteType; dots?: number; mark?: SourceConnection | SourceArticulation; action?: 'add' | 'remove'; target_note_id?: string };
export type SourceScoreResult = { document: SourceScoreDocument; job: Job };

export function sourceScoreUrl(value: string, id: string): string | undefined {
  try {
    const url = new URL(value, window.location.origin);
    if (url.origin !== window.location.origin || url.username || url.password || !url.pathname.startsWith(`/api/source-scores/${id}/`)) return undefined;
    return url.pathname + url.search;
  } catch { return undefined; }
}
