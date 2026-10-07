// @vitest-environment jsdom
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { recentProjects, rememberProject, request, upload } from './api';
import type { Health, Job } from './types';

const fixtures = vi.hoisted(() => ({ sourceId: 'a'.repeat(32), audioId: 'b'.repeat(32), imported: null as Job | null }));
vi.mock('./api', () => ({ request: vi.fn(), recentProjects: vi.fn(() => []), rememberProject: vi.fn(), upload: vi.fn() }));
vi.mock('./components/Artwork', () => ({ StudioArtwork: () => <div aria-label="음악 작업실 그림" /> }));
vi.mock('./components/Mixer', () => ({ instrumentIcons: Object.fromEntries(['vocal', 'bass', 'drums', 'synthesizer', 'guitar', 'piano'].map(key => [key, () => null])) }));
vi.mock('./components/ScoreImport', () => ({ default: ({ expanded, disabled, onImported, onDirty }: { expanded?: boolean; disabled: boolean; onImported?: (job: Job) => void; onDirty?: (dirty: boolean) => void }) => <section data-testid="score-import" data-expanded={!!expanded}><button disabled={disabled} onClick={() => onImported?.(fixtures.imported!)}>테스트 원본 악보 열기</button><button onClick={() => onDirty?.(true)}>테스트 TAB 초안 수정</button></section> }));
vi.mock('./components/Workspace', () => ({ default: ({ job, onNew, onEditorDirty }: { job: Job; onNew: () => void; onEditorDirty: (dirty: boolean) => void }) => {
  useEffect(() => () => onEditorDirty(false), [onEditorDirty]);
  return <section data-testid="workspace" data-kind={job.score_preserved ? 'source' : 'audio'}><h1>{job.title}</h1><button onClick={onNew}>테스트 프로젝트 나가기</button><button onClick={() => onEditorDirty(true)}>테스트 미저장 상태</button></section>;
} }));

const health: Health = { ok: true, ready: true, engine: { available: true, model: 'sam', device: 'cuda', issues: [], transcription_available: true }, limits: { max_upload_mb: 200, max_audio_seconds: 600 } };
let sourceJob: Job, audioJob: Job, container: HTMLDivElement, root: Root;
const api = vi.mocked(request);
function nav(text: string) { const button = [...container.querySelectorAll<HTMLButtonElement>('nav[aria-label="주 메뉴"] button')].find(item => item.textContent?.trim() === text); if (!button) throw new Error(`Missing navigation ${text}`); return button; }
function button(text: string) { const result = [...container.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === text); if (!result) throw new Error(`Missing button ${text}`); return result; }
async function click(text: string) { await act(async () => button(text).click()); }
async function navigate(text: string) { await act(async () => nav(text).click()); }
async function mount() { await act(async () => root.render(<App />)); }
async function hashNavigate(hash: string) { await act(async () => { history.replaceState(null, '', hash); window.dispatchEvent(new HashChangeEvent('hashchange')); }); }

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  history.replaceState(null, '', '/');
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  sourceJob = { id: fixtures.sourceId, title: '원본 악보 프로젝트', source_type: 'musicxml', demo: false, status: 'completed', stage: 'completed', progress: 100, message: '', error: null, created_at: '', duration: null, bpm: null, original_url: null, residual_url: null, stems: [], score_preserved: { instrument: 'guitar', part_id: 'P1', revision: 'r1' } };
  audioJob = { ...sourceJob, id: fixtures.audioId, title: '기존 음원 프로젝트', source_type: 'upload', score_preserved: undefined, original_url: '/audio.wav' };
  fixtures.imported = sourceJob;
  api.mockReset(); vi.mocked(recentProjects).mockReset().mockReturnValue([]); vi.mocked(rememberProject).mockReset(); vi.mocked(upload).mockReset();
  api.mockImplementation(async path => {
    if (path === '/api/health') return health;
    if (path === `/api/jobs/${sourceJob.id}`) return sourceJob;
    if (path === `/api/jobs/${audioJob.id}` || path === '/api/demo') return audioJob;
    throw new Error(`Unexpected path: ${path}`);
  });
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  vi.spyOn(window, 'confirm').mockReturnValue(false);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); history.replaceState(null, '', '/'); vi.restoreAllMocks(); });

