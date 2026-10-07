/**
 * Instance-local OSMD 1.9 TAB rendering compatibility adapter.
 *
 * OSMD creates VexFlow TabNotes without draw_stem/draw_dots and without dot
 * modifiers. Run after load(), before render(), ONLY for reviewed TAB projects
 * whose explicit rhythm is the sole displayed rhythm. No MusicXML, source
 * notes, prototypes or global factories are changed.
 */
const incompatible = 'TAB 리듬 표시를 준비하지 못했어요. 숫자만 표시한 악보를 완성된 결과로 보여주지 않습니다. MusicXML을 내려받거나 새로고침 후 다시 시도해주세요.';

type ObjectValue = Record<string, unknown>;
type RenderNote = ObjectValue & {
  getAttribute: (name: string) => unknown;
  getDots: () => unknown;
  getTicks: () => unknown;
  addDot: () => unknown;
  render_options: ObjectValue;
  glyph: ObjectValue;
  modifiers: unknown[];
};

function object(value: unknown): value is ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function fail(): never { throw new Error(incompatible); }

function ticks(note: Pick<RenderNote, 'getTicks'>): string {
  const value = note.getTicks();
  if (!object(value) || typeof value.numerator !== 'number' || !Number.isFinite(value.numerator)
      || typeof value.denominator !== 'number' || !Number.isFinite(value.denominator) || value.denominator <= 0) fail();
  return `${value.numerator}/${value.denominator}`;
}

function dots(note: RenderNote): number {
  return note.modifiers.filter(modifier => {
    if (!object(modifier) || typeof modifier.getCategory !== 'function') fail();
    return modifier.getCategory() === 'dots';
  }).length;
}

export function enableReviewedTabRhythm(display: unknown): void {
  try {
    if (!object(display) || !object(display.GraphicSheet) || !Array.isArray(display.GraphicSheet.MeasureList)) fail();
    const notes = new Set<RenderNote>();
    let rests = 0;
    for (const measureRow of display.GraphicSheet.MeasureList) {
      if (!Array.isArray(measureRow)) fail();
      for (const measure of measureRow) {
        if (measure == null) continue;
        if (!object(measure)) fail();
        if (!measure.isTabMeasure) continue;
        if (!Array.isArray(measure.staffEntries)) fail();
        for (const entry of measure.staffEntries) {
          if (!object(entry) || !Array.isArray(entry.graphicalVoiceEntries)) fail();
          for (const voice of entry.graphicalVoiceEntries) {
            if (!object(voice) || !object(voice.vfStaveNote) || typeof voice.vfStaveNote.getAttribute !== 'function') fail();
            const value = voice.vfStaveNote;
            if (typeof value.getAttribute !== 'function') fail();
            const kind = value.getAttribute('type');
            // Rest-filled TAB voices are deliberately represented as ghosts.
            if (kind === 'GhostNote') {
              if (typeof value.getTicks !== 'function') fail();
              const readTicks = value.getTicks.bind(value);
              ticks({ getTicks: readTicks });
              rests++;
              continue;
            }
            if (kind !== 'TabNote' || !object(value.render_options) || !object(value.glyph)
                || typeof value.glyph.stem !== 'boolean' || !Array.isArray(value.modifiers)
                || typeof value.getDots !== 'function' || typeof value.addDot !== 'function'
                || typeof value.getTicks !== 'function') fail();
            const note = value as RenderNote;
            const count = note.getDots();
            if (typeof count !== 'number' || !Number.isInteger(count) || count < 0 || count > 2 || dots(note) > count) fail();
            ticks(note);
            notes.add(note);
          }
        }
      }
    }
    // A user may legitimately replace the last TAB note with a rest in the
    // existing editor. A known TAB staff with valid ghost rests is compatible.
    if (notes.size === 0 && rests === 0) fail();
    for (const note of notes) {
      const nominalDots = note.getDots() as number;
      const durationTicks = ticks(note);
      const missing = nominalDots - dots(note);
      note.render_options.draw_stem = note.glyph.stem;
      note.render_options.draw_dots = true;
      // addDot adds a visual modifier AND increments VexFlow's nominal dot
      // count, although the duration string already supplied that count.
      // Restore the existing count; never alter ticks or source note lengths.
      for (let index = 0; index < missing; index++) note.addDot();
      note.dots = nominalDots;
      if (note.getDots() !== nominalDots || dots(note) !== nominalDots || ticks(note) !== durationTicks) fail();
    }
  } catch {
    // A library upgrade must fail visibly instead of silently producing a
    // numerals-only score that appears to contain the reviewed rhythm.
    fail();
  }
}
