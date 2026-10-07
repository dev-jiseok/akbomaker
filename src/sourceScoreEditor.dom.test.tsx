// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SourceScoreEditor from './components/SourceScoreEditor';
import Workspace from './components/Workspace';
import ScoreImport from './components/ScoreImport';
import { request } from './api';
import type { Instrument, Job, ScoreLayout } from './types';
import { sourceScoreUrl, type SourceArticulation, type SourceConnection, type SourceScoreDocument, type SourceScoreNote, type SourceScoreResult } from './sourceScore';

vi.mock('./api', () => ({ request: vi.fn() }));
vi.mock('./components/ScoreViewer', () => ({ default: ({ xml, preserveNotation, layout, spacious, reviewedTabRhythm }: { xml: string; preserveNotation: boolean; layout?: ScoreLayout; spacious: boolean; reviewedTabRhythm?: boolean }) => <div className="engraving" data-xml={xml} data-preserved={preserveNotation} data-layout={JSON.stringify(layout)} data-spacious={spacious} data-reviewed-tab-rhythm={reviewedTabRhythm}><svg /></div>, EmptyScore: () => <div>빈 악보</div> }));

const id = 'b'.repeat(32);
const note: SourceScoreNote = { id: 'm0n0', measure_index: 1, measure_number: '1', note_index: 1, staff: '1', voice: '1', kind: 'pitched', description: 'C4 · 4분음표', duration: '1', onset: '0', pitch: { step: 'C', alter: 0, octave: 4 }, lyrics: [{ index: 0, text: '가', editable: true }], editable: { pitch: true, fingering: false, drum: false }, reasons: {} };
const second: SourceScoreNote = { ...note, id: 'm1n0', measure_index: 2, measure_number: '2', description: 'D4 · 4분음표', pitch: { step: 'D', alter: 0, octave: 4 } };
let job: Job, doc: SourceScoreDocument, container: HTMLDivElement, root: Root;
const api = vi.mocked(request), onJob = vi.fn(), onNew = vi.fn(), onDirty = vi.fn();
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function button(label: string) { const result = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === label); if (!result) throw new Error(`Missing button ${label}`); return result; }
async function click(label: string) { await act(async () => button(label).click()); }
async function select(label: string, value: string) { const node = container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!; await act(async () => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); }); }
async function input(label: string, value: string) { const node = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!; await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); }
async function mount(openEditor = true) { await act(async () => root.render(<SourceScoreEditor job={job} onJob={onJob} onNew={onNew} onEditorDirty={onDirty} />)); if (openEditor) await click('틀린 부분 수정하기'); }
function response(next: Partial<SourceScoreDocument> = {}): SourceScoreResult { return { document: { ...doc, revision: 'revision-2', history: { undo: true, redo: false }, ...next }, job: { ...job, score_preserved: { ...job.score_preserved!, revision: 'revision-2' } } }; }

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  job = { id, title: '원본 곡', source_type: 'musicxml', demo: false, status: 'completed', stage: 'completed', progress: 100, message: '', error: null, created_at: '', duration: null, bpm: null, original_url: null, residual_url: null, stems: [], score_preserved: { instrument: 'vocal', part_id: 'P1', revision: 'revision-1' } };
  doc = { id, revision: 'revision-1', title: '원본 곡', instrument: 'vocal', part_id: 'P1', layout: { preset: 'practice', measures_per_line: 4, show_numbers: true }, xml: '<score-partwise><part><note><pitch/></note><repeat/></part></score-partwise>', notes: [{ ...note }, { ...second }], drum_options: [], warnings: ['원본 가사와 비교해주세요'], source_url: `/api/source-scores/${id}/files/source.mxl`, current_url: `/api/source-scores/${id}/files/current.musicxml`, original_url: `/api/source-scores/${id}/files/original.musicxml`, source_sha256: 'a'.repeat(64), working_sha256: 'b'.repeat(64), history: { undo: false, redo: false } };
  api.mockReset(); onJob.mockReset(); onNew.mockReset(); onDirty.mockReset();
  api.mockImplementation(async (path) => { if (path === `/api/source-scores/${id}`) return doc; return response(); });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, text: async () => '<score-partwise><original/></score-partwise>' }));
  vi.spyOn(window, 'confirm').mockReturnValue(false);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('original notation editor', () => {
  describe('read-only musical structure checks', () => {
    function report(issues: NonNullable<SourceScoreDocument['integrity_report']>['issues'] = []) {
      doc.integrity_report = { scope: 'musicxml-structure-not-pdf-accuracy', checked_measures: 2, checked_notes: 2, total_issues: issues.length, truncated: false, issues };
    }
    it('never treats zero structural issues as correct PDF recognition', async () => {
      report(); await mount(false);
      expect(container.textContent).toContain('악보 구조 점검 · 확인 항목 0개');
      expect(container.textContent).toContain('원본과 같다는 보증은 아니므로');
      expect(container.textContent).toContain('자동으로 음표를 바꾸지 않아요');
      expect(api).toHaveBeenCalledOnce(); expect(onJob).not.toHaveBeenCalled();
    });
    it('opens the target measure and note without modifying or saving it', async () => {
      report([{ code: 'note-duration-mismatch', severity: 'warning', measure_index: 2, measure_number: '21', note_id: second.id, message: '음표 모양과 재생 길이를 확인해주세요.' }]);
      await mount(false); await click('해당 위치 확인');
      expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 악보 수정할 마디"]')!.value).toBe('2');
      expect(container.querySelector('.source-score-note-button[aria-pressed="true"]')?.textContent).toContain(second.description);
      expect(container.textContent).toContain('21마디 확인:'); expect(api).toHaveBeenCalledOnce(); expect(onDirty).toHaveBeenLastCalledWith(false);
    });
    it('retains unsaved changes when the issue navigation confirmation is declined', async () => {
      report([{ code: 'measure-underfull', severity: 'info', measure_index: 2, measure_number: '2', message: '마디 길이 확인' }]);
      await mount(); await select('원본 음표 이름', 'E'); await click('해당 위치 확인');
      expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 악보 수정할 마디"]')!.value).toBe('1');
      expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 음표 이름"]')!.value).toBe('E');
      expect(onDirty).toHaveBeenLastCalledWith(true); expect(api).toHaveBeenCalledOnce();
    });
    it('labels truncated reports without claiming all issues are listed', async () => {
      report([{ code: 'tab-pitch-unverified', severity: 'info', measure_index: 1, measure_number: '1', message: '명시적 튜닝 없음' }]);
      doc.integrity_report!.total_issues = 250; doc.integrity_report!.truncated = true;
      await mount(false); expect(container.textContent).toContain('전체 250개 중 처음 1개를 표시');
      expect(container.querySelector('.integrity-info')?.textContent).toBe('참고');
    });
  });
  describe('safe articulation and paired-connection controls', () => {
    const nextNote: SourceScoreNote = { ...note, id: 'm0n1', note_index: 2, onset: '1' };
    const laterNote: SourceScoreNote = { ...note, id: 'm0n2', note_index: 3, onset: '2' };
    const selectedValue = (label: string) => container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!.value;
    const values = (label: string) => [...container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!.options].map(option => option.value);
    const markNames = { accent: '악센트', staccato: '스타카토', tenuto: '테누토' };

    it.each<{ mark: SourceArticulation; action: 'add' | 'remove' }>(
      (['accent', 'staccato', 'tenuto'] as const).flatMap(mark => (['add', 'remove'] as const).map(action => ({ mark, action }))),
    )('sends only explicit $mark $action without changing pitch, timing, lyrics or source XML', async ({ mark, action }) => {
      doc.notes = [{ ...note, articulations: action === 'remove' ? [mark] : [], editable: { ...note.editable, articulation: true } }, nextNote];
      const xmlBefore = doc.xml;
      await mount(); await select('원본 음표 수정 항목', 'articulation');
      expect(values('원본 연주 기호')).toEqual(['accent', 'staccato', 'tenuto']);
      expect(container.textContent).toContain(`현재 기호: ${action === 'remove' ? markNames[mark] : '없음'}`);
      await select('원본 연주 기호', mark); await select('원본 기호 처리', action);
      expect(container.querySelector('[aria-label="원본 기호 끝 음표"]')).toBeNull();
      expect(container.querySelector('[aria-label="원본 음표 이름"]')).toBeNull();
      await click('선택한 수정 저장');
      const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
      expect(JSON.parse(call[1]!.body as string)).toEqual({ base_revision: 'revision-1', patch: { note_id: note.id, operation: 'articulation', mark, action } });
      expect(window.confirm).not.toHaveBeenCalled();
      expect(container.querySelector('.source-score-paper .engraving')?.getAttribute('data-xml')).toBe(xmlBefore);
      expect(onDirty).toHaveBeenLastCalledWith(false);
    });

    it.each<Instrument>(['vocal', 'drums', 'piano', 'synthesizer', 'guitar', 'bass'])('uses the server articulation capability for %s rather than inferring instrumental notes', async instrument => {
      doc.instrument = instrument;
      doc.notes = [{ ...note, kind: instrument === 'drums' ? 'unpitched' : 'pitched', editable: { pitch: false, drum: false, fingering: false, articulation: true }, articulations: [] }];
      await mount();
      expect(selectedValue('원본 음표 수정 항목')).toBe('articulation');
      await select('원본 연주 기호', 'tenuto'); await click('선택한 수정 저장');
      expect(JSON.parse(api.mock.calls.find(([path]) => path.endsWith('/edit'))![1]!.body as string).patch).toEqual({ note_id: note.id, operation: 'articulation', mark: 'tenuto', action: 'add' });
    });

    it.each<{ mark: SourceConnection; action: 'add' | 'remove' }>(
      (['tie', 'slur', 'slide', 'hammer-on', 'pull-off'] as const).flatMap(mark => (['add', 'remove'] as const).map(action => ({ mark, action }))),
    )('sends the server-authorized $mark $action pair only after confirmation', async ({ mark, action }) => {
      doc.instrument = ['slide', 'hammer-on', 'pull-off'].includes(mark) ? 'guitar' : 'vocal';
      const pair = { mark, target_note_id: nextNote.id };
      doc.notes = [{ ...note, editable: { ...note.editable, connection: true },
        connection_targets: action === 'add' ? [pair] : [], connections: action === 'remove' ? [{ ...pair, number: '3' }] : [] }, nextNote];
      await mount(); await select('원본 음표 수정 항목', 'connection');
      expect(selectedValue('원본 기호 처리')).toBe(action);
      expect(values('원본 연주 기호')).toEqual([mark]);
      expect(selectedValue('원본 기호 끝 음표')).toBe(nextNote.id);
      expect(container.querySelector('[aria-label="원본 기호 끝 음표"]')?.textContent).toContain('1마디 · 2번째');
      await click('선택한 수정 저장');
      expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('선택한 두 음표의 연결 기호'));
      expect(api.mock.calls.some(([path]) => path.endsWith('/edit'))).toBe(false);
      expect(onDirty).toHaveBeenLastCalledWith(true);
      vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
      const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
      expect(JSON.parse(call[1]!.body as string)).toEqual({ base_revision: 'revision-1', patch: { note_id: note.id, operation: 'connection', mark, action, target_note_id: nextNote.id } });
      expect(container.textContent).toContain('음정·음 길이를 자동으로 바꾸지 않습니다');
      expect(onJob).toHaveBeenCalledOnce(); expect(onDirty).toHaveBeenLastCalledWith(false);
    });

    it('resets targets on mark/action changes and does not reuse a target from another pair', async () => {
      doc.notes = [{ ...note, editable: { ...note.editable, connection: true },
        connection_targets: [{ mark: 'tie', target_note_id: nextNote.id }, { mark: 'slur', target_note_id: laterNote.id }, { mark: 'slur', target_note_id: second.id }],
        connections: [{ mark: 'slur', target_note_id: nextNote.id, number: '2' }, { mark: 'slide', target_note_id: laterNote.id, number: '1' }] }, nextNote, laterNote, second];
      await mount(); await select('원본 음표 수정 항목', 'connection');
      expect(selectedValue('원본 연주 기호')).toBe('tie');
      expect(values('원본 기호 끝 음표')).toEqual([nextNote.id]);
      await select('원본 연주 기호', 'slur');
      expect(selectedValue('원본 기호 끝 음표')).toBe(laterNote.id);
      expect(values('원본 기호 끝 음표')).toEqual([laterNote.id, second.id]);
      await select('원본 기호 끝 음표', second.id);
      expect(selectedValue('원본 기호 끝 음표')).toBe(second.id);
      await select('원본 기호 처리', 'remove');
      expect(selectedValue('원본 연주 기호')).toBe('slur');
      expect(selectedValue('원본 기호 끝 음표')).toBe(nextNote.id);
      expect(values('원본 기호 끝 음표')).toEqual([nextNote.id]);
      await select('원본 연주 기호', 'slide');
      expect(selectedValue('원본 기호 끝 음표')).toBe(laterNote.id);
      await select('원본 기호 처리', 'add');
      expect(selectedValue('원본 연주 기호')).toBe('tie');
      expect(selectedValue('원본 기호 끝 음표')).toBe(nextNote.id);
      vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
      expect(JSON.parse(api.mock.calls.find(([path]) => path.endsWith('/edit'))![1]!.body as string).patch).toEqual({ note_id: note.id, operation: 'connection', action: 'add', mark: 'tie', target_note_id: nextNote.id });
    });

    it('submits a manually selected safe target instead of the first suggested target', async () => {
      doc.notes = [{ ...note, editable: { ...note.editable, connection: true },
        connection_targets: [{ mark: 'slur', target_note_id: nextNote.id }, { mark: 'slur', target_note_id: laterNote.id }], connections: [] }, nextNote, laterNote];
      await mount(); await select('원본 음표 수정 항목', 'connection'); await select('원본 기호 끝 음표', laterNote.id);
      vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
      expect(JSON.parse(api.mock.calls.find(([path]) => path.endsWith('/edit'))![1]!.body as string).patch.target_note_id).toBe(laterNote.id);
    });

    it.each(['missing-capability-target', 'empty-removal-list', 'cleared-target'] as const)('blocks %s before confirmation and HTTP mutation', async scenario => {
      doc.notes = [{ ...note, editable: { ...note.editable, connection: true },
        connection_targets: scenario === 'missing-capability-target' ? [] : [{ mark: 'tie', target_note_id: nextNote.id }], connections: [] }, nextNote];
      await mount(); await select('원본 음표 수정 항목', 'connection');
      if (scenario === 'empty-removal-list') await select('원본 기호 처리', 'remove');
      if (scenario === 'cleared-target') await select('원본 기호 끝 음표', '');
      vi.mocked(window.confirm).mockClear(); await click('선택한 수정 저장');
      expect(window.confirm).not.toHaveBeenCalled();
      expect(api.mock.calls.some(([path]) => path.endsWith('/edit'))).toBe(false);
      expect(container.textContent).toContain('안전하게 연결할 수 있는 기호와 끝 음표를 선택해주세요');
    });

    it('hides unsafe mark operations while showing the backend protection reasons', async () => {
      doc.notes = [{ ...note, editable: { ...note.editable, articulation: false, connection: false },
        reasons: { articulation: '오선·TAB 대응이 불명확해요', connection: '복잡한 연결 쌍은 원본을 유지합니다' } }];
      await mount();
      expect(values('원본 음표 수정 항목')).not.toContain('articulation');
      expect(values('원본 음표 수정 항목')).not.toContain('connection');
      expect(container.textContent).toContain('오선·TAB 대응이 불명확해요');
      expect(container.textContent).toContain('복잡한 연결 쌍은 원본을 유지합니다');
    });

    it('retains a pending paired mark after a stale-save conflict and rejected navigation', async () => {
      doc.notes = [{ ...note, editable: { ...note.editable, connection: true }, connection_targets: [{ mark: 'slur', target_note_id: nextNote.id }, { mark: 'slur', target_note_id: laterNote.id }] }, nextNote, laterNote, second];
      api.mockImplementation(async path => { if (path.endsWith('/edit')) throw new Error('다른 화면에서 연결 기호가 바뀌었어요'); return doc; });
      await mount(); await select('원본 음표 수정 항목', 'connection'); await select('원본 기호 끝 음표', laterNote.id);
      await select('원본 악보 수정할 마디', '2');
      expect(selectedValue('원본 악보 수정할 마디')).toBe('1');
      expect(selectedValue('원본 기호 끝 음표')).toBe(laterNote.id);
      vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
      expect(container.textContent).toContain('다른 화면에서 연결 기호가 바뀌었어요');
      expect(selectedValue('원본 연주 기호')).toBe('slur');
      expect(selectedValue('원본 기호 끝 음표')).toBe(laterNote.id);
      expect(onDirty).toHaveBeenLastCalledWith(true);
      expect(onJob).not.toHaveBeenCalled();
    });

    it('renders saved marks and undoes with the returned revision without rewriting original XML', async () => {
      doc.notes = [{ ...note, editable: { ...note.editable, connection: true }, connection_targets: [{ mark: 'tie', target_note_id: nextNote.id }], connections: [] }, nextNote];
      const savedXml = '<score-partwise><part><note><tie type="start"/></note><repeat/></part></score-partwise>';
      const savedNotes: SourceScoreNote[] = [{ ...doc.notes[0], connection_targets: [], connections: [{ mark: 'tie', target_note_id: nextNote.id, number: '1' }] }, nextNote];
      api.mockImplementation(async path => path.endsWith('/edit') ? response({ xml: savedXml, notes: savedNotes }) : path.endsWith('/history') ? response({ revision: 'revision-3', history: { undo: false, redo: true } }) : doc);
      await mount(); await select('원본 음표 수정 항목', 'connection');
      vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
      expect(container.querySelector('.source-score-paper .engraving')?.getAttribute('data-xml')).toBe(savedXml);
      expect(selectedValue('원본 기호 처리')).toBe('remove');
      expect(button('실행 취소').disabled).toBe(false);
      await click('실행 취소');
      const history = api.mock.calls.find(([path]) => path.endsWith('/history'))!;
      expect(JSON.parse(history[1]!.body as string)).toEqual({ base_revision: 'revision-2', action: 'undo' });
      expect(container.querySelector('.source-score-paper .engraving')?.getAttribute('data-xml')).toBe(doc.xml);
      expect(selectedValue('원본 기호 처리')).toBe('add');
      expect(button('다시 실행').disabled).toBe(false);
      expect(onDirty).toHaveBeenLastCalledWith(false);
      expect(api.mock.calls.some(([path]) => path.endsWith('/layout'))).toBe(false);
    });
  });

  it('opens as a style/output workflow with editing optional and collapsed', async () => {
    await mount(false);
    expect(container.querySelector('.source-score-controls')).toBeNull();
    expect(container.querySelector('.source-editor-closed')).not.toBeNull();
    expect(button('틀린 부분 수정하기').getAttribute('aria-expanded')).toBe('false');
    expect(container.querySelector('.source-score-paper .engraving')).not.toBeNull();
    expect(container.querySelector('.source-score-paper .engraving')?.getAttribute('data-reviewed-tab-rhythm')).toBe('false');
    expect(button('인쇄 / PDF 저장').disabled).toBe(false);
    await click('틀린 부분 수정하기'); expect(container.querySelector('.source-score-controls')).not.toBeNull();
    await select('원본 음표 이름', 'D'); await click('수정 도구 접기');
    expect(onDirty).toHaveBeenLastCalledWith(true); expect(button('인쇄 / PDF 저장').disabled).toBe(true);
    await click('틀린 부분 수정하기'); expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 음표 이름"]')!.value).toBe('D');
  });

  it('shows a scoped content-preservation check only when the server has verified it', async () => {
    doc.content_check = { style_preserves_music: true, music_edited: false, current_music_sha256: 'a', original_music_sha256: 'a', styled_music_sha256: 'a', scope: 'selected-part-musicxml-not-pdf-recognition' };
    await mount(false);
    expect(container.querySelector('.source-fidelity-status')?.textContent).toContain('스타일 변경 전후 음악 데이터 일치 확인');
    expect(container.textContent).toContain('가져온 MusicXML의 음악 내용 유지'); expect(container.textContent).toContain('PDF 인식 정확도 판정은 아닙니다');
  });
  it('enables rhythm display for reviewed TAB only and distinguishes the original PDF from generated XML', async () => {
    job.score_tab_review = { id: 'a'.repeat(32), source_sha256: 'a'.repeat(64), coordinate_sha256: 'b'.repeat(64), review_sha256: 'c'.repeat(64), method: 'user-reviewed-pdf-tab', warnings: [] };
    doc.attachments = [{ name: 'reference.pdf', url: `/api/source-scores/${id}/files/reference.pdf`, sha256: 'a'.repeat(64) }];
    await mount(false);
    expect(container.querySelector('.source-score-paper .engraving')?.getAttribute('data-reviewed-tab-rhythm')).toBe('true');
    expect(container.textContent).toContain('보관된 원본 PDF');
    expect(container.textContent).toContain('검토 후 생성한 최초 MusicXML');
    expect(container.textContent).toContain('원본 PDF 전체를 자동으로 복원한 결과는 아닙니다');
  });
  it.each<Instrument>(['drums', 'bass', 'guitar', 'piano', 'vocal', 'synthesizer'])('routes %s source projects away from lossy grid/transcription workspaces', async instrument => {
    job.score_preserved!.instrument = instrument; doc.instrument = instrument;
    await act(async () => root.render(<Workspace job={job} health={null} onJob={onJob} onNew={onNew} onError={vi.fn()} onEditorDirty={onDirty} />));
    expect(container.querySelector('[aria-label="원본 악보 보존 편집기"]')).not.toBeNull();
    expect(container.querySelector('[data-preserved="true"]')).not.toBeNull();
    expect(container.querySelector('audio')).toBeNull(); expect(container.querySelector('.note-grid')).toBeNull();
    expect(api.mock.calls.map(([path]) => path)).toEqual([`/api/source-scores/${id}`]);
  });

  it('sends only the explicit pitch patch and base revision while preserving the original XML view', async () => {
    await mount(); await select('원본 음표 이름', 'D'); expect(onDirty).toHaveBeenLastCalledWith(true);
    await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ base_revision: 'revision-1', patch: { note_id: 'm0n0', operation: 'pitch', step: 'D', alter: 0, octave: 4 } });
    expect(container.querySelector('.engraving')?.getAttribute('data-xml')).toContain('<repeat/>');
    expect(onJob).toHaveBeenCalledOnce(); expect(onDirty).toHaveBeenLastCalledWith(false);
    expect(button('실행 취소').disabled).toBe(false);
  });

  it('sends original TAB string/fret without auto-assignment or timing reconstruction', async () => {
    doc.instrument = 'bass'; doc.notes = [{ ...note, fingering: { string: 2, fret: 3 }, editable: { pitch: false, fingering: true, drum: false } }];
    await mount(); await input('원본 TAB 프렛', '5'); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'fingering', string: 2, fret: 5 });
  });

  it('changes TAB fret and sounding pitch only after explicit confirmation', async () => {
    doc.instrument = 'guitar'; doc.notes = [{ ...note, fingering: { string: 1, fret: 0 }, editable: { pitch: false, fingering: true, drum: false, tab_pitch: true } }];
    await mount(); expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 음표 수정 항목"]')!.value).toBe('tab_pitch');
    await input('원본 TAB 프렛', '5'); await click('선택한 수정 저장');
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('음정도 변경'));
    expect(api.mock.calls.some(([path]) => path.endsWith('/edit'))).toBe(false); expect(onDirty).toHaveBeenLastCalledWith(true);
    vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ base_revision: 'revision-1', patch: { note_id: 'm0n0', operation: 'tab_pitch', string: 1, fret: 5 } });
    expect(container.textContent).toContain('명확하게 연결된 오선·TAB');
  });

  it('makes source TAB mismatches visible before repair and explains protective accidental signs', async () => {
    doc.instrument = 'guitar'; doc.notes = [{ ...note, fingering: { string: 1, fret: 7 }, editable: { pitch: false, fingering: false, drum: false, tab_pitch: true }, warnings: ['현재 TAB 숫자와 음정이 다릅니다. 새로 입력한 줄·프렛을 기준으로 음정과 연결 오선을 함께 수정합니다.'] }];
    await mount();
    const warning = container.querySelector('.source-note-warnings')!;
    expect(warning.textContent).toContain('현재 TAB 숫자와 음정이 다릅니다');
    expect(warning.closest('details')).toBeNull();
    expect(container.textContent).toContain('후행 임시표(♯·♭·♮)가 명시될 수 있어요');
    expect(api.mock.calls.some(([path]) => path.endsWith('/edit'))).toBe(false);
  });

  it('can explicitly repair encoded pitch from an unchanged TAB fingering when the server reports a mismatch', async () => {
    doc.instrument = 'guitar'; doc.notes = [{ ...note, fingering: { string: 1, fret: 7 }, fingering_mismatch: true, editable: { pitch: false, fingering: false, drum: false, tab_pitch: true }, warnings: ['현재 TAB 숫자와 음정이 다릅니다.'] }];
    await mount(); expect(onDirty).toHaveBeenLastCalledWith(false); expect(button('선택한 수정 저장').disabled).toBe(false);
    await click('선택한 수정 저장'); expect(api.mock.calls.some(([path]) => path.endsWith('/edit'))).toBe(false);
    vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'tab_pitch', string: 1, fret: 7 });
  });

  it('deletes a note by explicitly replacing it with a same-length rest, not shortening the bar', async () => {
    doc.notes = [{ ...note, editable: { ...note.editable, delete: true } }];
    await mount(); await select('원본 음표 수정 항목', 'delete');
    expect(container.textContent).toContain('음표를 같은 길이의 쉼표로 바꿉니다');
    expect(onDirty).toHaveBeenLastCalledWith(true); await click('선택한 수정 저장');
    expect(api.mock.calls.some(([path]) => path.endsWith('/edit'))).toBe(false);
    vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'delete' });
  });

  it.each<Instrument>(['piano', 'synthesizer', 'vocal'])('inserts a pitched note in a %s rest without requiring a fake initial edit', async instrument => {
    doc.instrument = instrument; doc.notes = [{ ...note, kind: 'rest', pitch: undefined, lyrics: [], editable: { pitch: false, fingering: false, drum: false, insert: true }, insert_kinds: ['pitched'] }];
    vi.mocked(window.confirm).mockReturnValue(true); await mount();
    expect(button('선택한 수정 저장').disabled).toBe(false); expect(onDirty).toHaveBeenLastCalledWith(false);
    await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'insert', kind: 'pitched', step: 'C', alter: 0, octave: 4 });
  });

  it('inserts a declared drum sound in an existing rest', async () => {
    doc.instrument = 'drums'; doc.drum_options = [{ id: 'hh', name: '하이햇' }, { id: 'kick', name: '킥' }];
    doc.notes = [{ ...note, kind: 'rest', pitch: undefined, lyrics: [], editable: { pitch: false, fingering: false, drum: false, insert: true }, insert_kinds: ['unpitched'] }];
    await mount(); await select('원본 드럼 타격 종류', 'kick'); vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'insert', kind: 'unpitched', drum_id: 'kick' });
  });

  it.each<Instrument>(['guitar', 'bass'])('inserts a TAB note using %s source tuning rather than fabricated pitch', async instrument => {
    doc.instrument = instrument; doc.notes = [{ ...note, kind: 'rest', pitch: undefined, lyrics: [], editable: { pitch: false, fingering: false, drum: false, insert: true }, insert_kinds: ['tab'] }];
    await mount(); await input('원본 TAB 줄', '2'); await input('원본 TAB 프렛', '3'); vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'insert', kind: 'tab', string: 2, fret: 3 });
  });

  it('sends an explicit rhythmic type and dots while explaining adjacent-rest handling', async () => {
    doc.notes = [{ ...note, notation: { type: 'quarter', dots: 0 }, rhythm_limit: '2', editable: { ...note.editable, rhythm: true } }];
    await mount(); await select('원본 음표 수정 항목', 'rhythm');
    expect(button('선택한 수정 저장').disabled).toBe(true);
    await select('원본 음표 길이', 'eighth'); await select('원본 음표 점', '1'); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'rhythm', type: 'eighth', dots: 1 });
    expect(container.textContent).toContain('인접한 쉼표를 함께 조절'); expect(container.textContent).toContain('최대 2박');
  });

  it('adds a simultaneous chord note with an explicit kind and confirmation', async () => {
    doc.notes = [{ ...note, editable: { ...note.editable, chord: true }, chord_kinds: ['pitched'] }];
    await mount(); await select('원본 음표 수정 항목', 'chord'); await select('원본 음표 이름', 'E');
    vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'chord', kind: 'pitched', step: 'E', alter: 0, octave: 4 });
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('동시에 울리는 음'));
  });

  it('adds a new lyric instead of overwriting an existing verse and rejects blank input', async () => {
    doc.notes = [{ ...note, editable: { ...note.editable, lyric_add: true } }];
    await mount(); await select('원본 음표 수정 항목', 'lyric_add');
    expect(container.querySelector<HTMLInputElement>('[aria-label="원본 가사 내용"]')!.value).toBe('');
    await click('선택한 수정 저장'); expect(container.textContent).toContain('가사를 입력해주세요');
    expect(api.mock.calls.some(([path]) => path.endsWith('/edit'))).toBe(false);
    await input('원본 가사 내용', '새 가사'); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'lyric_add', text: '새 가사' });
  });

  it('offers deletion only for independently deletable lyrics and sends only their index', async () => {
    doc.notes = [{ ...note, editable: { ...note.editable, lyric_delete: true }, lyrics: [{ index: 0, text: '늘이는 가사', editable: true, deletable: false, delete_reason: '연결된 가사' }, { index: 1, text: '삭제 가능', editable: true, deletable: true }] }];
    await mount(); await select('원본 음표 수정 항목', 'lyric_delete');
    const items = container.querySelector<HTMLSelectElement>('[aria-label="원본 가사 항목"]')!;
    expect(items.options).toHaveLength(1); expect(items.value).toBe('1'); expect(container.textContent).toContain('연결된 가사');
    vi.mocked(window.confirm).mockReturnValue(true); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'lyric_delete', lyric_index: 1 });
  });

  it('does not expose unsupported structural edits or insert types without safe capabilities', async () => {
    doc.notes = [{ ...note, editable: { ...note.editable, insert: true, chord: false, delete: false, lyric_delete: true }, insert_kinds: [], reasons: { chord: '복잡한 성부라 동시음 추가 불가' } }];
    await mount();
    const operations = [...container.querySelector<HTMLSelectElement>('[aria-label="원본 음표 수정 항목"]')!.options].map(item => item.value);
    expect(operations).not.toContain('delete'); expect(operations).not.toContain('insert'); expect(operations).not.toContain('chord'); expect(operations).not.toContain('lyric_delete');
    expect(container.textContent).toContain('복잡한 성부라 동시음 추가 불가');
  });

  it('edits only declared drum instruments and retains read-only reasons for unsafe edits', async () => {
    doc.instrument = 'drums'; doc.notes = [{ ...note, kind: 'unpitched', pitch: undefined, drum_id: 'hh', editable: { pitch: false, fingering: false, drum: true }, reasons: { pitch: '드럼은 음정 대신 악기를 수정합니다' } }]; doc.drum_options = [{ id: 'hh', name: '닫힌 하이햇' }, { id: 'snare', name: '스네어' }];
    await mount(); await select('원본 드럼 타격 종류', 'snare'); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'drum', drum_id: 'snare' });
    expect(container.textContent).toContain('드럼은 음정 대신 악기를 수정합니다');
  });

  it('saves the selected lyric index and does not offer blocked lyric structures', async () => {
    doc.notes = [{ ...note, lyrics: [{ index: 0, text: '복잡한 가사', editable: false, reason: '결합 가사는 보존합니다' }, { index: 1, text: '두번째', editable: true }] }];
    await mount(); await select('원본 음표 수정 항목', 'lyric');
    expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 가사 항목"]')!.options).toHaveLength(1);
    await input('원본 가사 내용', '수정된 가사'); await click('선택한 수정 저장');
    const call = api.mock.calls.find(([path]) => path.endsWith('/edit'))!;
    expect(JSON.parse(call[1]!.body as string).patch).toEqual({ note_id: 'm0n0', operation: 'lyric', lyric_index: 1, text: '수정된 가사' });
    expect(container.textContent).toContain('결합 가사는 보존합니다');
  });

  it('keeps pending note changes across rejected navigation and failed stale saves', async () => {
    api.mockImplementation(async path => { if (path.endsWith('/edit')) throw new Error('최신 악보 버전과 충돌했어요. 다시 열어주세요.'); return doc; });
    await mount(); await select('원본 음표 이름', 'E'); await select('원본 악보 수정할 마디', '2');
    expect(window.confirm).toHaveBeenCalled(); expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 악보 수정할 마디"]')!.value).toBe('1');
    await click('선택한 수정 저장'); expect(container.textContent).toContain('최신 악보 버전과 충돌');
    expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 음표 이름"]')!.value).toBe('E'); expect(onDirty).toHaveBeenLastCalledWith(true);
    await click('작업실 홈'); expect(onNew).not.toHaveBeenCalled();
    await click('입력 취소'); await click('작업실 홈'); expect(onNew).toHaveBeenCalledOnce();
  });

  it('deduplicates saves and disables selection until a mutation completes', async () => {
    const pending = deferred<SourceScoreResult>(); api.mockImplementation(path => path.endsWith('/edit') ? pending.promise : Promise.resolve(doc));
    await mount(); await select('원본 음표 이름', 'D');
    await act(async () => { button('선택한 수정 저장').click(); button('선택한 수정 저장').click(); });
    expect(api.mock.calls.filter(([path]) => path.endsWith('/edit'))).toHaveLength(1);
    expect(container.querySelector<HTMLSelectElement>('[aria-label="원본 악보 수정할 마디"]')!.disabled).toBe(true);
    await act(async () => pending.resolve(response())); expect(button('작업실 홈').disabled).toBe(false);
  });

  it('saves layout separately and uses the updated revision for undo', async () => {
    api.mockImplementation(async path => path.endsWith('/layout') ? response({ layout: { ...doc.layout, preset: 'large', measures_per_line: 2 } }) : path.endsWith('/history') ? response() : doc);
    await mount(); await select('원본 보존 출력 스타일', 'large'); await select('원본 보존 한 줄 마디', '2');
    await click('스타일 저장');
    const layout = api.mock.calls.find(([path]) => path.endsWith('/layout'))!;
    expect(layout[1]!.method).toBe('PUT'); expect(JSON.parse(layout[1]!.body as string)).toEqual({ base_revision: 'revision-1', preset: 'large', measures_per_line: 2 });
    await click('실행 취소'); const history = api.mock.calls.find(([path]) => path.endsWith('/history'))!;
    expect(JSON.parse(history[1]!.body as string)).toEqual({ base_revision: 'revision-2', action: 'undo' });
  });

  it('fetches only same-project original MusicXML and scopes printing to the saved score', async () => {
    await mount(); const details = container.querySelector<HTMLDetailsElement>('.source-score-compare')!;
    await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); });
    expect(fetch).toHaveBeenCalledWith(doc.original_url, expect.objectContaining({ cache: 'no-store' }));
    expect(container.querySelectorAll('.engraving')).toHaveLength(2);
    const print = vi.spyOn(window, 'print').mockImplementation(() => {
      expect(document.body.hasAttribute('data-score-preserve-print')).toBe(true);
      expect(container.querySelector('.source-score-paper')?.hasAttribute('data-score-preserve-target')).toBe(true);
      expect(details.hasAttribute('data-score-preserve-target')).toBe(false);
    });
    await click('인쇄 / PDF 저장'); expect(print).toHaveBeenCalledOnce();
    await act(async () => window.dispatchEvent(new Event('afterprint')));
    expect(document.body.hasAttribute('data-score-preserve-print')).toBe(false);
  });

  it('aborts a save on unmount without applying a late project update', async () => {
    const pending = deferred<SourceScoreResult>(); api.mockImplementation(path => path.endsWith('/edit') ? pending.promise : Promise.resolve(doc));
    await mount(); await select('원본 음표 이름', 'F'); await click('선택한 수정 저장');
    const signal = api.mock.calls.find(([path]) => path.endsWith('/edit'))![1]!.signal;
    await act(async () => root.render(null)); expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve(response())); expect(onJob).not.toHaveBeenCalled();
  });

  it('refreshes the original comparison after layout saves and undo with matching viewer layout', async () => {
    api.mockImplementation(async path => path.endsWith('/layout') ? response({ layout: { ...doc.layout, preset: 'standard', measures_per_line: 2 } }) : path.endsWith('/history') ? response() : doc);
    await mount(); const details = container.querySelector<HTMLDetailsElement>('.source-score-compare')!;
    await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); });
    expect(fetch).toHaveBeenCalledTimes(1);
    await select('원본 보존 출력 스타일', 'standard'); await select('원본 보존 한 줄 마디', '2'); await click('스타일 저장');
    expect(fetch).toHaveBeenCalledTimes(2);
    const original = () => details.querySelector('.engraving')!;
    expect(JSON.parse(original().getAttribute('data-layout')!)).toEqual({ ...doc.layout, preset: 'standard', measures_per_line: 2 });
    expect(original().getAttribute('data-spacious')).toBe('false');
    await click('실행 취소'); expect(fetch).toHaveBeenCalledTimes(3);
    expect(JSON.parse(original().getAttribute('data-layout')!)).toEqual(doc.layout);
    expect(original().getAttribute('data-spacious')).toBe('true');
  });

  it('does not fetch a hidden stale comparison until reopened', async () => {
    api.mockImplementation(async path => path.endsWith('/layout') ? response({ layout: { ...doc.layout, preset: 'large' } }) : doc);
    await mount(); const details = container.querySelector<HTMLDetailsElement>('.source-score-compare')!;
    await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); });
    await act(async () => { details.open = false; details.dispatchEvent(new Event('toggle')); });
    await select('원본 보존 출력 스타일', 'large'); await click('스타일 저장');
    expect(fetch).toHaveBeenCalledTimes(1); expect(details.querySelector('.engraving')).toBeNull();
    await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(details.querySelector('.engraving')!.getAttribute('data-layout')!).preset).toBe('large');
  });

  it('aborts an old-layout original request and ignores its late response', async () => {
    const pending = deferred<Response>();
    vi.mocked(fetch).mockImplementationOnce(() => pending.promise);
    api.mockImplementation(async path => path.endsWith('/layout') ? response({ layout: { ...doc.layout, preset: 'large' } }) : doc);
    await mount(); const details = container.querySelector<HTMLDetailsElement>('.source-score-compare')!;
    await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); });
    const signal = vi.mocked(fetch).mock.calls[0][1]!.signal;
    await select('원본 보존 출력 스타일', 'large'); await click('스타일 저장');
    expect(signal?.aborted).toBe(true); expect(fetch).toHaveBeenCalledTimes(2);
    await act(async () => pending.resolve({ ok: true, text: async () => '<old-layout/>' } as Response));
    expect(details.querySelector('.engraving')!.getAttribute('data-xml')).toContain('<original/>');
    expect(details.textContent).not.toContain('불러오는 중');
  });

  it('rejects external or cross-project URLs', () => {
    expect(sourceScoreUrl(doc.original_url, id)).toBe(doc.original_url);
    for (const url of ['https://other.example/file.musicxml', '/api/source-scores/other/files/source.xml', `/api/source-scores/${id}/../other/files/source.xml`, 'javascript:alert(1)']) expect(sourceScoreUrl(url, id)).toBeUndefined();
  });

  it('offers same-project saved note data without downloading uncommitted edits', async () => {
    await mount(); const link = [...container.querySelectorAll('a')].find(item => item.textContent === '저장된 음표 데이터 JSON')!;
    expect(link.getAttribute('href')).toBe(`/api/source-scores/${id}/files/notes.json`);
    expect(link.hasAttribute('download')).toBe(true);
    await select('원본 음표 이름', 'D');
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    await act(async () => { expect(link.dispatchEvent(event)).toBe(false); });
    expect(container.textContent).toContain('음표 데이터에는 저장된 내용만 포함됩니다');
  });

  it('makes preserved import the primary path and sends its selected part, instrument, and style', async () => {
    api.mockImplementation(async path => path === '/api/score-import/inspect' ? { title: '원본', parts: [{ id: 'P1', name: 'Guitar', measures: 4 }] } : job);
    await act(async () => root.render(<ScoreImport disabled={false} onImported={onJob} />));
    const file = container.querySelector<HTMLInputElement>('[aria-label="MusicXML 악보 파일 선택"]')!;
    Object.defineProperty(file, 'files', { configurable: true, value: [new File(['xml'], 'original.musicxml')] });
    await act(async () => file.dispatchEvent(new Event('change', { bubbles: true })));
    await select('가져올 악기', 'guitar'); await select('미리보기 스타일', 'large'); await select('미리보기 한 줄 마디', '2');
    await click('원본 유지 프로젝트 열기');
    const body = api.mock.calls.find(([path]) => path === '/api/source-scores')![1]!.body as FormData;
    expect(body.get('part_id')).toBe('P1'); expect(body.get('instrument')).toBe('guitar'); expect(body.get('preset')).toBe('large'); expect(body.get('measures_per_line')).toBe('2');
    expect(api.mock.calls.some(([path]) => path === '/api/score-import')).toBe(false); expect(onJob).toHaveBeenCalledWith(job);
  });
});
