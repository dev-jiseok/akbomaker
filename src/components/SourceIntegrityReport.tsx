import type { ScoreIntegrityIssue, ScoreIntegrityReport } from '../sourceScore';

type Props = { report: ScoreIntegrityReport; disabled: boolean; onInspect: (issue: ScoreIntegrityIssue) => void };
export default function SourceIntegrityReport({ report, disabled, onInspect }: Props) {
  if (report.scope !== 'musicxml-structure-not-pdf-accuracy') return null;
  return <details className="source-score-integrity">
    <summary>악보 구조 점검 · 확인 항목 {report.total_issues}개</summary>
    <p className="editor-help">선택한 MusicXML의 {report.checked_measures}마디·음표/쉼표 {report.checked_notes}개를 검사했습니다. 음표 길이·박자 합·연결 기호·악기 표기의 일관성을 확인하는 보조 검사이며, 원본 PDF와 같은 음인지 또는 인식 누락이 없는지는 판정하지 않습니다. 자동으로 음표를 바꾸지 않아요.</p>
    {report.total_issues === 0 ? <p className="editor-help">지원하는 구조 검사에서 확인 항목이 발견되지 않았습니다. 원본과 같다는 보증은 아니므로 원본도 함께 대조해주세요.</p> : <>
      <ul className="recognition-warnings">{report.issues.map((issue, index) => <li key={`${issue.code}:${index}`}><span className={`integrity-severity integrity-${issue.severity}`}>{issue.severity === 'warning' ? '확인 필요' : '참고'}</span><strong>{issue.measure_number}마디</strong> · {issue.message} <button className="text-button" disabled={disabled} onClick={() => onInspect(issue)}>해당 위치 확인</button></li>)}</ul>
      {report.truncated && <p className="editor-help">전체 {report.total_issues}개 중 처음 {report.issues.length}개를 표시합니다. 앞부분을 확인한 뒤 저장하면 다시 검사합니다.</p>}
    </>}
  </details>;
}
