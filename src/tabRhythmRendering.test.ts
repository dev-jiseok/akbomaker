// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { EngravingRules, Fraction, VexFlowConverter } from 'opensheetmusicdisplay';
import { enableReviewedTabRhythm } from './tabRhythmRendering';

function renderNote(count = 0, stem = true) {
  return {
    dots: count,
    render_options: { draw_stem: undefined as boolean | undefined, draw_dots: undefined as boolean | undefined },
    glyph: { stem }, modifiers: [] as { getCategory: () => string }[],
    getAttribute: () => 'TabNote',
    getDots() { return this.dots; },
    getTicks: () => ({ numerator: 6144, denominator: 1 }),
    addDot() { this.dots++; this.modifiers.push({ getCategory: () => 'dots' }); return this; },
  };
}

function displayFor(...notes: unknown[]) {
  return { GraphicSheet: { MeasureList: [[{ isTabMeasure: true, staffEntries: [
    { graphicalVoiceEntries: notes.map(vfStaveNote => ({ vfStaveNote })) },
  ] }]] } };
}

describe('reviewed TAB instance-local rhythm rendering', () => {
  it('enables stems and dot modifiers without changing nominal rhythm', () => {
    const note = renderNote(1), before = note.getTicks();
    enableReviewedTabRhythm(displayFor(note));
    expect(note.render_options).toEqual({ draw_stem: true, draw_dots: true });
    expect(note.modifiers).toHaveLength(1);
    expect(note.getDots()).toBe(1);
    expect(note.getTicks()).toEqual(before);
  });

  it('retains whole-note stem suppression and supports double dots', () => {
    const whole = renderNote(0, false), dotted = renderNote(2);
    enableReviewedTabRhythm(displayFor(whole, dotted));
    expect(whole.render_options.draw_stem).toBe(false);
    expect(dotted.modifiers).toHaveLength(2);
    expect(dotted.getDots()).toBe(2);
  });

  it('is idempotent and avoids duplicate modifiers for shared render notes', () => {
    const note = renderNote(2);
    note.modifiers.push({ getCategory: () => 'dots' }, { getCategory: () => 'bends' });
    const display = displayFor(note, note);
    enableReviewedTabRhythm(display);
    enableReviewedTabRhythm(display);
    expect(note.modifiers.filter(item => item.getCategory() === 'dots')).toHaveLength(2);
    expect(note.modifiers.filter(item => item.getCategory() === 'bends')).toHaveLength(1);
    expect(note.getDots()).toBe(2);
  });

  it('does not mutate ghosts, ordinary staff notes, source music or another instance', () => {
    const note = renderNote(), other = renderNote();
    const ghost = { getAttribute: () => 'GhostNote', getTicks: () => ({ numerator: 16384, denominator: 1 }) };
    const source = { duration: 96, pitch: 33, stem: 'up' };
    const display = displayFor(note, ghost);
    Object.assign(display, { Sheet: source });
    display.GraphicSheet.MeasureList[0].push({ isTabMeasure: false, staffEntries: [{ graphicalVoiceEntries: [{ vfStaveNote: other }] }] });
    const separate = displayFor(renderNote(1));
    enableReviewedTabRhythm(display);
    expect(other.render_options.draw_stem).toBeUndefined();
    expect(ghost).toEqual({ getAttribute: ghost.getAttribute, getTicks: ghost.getTicks });
    expect(source).toEqual({ duration: 96, pitch: 33, stem: 'up' });
    expect(separate.GraphicSheet.MeasureList[0][0].staffEntries[0].graphicalVoiceEntries[0].vfStaveNote).toMatchObject({ modifiers: [], render_options: { draw_stem: undefined } });
  });

  it('allows a valid all-rest TAB project after the user removes its final note', () => {
    const ghost = { getAttribute: () => 'GhostNote', getTicks: () => ({ numerator: 16384, denominator: 1 }) };
    expect(() => enableReviewedTabRhythm(displayFor(ghost))).not.toThrow();
    expect(Object.keys(ghost)).toEqual(['getAttribute', 'getTicks']);
  });

  it('does not confuse ordinary-staff ghost rests with a compatible TAB project', () => {
    const display = displayFor({ getAttribute: () => 'GhostNote', getTicks: () => ({ numerator: 16384, denominator: 1 }) });
    display.GraphicSheet.MeasureList[0][0].isTabMeasure = false;
    expect(() => enableReviewedTabRhythm(display)).toThrow('TAB 리듬 표시');
  });

  it.each([null, {}, { GraphicSheet: {} }, { GraphicSheet: { MeasureList: [] } },
    { GraphicSheet: { MeasureList: [null] } }, displayFor({ getAttribute: () => 'GhostNote' })])(
    'fails visibly when a reviewed TAB has no compatible rendered notes: %j', value => {
      expect(() => enableReviewedTabRhythm(value)).toThrow('숫자만 표시한 악보');
    },
  );

  it.each(['glyph', 'render_options', 'modifiers', 'getDots', 'getTicks', 'addDot'])('rejects missing %s capability', field => {
    const note = renderNote(1) as Record<string, unknown>;
    delete note[field];
    expect(() => enableReviewedTabRhythm(displayFor(note))).toThrow('TAB 리듬 표시');
  });

  it('validates all note shapes before changing any render objects', () => {
    const first = renderNote(1), invalid = { getAttribute: () => 'StaveNote' };
    expect(() => enableReviewedTabRhythm(displayFor(first, invalid))).toThrow('TAB 리듬 표시');
    expect(first.render_options.draw_stem).toBeUndefined();
    expect(first.modifiers).toHaveLength(0);
  });

  it('detects an incompatible dot API instead of claiming success', () => {
    const note = renderNote(1);
    note.addDot = () => note;
    expect(() => enableReviewedTabRhythm(displayFor(note))).toThrow('TAB 리듬 표시');
  });

  it('detects unexpected duration changes from a library modifier API', () => {
    const note = renderNote(1);
    const original = note.addDot;
    note.addDot = () => { original.call(note); note.getTicks = () => ({ numerator: 12288, denominator: 1 }); return note; };
    expect(() => enableReviewedTabRhythm(displayFor(note))).toThrow('TAB 리듬 표시');
  });
});

