import type { SourceNoteKind, SourceScoreDocument, SourceScoreNote, SourceScoreOperation } from '../sourceScore';

export type SourceEditFields = { step: string; alter: string; octave: string; string: string; fret: string; drum_id: string; lyric_index: string; text: string; kind: SourceNoteKind; type: string; dots: string; mark: string; action: string; target_note_id: string };
export const sourceOperationLabels: Record<SourceScoreOperation, string> = {
  pitch: '음정 변경', fingering: '같은 음의 TAB 운지', tab_pitch: 'TAB 프렛 · 음정 변경', drum: '드럼 타격 종류',
  rhythm: '음표 · 쉼표 길이', insert: '쉼표에 음표 넣기', chord: '동시에 울릴 음 추가', delete: '음표를 쉼표로 바꾸기',
  lyric: '가사 수정', lyric_add: '가사 추가', lyric_delete: '가사 삭제',
  articulation: '악센트 · 스타카토 · 테누토', connection: '타이 · 슬러 · TAB 연결 주법',
};
export const sourceMarkLabels: Record<string, string> = { accent: '악센트', staccato: '스타카토', tenuto: '테누토', tie: '타이 · 같은 음 이어서', slur: '슬러 · 프레이즈 연결', slide: '슬라이드', 'hammer-on': '해머링', 'pull-off': '풀링' };
export const sourceNoteTypes = [
  ['whole', '온음표 · 4박'], ['half', '2분음표 · 2박'], ['quarter', '4분음표 · 1박'],
  ['eighth', '8분음표 · 1/2박'], ['16th', '16분음표 · 1/4박'], ['32nd', '32분음표 · 1/8박'], ['64th', '64분음표 · 1/16박'],
] as const;
export function quarterBeatLabel(value: string): string {
  const [top, bottom = '1'] = value.split('/');
  const number = Number(top) / Number(bottom);
  return Number.isFinite(number) && number >= 0 ? `${value}박` : '위치 확인 필요';
}

type Props = {
  note: SourceScoreNote; score: SourceScoreDocument; operation: SourceScoreOperation; fields: SourceEditFields;
  onField: (key: keyof SourceEditFields, value: string) => void; onLyricIndex: (value: string) => void;
};

