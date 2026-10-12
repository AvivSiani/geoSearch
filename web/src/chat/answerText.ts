/**
 * Turns an agent answer into plain data the UI renders with React elements
 * (never as HTML). Handles what small models actually emit: paragraphs, "-" /
 * "1." lists, "#" headings, **bold**, and item citations like [i3] or [i3, i7]
 * (the backend has already dropped citations to unknown items).
 */

export type Inline =
  | { t: 'text'; v: string; bold?: boolean }
  | { t: 'cite'; ids: string[] };

export type Block =
  | { type: 'p'; lines: Inline[][] }
  | { type: 'heading'; content: Inline[] }
  | { type: 'ul' | 'ol'; items: Inline[][] };

const CITATION = /\s?\[\s*(i\d+(?:\s*,\s*i\d+)*)\s*\]/g;
const BOLD = /\*\*(.+?)\*\*|__(.+?)__/g;
const BULLET = /^\s*(?:[-*•])\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

function citations(text: string, bold: boolean): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of text.matchAll(CITATION)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ t: 'text', v: text.slice(last, start), ...(bold && { bold }) });
    out.push({ t: 'cite', ids: m[1]!.split(',').map((s) => s.trim()) });
    last = start + m[0].length;
  }
  if (last < text.length) out.push({ t: 'text', v: text.slice(last), ...(bold && { bold }) });
  return out;
}

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of text.matchAll(BOLD)) {
    const start = m.index ?? 0;
    if (start > last) out.push(...citations(text.slice(last, start), false));
    out.push(...citations(m[1] ?? m[2] ?? '', true));
    last = start + m[0].length;
  }
  if (last < text.length) out.push(...citations(text.slice(last), false));
  return out;
}

export function parseAnswer(text: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { type: 'ul' | 'ol'; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length) blocks.push({ type: 'p', lines: para.map(parseInline) });
    para = [];
  };
  const flushList = () => {
    if (list) blocks.push({ type: list.type, items: list.items.map(parseInline) });
    list = null;
  };

  for (const raw of text.replace(/\r\n?/g, '\n').split('\n')) {
    const line = raw.trimEnd();
    if (line.trim() === '') {
      flushPara();
      flushList();
      continue;
    }
    const heading = HEADING.exec(line);
    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    if (heading) {
      flushPara();
      flushList();
      blocks.push({ type: 'heading', content: parseInline(heading[1]!) });
    } else if (bullet || ordered) {
      flushPara();
      const type = bullet ? 'ul' : 'ol';
      if (list && list.type !== type) flushList();
      list ??= { type, items: [] };
      list.items.push((bullet ?? ordered)![1]!);
    } else if (list && /^\s{2,}/.test(raw)) {
      // An indented continuation line belongs to the last list item.
      list.items[list.items.length - 1] += ` ${line.trim()}`;
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return blocks;
}

/** Every cited item id, in order of first appearance. */
export function citedIds(text: string): string[] {
  const seen = new Set<string>();
  for (const m of text.matchAll(CITATION)) {
    for (const id of m[1]!.split(',')) seen.add(id.trim());
  }
  return [...seen];
}
