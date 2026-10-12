import { describe, expect, it } from 'vitest';
import { citedIds, parseAnswer, parseInline } from './answerText';
import { toGeoJson } from './exportResults';

describe('parseInline', () => {
  it('splits citations out of text', () => {
    expect(parseInline('Try Lotus [i1] or Bamboo [i2, i5].')).toEqual([
      { t: 'text', v: 'Try Lotus' },
      { t: 'cite', ids: ['i1'] },
      { t: 'text', v: ' or Bamboo' },
      { t: 'cite', ids: ['i2', 'i5'] },
      { t: 'text', v: '.' },
    ]);
  });

  it('handles bold, including citations inside it', () => {
    expect(parseInline('**קפה לוטוס [i3]** מומלץ')).toEqual([
      { t: 'text', v: 'קפה לוטוס', bold: true },
      { t: 'cite', ids: ['i3'] },
      { t: 'text', v: ' מומלץ' },
    ]);
  });

  it('leaves unrelated brackets alone', () => {
    expect(parseInline('see [note] and [x1]')).toEqual([{ t: 'text', v: 'see [note] and [x1]' }]);
  });
});

describe('parseAnswer', () => {
  it('builds paragraphs and lists', () => {
    const blocks = parseAnswer('Found 3 places:\n\n- A [i1]\n- B [i2]\n\n1. first\n2. second\n\nBye');
    expect(blocks.map((b) => b.type)).toEqual(['p', 'ul', 'ol', 'p']);
    expect(blocks[1]).toMatchObject({ type: 'ul', items: [[{ v: 'A' }, { ids: ['i1'] }], [{ v: 'B' }, { ids: ['i2'] }]] });
  });

  it('keeps single newlines as lines of one paragraph', () => {
    expect(parseAnswer('line one\nline two')).toEqual([
      { type: 'p', lines: [[{ t: 'text', v: 'line one' }], [{ t: 'text', v: 'line two' }]] },
    ]);
  });

  it('reads headings and CRLF text', () => {
    expect(parseAnswer('## Results\r\nText')[0]).toEqual({ type: 'heading', content: [{ t: 'text', v: 'Results' }] });
  });

  it('never yields HTML-ish structure from raw text', () => {
    const blocks = parseAnswer('<img src=x onerror=alert(1)>');
    expect(blocks).toEqual([{ type: 'p', lines: [[{ t: 'text', v: '<img src=x onerror=alert(1)>' }]] }]);
  });
});

describe('citedIds', () => {
  it('lists ids in order of first appearance', () => {
    expect(citedIds('a [i2] b [i1, i2] c [i3]')).toEqual(['i2', 'i1', 'i3']);
  });
});

describe('toGeoJson', () => {
  it('exports located items as numbered points', () => {
    const fc = toGeoJson([
      { id: 'i1', source_id: 9101, name: 'A', lon: 34.7, lat: 32.1, data: { rating: 4.5 } },
      { id: 'i2', source_id: 9101, name: 'B', lon: null, lat: null, data: {} },
      { id: 'i3', source_id: 9101, name: 'C', lon: 34.8, lat: 32.2, data: {} },
    ]);
    expect(fc.features).toHaveLength(2);
    expect(fc.features[0]).toMatchObject({
      geometry: { coordinates: [34.7, 32.1] },
      properties: { n: 1, id: 'i1', name: 'A', rating: 4.5 },
    });
    expect(fc.features[1]!.properties).toMatchObject({ n: 3 });
  });
});