export default function SourceScoreNoteFields({ note, score, operation, fields, onField, onLyricIndex }: Props) {
  const creating = operation === 'insert' || operation === 'chord';
  const kinds = operation === 'chord' ? note.chord_kinds ?? [] : note.insert_kinds ?? [];
  const pitch = operation === 'pitch' || creating && fields.kind === 'pitched';
  const tab = operation === 'fingering' || operation === 'tab_pitch' || creating && fields.kind === 'tab';
  const drum = operation === 'drum' || creating && fields.kind === 'unpitched';
  const lyricItems = operation === 'lyric_delete' ? note.lyrics.filter(item => item.deletable) : note.lyrics.filter(item => item.editable);
  const connectionOptions = fields.action === 'remove' ? note.connections ?? [] : note.connection_targets ?? [];
  const connectionMarks = [...new Set(connectionOptions.map(item => item.mark))];
  function changeMark(mark: string) { onField('mark', mark); onField('target_note_id', connectionOptions.find(item => item.mark === mark)?.target_note_id ?? ''); }
  function changeAction(action: string) {
    onField('action', action);
    if (operation === 'connection') {
      const options = action === 'remove' ? note.connections ?? [] : note.connection_targets ?? [];
      const first = options.find(item => item.mark === fields.mark) ?? options[0];
      onField('mark', first?.mark ?? 'tie'); onField('target_note_id', first?.target_note_id ?? '');
    }
  }
  return <>
    {(operation === 'articulation' || operation === 'connection') && <>
      <label>처리<select aria-label="원본 기호 처리" value={fields.action} onChange={event => changeAction(event.target.value)}><option value="add">기호 추가</option><option value="remove">기호 삭제</option></select></label>
      <label>기호<select aria-label="원본 연주 기호" value={fields.mark} onChange={event => operation === 'connection' ? changeMark(event.target.value) : onField('mark', event.target.value)}>{(operation === 'connection' ? connectionMarks : ['accent', 'staccato', 'tenuto']).map(mark => <option key={mark} value={mark}>{sourceMarkLabels[mark]}</option>)}</select></label>
      {operation === 'connection' && <label>연결할 끝 음표<select aria-label="원본 기호 끝 음표" value={fields.target_note_id} onChange={event => onField('target_note_id', event.target.value)}>{connectionOptions.filter(item => item.mark === fields.mark).map(item => { const target = score.notes.find(candidate => candidate.id === item.target_note_id); return <option key={`${item.mark}:${item.target_note_id}`} value={item.target_note_id}>{target ? `${target.measure_number}마디 · ${target.note_index}번째 · ${target.description}` : item.target_note_id}</option>; })}</select></label>}
      <p className="editor-help">{operation === 'connection' ? '시작 음표와 끝 음표에 연결 기호를 함께 반영합니다. 안전하게 대응되는 끝 음표만 표시하며, 명확히 연결된 오선·TAB는 함께 수정합니다. 음정·음 길이를 자동으로 바꾸지 않습니다.' : '선택한 음표의 연주 기호만 추가하거나 삭제합니다. 음정·음 길이·가사는 유지합니다.'}</p>
      {operation === 'articulation' && <p className="editor-help">현재 기호: {note.articulations?.map(mark => sourceMarkLabels[mark]).join(', ') || '없음'}</p>}
      {operation === 'connection' && !connectionOptions.length && <p className="recognition-caution">이 처리 방식으로 안전하게 연결하거나 삭제할 수 있는 기호가 없어요.</p>}
    </>}
    {creating && <label>입력 방식<select aria-label="새 음표 입력 방식" value={fields.kind} onChange={event => onField('kind', event.target.value)}>{kinds.map(kind => <option key={kind} value={kind}>{kind === 'pitched' ? '음 이름으로 입력' : kind === 'tab' ? 'TAB 줄 · 프렛으로 입력' : '드럼 타격 종류로 입력'}</option>)}</select></label>}
    {pitch && <div className="source-note-field-group"><label>음 이름<select aria-label="원본 음표 이름" value={fields.step} onChange={event => onField('step', event.target.value)}>{[['C', '도'], ['D', '레'], ['E', '미'], ['F', '파'], ['G', '솔'], ['A', '라'], ['B', '시']].map(([step, name]) => <option key={step} value={step}>{step} · {name}</option>)}</select></label><label>변화음<select aria-label="원본 음표 변화음" value={fields.alter} onChange={event => onField('alter', event.target.value)}><option value="-2">𝄫 · 두 반음 내림</option><option value="-1">♭ · 반음 내림</option><option value="0">♮ · 변화 없음</option><option value="1">♯ · 반음 올림</option><option value="2">𝄪 · 두 반음 올림</option></select></label><label>옥타브<input aria-label="원본 음표 옥타브" type="number" min={0} max={9} value={fields.octave} onChange={event => onField('octave', event.target.value)} /></label><p className="editor-help">같은 도(C)라도 옥타브가 높을수록 높은 음이에요. 가운데 도는 C4입니다.</p></div>}
    {tab && <div className="source-note-field-group"><label>TAB 줄<input aria-label="원본 TAB 줄" type="number" min={1} max={7} value={fields.string} onChange={event => onField('string', event.target.value)} /></label><label>TAB 프렛<input aria-label="원본 TAB 프렛" type="number" min={0} max={36} value={fields.fret} onChange={event => onField('fret', event.target.value)} /></label><p className="editor-help">{operation === 'fingering' ? '같은 음을 내는 다른 줄·프렛으로만 변경합니다. 음정도 바꾸려면 수정 항목에서 TAB 프렛 · 음정 변경을 선택해주세요.' : '원본 튜닝·카포를 기준으로 프렛에 맞는 음정을 함께 적용합니다. 명확하게 연결된 오선·TAB가 있으면 같이 수정하며, 다른 음표의 운지는 재배정하지 않습니다.'} 1번 줄은 가장 높은 줄이고, 0프렛은 개방현입니다.</p></div>}
    {drum && <label>드럼 타격 종류<select aria-label="원본 드럼 타격 종류" value={fields.drum_id} onChange={event => onField('drum_id', event.target.value)}>{score.drum_options.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>}
    {operation === 'rhythm' && <div className="source-note-field-group"><label>음표 길이<select aria-label="원본 음표 길이" value={fields.type} onChange={event => onField('type', event.target.value)}>{sourceNoteTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>점음표<select aria-label="원본 음표 점" value={fields.dots} onChange={event => onField('dots', event.target.value)}><option value="0">점 없음 · 기본 길이</option><option value="1">점 1개 · 1.5배</option><option value="2">점 2개 · 1.75배</option></select></label><p className="editor-help">여기서는 4분음표를 1박으로 표시합니다. 인접한 쉼표를 함께 조절해 마디 전체 길이와 뒤 음표의 시작 위치를 유지해요.{note.rhythm_limit && ` 현재 안전하게 사용할 수 있는 길이: 최대 ${quarterBeatLabel(note.rhythm_limit)}.`} 선택한 길이를 정확하게 만들 수 없으면 저장하지 않습니다.</p></div>}
    {(operation === 'lyric' || operation === 'lyric_delete') && <label>가사 항목<select aria-label="원본 가사 항목" value={fields.lyric_index} onChange={event => onLyricIndex(event.target.value)}>{lyricItems.map(item => <option value={item.index} key={item.index}>{item.index + 1}번째 가사 · {item.text}</option>)}</select></label>}
    {(operation === 'lyric' || operation === 'lyric_add') && <label>{operation === 'lyric_add' ? '추가할 가사' : '가사 내용'}<input aria-label="원본 가사 내용" maxLength={500} value={fields.text} onChange={event => onField('text', event.target.value)} /></label>}
    {operation === 'lyric_add' && <p className="editor-help">선택한 음표에 가사 항목을 추가합니다. 기존 가사와 연결 표기는 유지하고 새로운 절 번호를 배정해요.</p>}
    {operation === 'lyric_delete' && <p className="recognition-caution">선택한 가사 항목만 삭제합니다. 음표는 그대로 유지하며, 연결된 음절·늘임표가 있는 가사는 안전을 위해 삭제할 수 없어요.</p>}
    {operation === 'delete' && <p className="recognition-caution">음표를 같은 길이의 쉼표로 바꿉니다. 마디를 줄이거나 뒤 음표를 당기지 않습니다. 연결 표기 등 함께 보존할 수 없는 내용이 있으면 실행하지 않아요.</p>}
    {operation === 'insert' && <p className="editor-help">선택한 쉼표를 같은 길이의 음표로 바꿉니다. 음표 길이는 저장 후 별도로 수정할 수 있으며, 뒤 음표의 위치는 유지합니다.</p>}
    {operation === 'chord' && <p className="editor-help">선택한 음표와 동시에 울리는 음을 하나 추가합니다. 시작 위치와 길이는 선택한 음표를 따르며, 지원하지 않는 성부·주법은 변경하지 않습니다.</p>}
  </>;
}
