import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Music2, AlertCircle, ZoomIn } from 'lucide-react';
import type { ScoreLayout, Stem } from '../types';

type Props = { stem: Stem; scale: number; spacious: boolean; measureNumbers: boolean; xml?: string; layout?: ScoreLayout; onMeasureSelect?: (measure: number) => void; activeMeasure?: number | null };

export default function ScoreViewer({ stem, scale, spacious, measureNumbers, xml, layout = stem.score_layout, onMeasureSelect, activeMeasure }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [wide, setWide] = useState(false);
  const measureCallback = useRef(onMeasureSelect);
  measureCallback.current = onMeasureSelect;
  useEffect(() => { container.current?.querySelectorAll('.vf-measure').forEach(node => node.classList.toggle('playing-measure', node.id === String(activeMeasure))); }, [activeMeasure, loading]);
  useEffect(() => {
    const host = container.current;
    if (!host || (!stem.score_url && !xml)) return;
    const controller = new AbortController();
    let disposed = false;
    let observer: ResizeObserver | undefined;
    let resizeTimer: ReturnType<typeof setTimeout>;
    setLoading(true);
    setError('');
    host.innerHTML = '';
    // Engrave on a canonical A4-width surface. Mobile/desktop CSS scales the
    // page, rather than changing the musical line breaks at each viewport.
    const surface = document.createElement('div');
    surface.style.width = '1200px';
    host.appendChild(surface);
    const pickMeasure = (event: MouseEvent) => {
      const measure = (event.target as Element).closest('.vf-measure');
      if (measure && /^\d+$/.test(measure.id)) measureCallback.current?.(Number(measure.id));
    };
    surface.addEventListener('click', pickMeasure);
    (async () => {
      try {
        const [{ OpenSheetMusicDisplay }, notation] = await Promise.all([
          import('opensheetmusicdisplay'), xml ? Promise.resolve(xml) : fetch(stem.score_url! + `?v=${stem.score_revision || 0}`, { signal: controller.signal, cache: 'no-store' }).then(async response => {
            if (!response.ok) throw new Error('악보 파일을 가져오지 못했어요.');
            return response.text();
          }),
        ]);
        if (disposed) return;
        const display = new OpenSheetMusicDisplay(surface, {
          autoResize: false, backend: 'svg', drawTitle: false, autoBeam: true,
          autoGenerateMultipleRestMeasuresFromRestMeasures: false,
          pageFormat: 'A4 P',
          drawSubtitle: false, drawComposer: false, drawPartNames: false,
          drawMeasureNumbers: measureNumbers,
          measureNumberInterval: 1,
        });
        display.EngravingRules.StaffDistance = spacious ? 13 : 7;
        display.EngravingRules.MinimumDistanceBetweenSystems = spacious ? 10 : 5;
        display.EngravingRules.RenderXMeasuresPerLineAkaSystem = layout?.measures_per_line || 4;
        display.EngravingRules.NewSystemAtXMLNewSystemAttribute = true;
        display.EngravingRules.UseXMLMeasureNumbers = true;
        display.EngravingRules.TabFingeringsRendered = false;
        display.EngravingRules.TabBeamsRendered = false;
        display.EngravingRules.TabTimeSignatureRendered = false;
        display.EngravingRules.TabKeySignatureRendered = false;
        display.EngravingRules.TabStaffInterlineHeight = 1.25;
        display.EngravingRules.VexFlowDefaultTabFontScale = 48;
        display.EngravingRules.RenderLyrics = true;
        // Rest-only instrument bars can still contain vocal cues. Combining
        // them into multirests loses the timeline and overlaps their lyrics.
        display.EngravingRules.RenderMultipleRestMeasures = false;
        display.EngravingRules.PercussionOneLineCutoff = 0;
        display.EngravingRules.LyricsHeight = 1.7;
        display.EngravingRules.LyricsYMarginToBottomLine = 1.5;
        display.Zoom = (layout?.preset === 'large' ? Math.max(1.3, scale) : scale) * 0.7;
        await display.load(notation);
        if (disposed) return;
        display.render();
        surface.style.width = '100%';
        setLoading(false);
        let lastWidth = host.clientWidth;
        observer = new ResizeObserver(() => {
          if (Math.abs(host.clientWidth - lastWidth) < 2 || disposed) return;
          lastWidth = host.clientWidth;
          clearTimeout(resizeTimer);
          resizeTimer = setTimeout(() => {
            if (!disposed) { surface.style.width = '1200px'; display.render(); surface.style.width = '100%'; }
          }, 120);
        });
        observer.observe(host);
      } catch (error) {
        if (!disposed) {
          setError(error instanceof Error ? error.message : '악보를 표시하지 못했어요.');
          setLoading(false);
        }
      }
    })();
    return () => { disposed = true; controller.abort(); observer?.disconnect(); clearTimeout(resizeTimer); surface.removeEventListener('click', pickMeasure); };
  }, [stem.score_url, stem.score_revision, scale, spacious, measureNumbers, xml, layout?.preset, layout?.measures_per_line]);
  return <>
    {loading && <div className="score-placeholder"><LoaderCircle className="spin" size={28} /><p>악보를 그리는 중이에요</p></div>}
    {error && <div className="score-placeholder"><AlertCircle size={28} /><p>{error}</p><a href={stem.score_url + '?download=true'}>MusicXML 파일로 다운로드</a></div>}
    {!loading && !error && <button className="text-button score-reading-toggle" aria-pressed={wide} onClick={() => setWide(!wide)}><ZoomIn size={14} />{wide ? '페이지 너비에 맞추기' : '가독성 확대 · 좌우로 읽기'}</button>}
    <div className={`engraving ${wide ? 'reading-wide' : ''}`} ref={container} aria-label={`${stem.label} 악보`} style={{ visibility: loading ? 'hidden' : 'visible' }} />
  </>;
}

export function EmptyScore({ message }: { message: string }) {
  return <div className="score-placeholder"><Music2 size={36} strokeWidth={1.2} /><h3>소리에서 악보로</h3><p>{message}</p></div>;
}
