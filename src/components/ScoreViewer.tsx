import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Music2, AlertCircle } from 'lucide-react';
import type { Stem } from '../types';

type Props = { stem: Stem; scale: number; spacious: boolean; measureNumbers: boolean };

export default function ScoreViewer({ stem, scale, spacious, measureNumbers }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    const host = container.current;
    if (!host || !stem.score_url) return;
    const controller = new AbortController();
    let disposed = false;
    let observer: ResizeObserver | undefined;
    let resizeTimer: ReturnType<typeof setTimeout>;
    setLoading(true);
    setError('');
    host.innerHTML = '';
    (async () => {
      try {
        const [{ OpenSheetMusicDisplay }, response] = await Promise.all([
          import('opensheetmusicdisplay'), fetch(stem.score_url! + `?v=${stem.score_revision || 0}`, { signal: controller.signal, cache: 'no-store' }),
        ]);
        if (!response.ok) throw new Error('악보 파일을 가져오지 못했어요.');
        const xml = await response.text();
        if (disposed) return;
        const display = new OpenSheetMusicDisplay(host, {
          autoResize: false, backend: 'svg', drawTitle: false, autoBeam: true,
          pageFormat: 'A4 P',
          drawSubtitle: false, drawComposer: false, drawPartNames: false,
          drawMeasureNumbers: measureNumbers,
        });
        display.EngravingRules.StaffDistance = spacious ? 13 : 7;
        display.EngravingRules.MinimumDistanceBetweenSystems = spacious ? 10 : 5;
        display.Zoom = scale;
        await display.load(xml);
        if (disposed) return;
        display.render();
        setLoading(false);
        let lastWidth = host.clientWidth;
        observer = new ResizeObserver(() => {
          if (Math.abs(host.clientWidth - lastWidth) < 2 || disposed) return;
          lastWidth = host.clientWidth;
          clearTimeout(resizeTimer);
          resizeTimer = setTimeout(() => { if (!disposed) display.render(); }, 120);
        });
        observer.observe(host);
      } catch (error) {
        if (!disposed) {
          setError(error instanceof Error ? error.message : '악보를 표시하지 못했어요.');
          setLoading(false);
        }
      }
    })();
    return () => { disposed = true; controller.abort(); observer?.disconnect(); clearTimeout(resizeTimer); };
  }, [stem.score_url, stem.score_revision, scale, spacious, measureNumbers]);
  return <>
    {loading && <div className="score-placeholder"><LoaderCircle className="spin" size={28} /><p>악보를 그리는 중이에요</p></div>}
    {error && <div className="score-placeholder"><AlertCircle size={28} /><p>{error}</p><a href={stem.score_url + '?download=true'}>MusicXML 파일로 다운로드</a></div>}
    <div className="engraving" ref={container} aria-label={`${stem.label} 악보`} style={{ visibility: loading ? 'hidden' : 'visible' }} />
  </>;
}

export function EmptyScore({ message }: { message: string }) {
  return <div className="score-placeholder"><Music2 size={36} strokeWidth={1.2} /><h3>소리에서 악보로</h3><p>{message}</p></div>;
}