describe('separate score-style workflow navigation', () => {
  it('protects an unsaved TAB review even before a project exists', async () => {
    history.replaceState(null, '', '#score-style'); await mount(); await click('테스트 TAB 초안 수정');
    await hashNavigate(`#project=${audioJob.id}`);
    expect(location.hash).toBe('#score-style'); expect(container.querySelector('.score-style-page')).not.toBeNull();
    expect(api.mock.calls.some(([path]) => path.startsWith('/api/jobs/'))).toBe(false);
    await navigate('음악 작업실'); expect(location.hash).toBe('#score-style');
    vi.mocked(window.confirm).mockReturnValue(true); await hashNavigate(`#project=${audioJob.id}`);
    expect(location.hash).toBe(`#project=${audioJob.id}`);
    expect(container.querySelector('[data-testid="workspace"]')).not.toBeNull();
  });
  it('offers a dedicated expanded score import page without audio upload or YouTube inputs', async () => {
    await mount(); expect(container.querySelector('[aria-label="음악 파일 선택"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="score-import"]')?.getAttribute('data-expanded')).toBe('false');
    await navigate('악보 스타일 변환');
    expect(location.hash).toBe('#score-style'); expect(container.querySelector('.score-style-page')).not.toBeNull();
    expect(container.querySelector('[data-testid="score-import"]')?.getAttribute('data-expanded')).toBe('true');
    expect(container.querySelector('[aria-label="음악 파일 선택"]')).toBeNull(); expect(container.querySelector('#youtube-url')).toBeNull();
    expect(container.querySelector('[aria-label="음악 가져오기 방식"]')).toBeNull();
    expect(container.textContent).toContain('음원에서 새로 채보하지 않아요');
    expect(vi.mocked(upload)).not.toHaveBeenCalled(); expect(api.mock.calls.map(([path]) => path)).toEqual(['/api/health']);
  });

  it('restores the dedicated score-style route on direct load', async () => {
    history.replaceState(null, '', '#score-style'); await mount();
    expect(container.querySelector('.score-style-page')).not.toBeNull();
    expect(nav('악보 스타일 변환').classList.contains('active')).toBe(true);
    expect(container.querySelector('[data-testid="score-import"]')?.getAttribute('data-expanded')).toBe('true');
    expect(container.querySelector('[aria-label="음악 파일 선택"]')).toBeNull();
    expect(api.mock.calls.some(([path]) => path.startsWith('/api/jobs/'))).toBe(false);
  });

  it('keeps the existing music file and YouTube workflow available after returning home', async () => {
    history.replaceState(null, '', '#score-style'); await mount(); await navigate('음악 작업실');
    expect(location.hash).toBe(''); expect(container.querySelector('.score-style-page')).toBeNull();
    expect(container.querySelector('[aria-label="음악 파일 선택"]')).not.toBeNull();
    expect(container.querySelector('.start-button')?.textContent).toContain('악기 분리 시작');
    await click('YouTube 링크'); expect(container.querySelector('#youtube-url')).not.toBeNull();
    expect(container.querySelector('[data-testid="score-import"]')?.getAttribute('data-expanded')).toBe('false');
  });

  it('returns a new source-score project to the dedicated style page', async () => {
    history.replaceState(null, '', '#score-style'); await mount(); await click('테스트 원본 악보 열기');
    expect(location.hash).toBe(`#project=${sourceJob.id}`); expect(container.querySelector('[data-testid="workspace"]')?.getAttribute('data-kind')).toBe('source');
    expect(rememberProject).toHaveBeenCalledWith(sourceJob); expect(nav('악보 스타일 변환').classList.contains('active')).toBe(true);
    await click('테스트 프로젝트 나가기'); expect(location.hash).toBe('#score-style');
    expect(container.querySelector('.score-style-page')).not.toBeNull(); expect(container.querySelector('[aria-label="음악 파일 선택"]')).toBeNull();
  });

  it('returns an existing audio project to the original music home', async () => {
    history.replaceState(null, '', `#project=${audioJob.id}`); await mount();
    expect(container.querySelector('[data-testid="workspace"]')?.getAttribute('data-kind')).toBe('audio');
    expect(nav('음악 작업실').classList.contains('active')).toBe(true);
    await click('테스트 프로젝트 나가기'); expect(location.hash).toBe('');
    expect(container.querySelector('[aria-label="음악 파일 선택"]')).not.toBeNull(); expect(container.querySelector('.score-style-page')).toBeNull();
  });

  it('preserves the unsaved-work guard for navigation between both workflows', async () => {
    history.replaceState(null, '', `#project=${sourceJob.id}`); await mount(); await click('테스트 미저장 상태');
    await navigate('음악 작업실'); expect(window.confirm).toHaveBeenCalled();
    expect(location.hash).toBe(`#project=${sourceJob.id}`); expect(container.querySelector('[data-testid="workspace"]')).not.toBeNull();
    await navigate('악보 스타일 변환'); expect(container.querySelector('.score-style-page')).toBeNull();
    vi.mocked(window.confirm).mockReturnValue(true); await navigate('악보 스타일 변환');
    expect(location.hash).toBe('#score-style'); expect(container.querySelector('.score-style-page')).not.toBeNull();
  });

  it('blocks dirty hash navigation and restores the current project URL until approved', async () => {
    history.replaceState(null, '', `#project=${audioJob.id}`); await mount(); await click('테스트 미저장 상태');
    await hashNavigate('#score-style');
    expect(location.hash).toBe(`#project=${audioJob.id}`); expect(container.querySelector('[data-testid="workspace"]')).not.toBeNull();
    vi.mocked(window.confirm).mockReturnValue(true); await hashNavigate('#score-style');
    expect(location.hash).toBe('#score-style'); expect(container.querySelector('.score-style-page')).not.toBeNull();
    expect(vi.mocked(upload)).not.toHaveBeenCalled();
  });
});
