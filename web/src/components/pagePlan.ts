// The page grid's state and the pdf.organize page plan it produces (plan tasks 21, 22). Pure, so
// it is unit-tested. An entry is one tile: a source page (from 1), its added turn and whether it
// is deleted. Deleted tiles stay in the grid so they can be restored; the plan leaves them out.
export type Turn = 0 | 90 | 180 | 270;
export type Entry = { key: number; page: number; rotate: Turn; deleted: boolean };
export type PageStep = { page: number; rotate: Turn };
type Keys = ReadonlySet<number>;

export function initialEntries(pages: number): Entry[] {
  return Array.from({ length: pages }, (_, i) => ({ key: i + 1, page: i + 1, rotate: 0, deleted: false }));
}

/** The `pages` option for pdf.organize: kept tiles in grid order. */
export function toPlan(entries: readonly Entry[]): PageStep[] {
  return entries.filter((e) => !e.deleted).map(({ page, rotate }) => ({ page, rotate }));
}

/** Each kept tile's page number in the result (deleted tiles get none). */
export function resultNumbers(entries: readonly Entry[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const e of entries) if (!e.deleted) out.set(e.key, out.size + 1);
  return out;
}

/** Move every selected tile one place earlier (-1) or later (1); a block moves as one. */
export function moveBy(entries: readonly Entry[], keys: Keys, dir: -1 | 1): Entry[] {
  const next = [...entries];
  const n = next.length;
  for (let k = 0; k < n; k++) {
    const i = dir < 0 ? k : n - 1 - k;
    const j = i + dir;
    if (j >= 0 && j < n && keys.has(next[i].key) && !keys.has(next[j].key)) {
      [next[i], next[j]] = [next[j], next[i]];
    }
  }
  return next;
}

/** Move the selected tiles, in their order, before (or after) the tile `target`. */
export function moveNextTo(entries: readonly Entry[], keys: Keys, target: number, after: boolean): Entry[] {
  if (keys.has(target)) return [...entries];
  const moving = entries.filter((e) => keys.has(e.key));
  const rest = entries.filter((e) => !keys.has(e.key));
  const at = rest.findIndex((e) => e.key === target) + (after ? 1 : 0);
  return [...rest.slice(0, at), ...moving, ...rest.slice(at)];
}

/** Move the selected tiles so the first one becomes page `n` of the result (1-based). */
export function moveTo(entries: readonly Entry[], keys: Keys, n: number): Entry[] {
  const moving = entries.filter((e) => keys.has(e.key));
  const rest = entries.filter((e) => !keys.has(e.key));
  let at = rest.length;
  for (let i = 0, kept = 0; i < rest.length; i++) {
    if (rest[i].deleted) continue;
    if (++kept === n) {
      at = i;
      break;
    }
  }
  return [...rest.slice(0, at), ...moving, ...rest.slice(at)];
}

export function turn(entries: readonly Entry[], keys: Keys, by: 90 | -90): Entry[] {
  return entries.map((e) => (keys.has(e.key) ? { ...e, rotate: (((e.rotate + by) % 360) + 360) % 360 as Turn } : e));
}

export function setDeleted(entries: readonly Entry[], keys: Keys, deleted: boolean): Entry[] {
  return entries.map((e) => (keys.has(e.key) ? { ...e, deleted } : e));
}

/** A copy of each selected tile right after it; returns the copies' keys too. */
export function duplicate(entries: readonly Entry[], keys: Keys): { entries: Entry[]; added: number[] } {
  let key = Math.max(0, ...entries.map((e) => e.key));
  const added: number[] = [];
  const next = entries.flatMap((e) => {
    if (!keys.has(e.key)) return [e];
    added.push(++key);
    return [e, { ...e, key }];
  });
  return { entries: next, added };
}
