import { describe, expect, it } from "vitest";
import {
  duplicate,
  initialEntries,
  moveBy,
  moveNextTo,
  moveTo,
  resultNumbers,
  setDeleted,
  toPlan,
  turn,
  type Entry,
} from "./pagePlan";

const pages = (es: Entry[]) => es.map((e) => e.page);
const keys = (...k: number[]) => new Set(k);

describe("page plan", () => {
  it("starts as every page in order, unturned", () => {
    expect(toPlan(initialEntries(3))).toEqual([
      { page: 1, rotate: 0 },
      { page: 2, rotate: 0 },
      { page: 3, rotate: 0 },
    ]);
  });

  it("moves a block one place as one, and stops at the ends", () => {
    const es = initialEntries(5);
    expect(pages(moveBy(es, keys(2, 3), -1))).toEqual([2, 3, 1, 4, 5]);
    expect(pages(moveBy(es, keys(4, 5), 1))).toEqual([1, 2, 3, 4, 5]);
    expect(pages(moveBy(es, keys(1, 4), 1))).toEqual([2, 1, 3, 5, 4]);
  });

  it("drops the selection before or after a target tile, keeping its order", () => {
    const es = initialEntries(5);
    expect(pages(moveNextTo(es, keys(4, 1), 2, true))).toEqual([2, 1, 4, 3, 5]);
    expect(pages(moveNextTo(es, keys(5), 1, false))).toEqual([5, 1, 2, 3, 4]);
    expect(pages(moveNextTo(es, keys(2), 2, false))).toEqual([1, 2, 3, 4, 5]);
  });

  it("moves to a result page number, skipping deleted tiles", () => {
    const es = setDeleted(initialEntries(5), keys(1), true);
    // Result is 2,3,4,5; moving page 5 to result page 2 puts it before page 3.
    expect(pages(moveTo(es, keys(5), 2))).toEqual([1, 2, 5, 3, 4]);
    expect(pages(moveTo(es, keys(2), 99))).toEqual([1, 3, 4, 5, 2]);
  });

  it("turns both ways and wraps", () => {
    const es = turn(initialEntries(2), keys(1), -90);
    expect(es.map((e) => e.rotate)).toEqual([270, 0]);
    expect(turn(turn(es, keys(1), 90), keys(1), 90)[0].rotate).toBe(90);
  });

  it("duplicates right after the original with new keys", () => {
    const { entries, added } = duplicate(initialEntries(3), keys(2));
    expect(pages(entries)).toEqual([1, 2, 2, 3]);
    expect(added).toEqual([4]);
    expect(new Set(entries.map((e) => e.key)).size).toBe(4);
  });

  it("leaves deleted tiles out of the plan and the numbering", () => {
    const es = turn(setDeleted(initialEntries(4), keys(2), true), keys(3), 90);
    expect(toPlan(es)).toEqual([
      { page: 1, rotate: 0 },
      { page: 3, rotate: 90 },
      { page: 4, rotate: 0 },
    ]);
    expect([...resultNumbers(es)]).toEqual([
      [1, 1],
      [3, 2],
      [4, 3],
    ]);
  });
});