type ActualNote = ReturnType<typeof renderNote> & { getDuration: () => string; glyph: { stem: boolean; flag: boolean } };
function actualNote(numerator: number, denominator: number, dotCount: number): ActualNote {
  // The bundle's factory is the real code path used by OSMD's TAB measure.
  // Source-note semantics are irrelevant to this renderer compatibility test;
  // no canvas, network or generated audio is involved.
  const graphicalVoice = {
    notes: [{ graphicalNoteLength: new Fraction(numerator, denominator), numberOfDots: dotCount,
      sourceNote: { StringNumberTab: 1, FretNumber: 3, Pitch: { ToString: () => 'G' } }, setIndex() {} }],
    parentStaffEntry: { parentMeasure: { parentSourceMeasure: { Rules: new EngravingRules() }, MeasureNumber: 1 } },
  };
  return VexFlowConverter.CreateTabNote(graphicalVoice as unknown as Parameters<typeof VexFlowConverter.CreateTabNote>[0]) as unknown as ActualNote;
}

describe('installed OSMD/VexFlow bundle compatibility', () => {
  it('accepts real VexFlow ghost rests without adding false stems or dots', () => {
    const rests = VexFlowConverter.GhostNotes(new Fraction(1, 1));
    expect(rests.length).toBeGreaterThan(0);
    expect(() => enableReviewedTabRhythm(displayFor(...rests))).not.toThrow();
  });

  it('adds the missing dot and stem without altering the actual dotted-quarter duration', () => {
    const note = actualNote(3, 8, 1), before = { ...note.getTicks() };
    expect(note.getDuration()).toBe('q');
    expect(note.getDots()).toBe(1);
    expect(note.modifiers).toHaveLength(0);
    expect(note.render_options.draw_stem).toBeUndefined();
    enableReviewedTabRhythm(displayFor(note));
    expect(note.getDuration()).toBe('q');
    expect(note.getDots()).toBe(1);
    expect(note.modifiers.filter(item => item.getCategory() === 'dots')).toHaveLength(1);
    expect({ ...note.getTicks() }).toEqual(before);
    expect(note.render_options.draw_stem).toBe(true);
    enableReviewedTabRhythm(displayFor(note));
    expect(note.modifiers).toHaveLength(1);
  });

  it('keeps whole notes stemless and allows existing eighth-note flag glyphs to draw', () => {
    const whole = actualNote(1, 1, 0), eighth = actualNote(1, 8, 0), doubleDot = actualNote(7, 16, 2);
    const untouched = actualNote(1, 4, 0);
    enableReviewedTabRhythm(displayFor(whole, eighth, doubleDot));
    expect(whole.render_options.draw_stem).toBe(false);
    expect(eighth.render_options.draw_stem).toBe(true);
    expect(eighth.glyph.flag).toBe(true);
    expect(doubleDot.modifiers.filter(item => item.getCategory() === 'dots')).toHaveLength(2);
    expect(doubleDot.getDots()).toBe(2);
    expect(untouched.render_options.draw_stem).toBeUndefined();
    expect(untouched.modifiers).toHaveLength(0);
  });
});
