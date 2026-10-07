// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ScoreViewer from './components/ScoreViewer';
import { enableReviewedTabRhythm } from './tabRhythmRendering';

vi.mock('./tabRhythmRendering', () => ({ enableReviewedTabRhythm: vi.fn() }));

const displays = vi.hoisted(() => [] as { options: Record<string, unknown>; rules: Record<string, unknown> }[]);
vi.mock('opensheetmusicdisplay', () => ({ OpenSheetMusicDisplay: class {
  EngravingRules: Record<string, unknown> = {};
  Zoom = 1;
  constructor(_host: HTMLElement, options: Record<string, unknown>) { displays.push({ options, rules: this.EngravingRules }); }
  async load() {}
  render() {}
} }));
let container: HTMLDivElement, root: Root;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  displays.length = 0; container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  vi.mocked(enableReviewedTabRhythm).mockReset();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
it('keeps existing generated-score beaming but disables generated beams in raw notation previews', async () => {
  const stem = { id: 'guitar' as const, label: '기타', status: 'ready' as const, score_status: 'ready' as const, waveform: [] };
  const render = async (preserveNotation: boolean) => act(async () => root.render(<ScoreViewer stem={stem} scale={1} spacious measureNumbers xml="<score-partwise/>" preserveNotation={preserveNotation} />));
  await render(false);
  expect(displays.at(-1)?.options.autoBeam).toBe(true); expect(displays.at(-1)?.rules.TabBeamsRendered).toBe(false);
  expect(displays.at(-1)?.rules.TabTimeSignatureRendered).toBe(false);
  expect(displays.at(-1)?.options.stretchLastSystemLine).toBe(false);
  await render(true);
  expect(displays.at(-1)?.options.autoBeam).toBe(false); expect(displays.at(-1)?.rules.AutoBeamTabs).toBe(false);
  expect(displays.at(-1)?.rules.TabBeamsRendered).toBe(true); expect(displays.at(-1)?.rules.TabFingeringsRendered).toBe(true);
  expect(displays.at(-1)?.rules.TabTimeSignatureRendered).toBe(true);
  expect(displays.at(-1)?.options.stretchLastSystemLine).toBe(true);
  expect(enableReviewedTabRhythm).not.toHaveBeenCalled();
});

it('enables TAB rhythm rendering only when the reviewed-TAB project explicitly opts in', async () => {
  const stem = { id: 'bass' as const, label: '베이스', status: 'ready' as const, score_status: 'ready' as const, waveform: [] };
  await act(async () => root.render(<ScoreViewer stem={stem} scale={1} spacious measureNumbers xml="<score-partwise/>" preserveNotation reviewedTabRhythm />));
  expect(enableReviewedTabRhythm).toHaveBeenCalledOnce();
});

it('reports incompatible TAB rhythm rendering rather than silently showing numbers alone', async () => {
  vi.mocked(enableReviewedTabRhythm).mockImplementation(() => { throw new Error('TAB 리듬 표시 호환성을 확인할 수 없어요.'); });
  const stem = { id: 'bass' as const, label: '베이스', status: 'ready' as const, score_status: 'ready' as const, waveform: [] };
  await act(async () => root.render(<ScoreViewer stem={stem} scale={1} spacious measureNumbers xml="<score-partwise/>" preserveNotation reviewedTabRhythm />));
  expect(container.textContent).toContain('TAB 리듬 표시 호환성을 확인할 수 없어요.');
  expect(container.textContent).not.toContain('가독성 확대');
});
