import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThumbStore } from "./thumbs";

let made = 0;
beforeEach(() => {
  made = 0;
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => `blob:${++made}`), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

function deferredRender() {
  const pending: { page: number; signal: AbortSignal; ok: () => void }[] = [];
  const render = vi.fn(
    (page: number, signal: AbortSignal) =>
      new Promise<Blob>((ok) => pending.push({ page, signal, ok: () => ok(new Blob(["x"])) })),
  );
  return { render, pending };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("ThumbStore", () => {
  it("renders at most two at a time, in the order asked", async () => {
    const { render, pending } = deferredRender();
    const store = new ThumbStore(render);
    const got: (string | null)[] = [];
    for (const n of [1, 2, 3]) store.request(n, (u) => got.push(u));
    expect(render.mock.calls.map((c) => c[0])).toEqual([1, 2]);
    pending[0].ok();
    await flush();
    expect(got).toEqual(["blob:1"]);
    expect(render.mock.calls.map((c) => c[0])).toEqual([1, 2, 3]);
  });

  it("serves a cached page without rendering again", async () => {
    const { render, pending } = deferredRender();
    const store = new ThumbStore(render);
    store.request(1, () => {});
    pending[0].ok();
    await flush();
    const got: (string | null)[] = [];
    store.request(1, (u) => got.push(u));
    expect(got).toEqual(["blob:1"]);
    expect(render).toHaveBeenCalledTimes(1);
  });

  it("drops a queued ask that is released before it starts", async () => {
    const { render, pending } = deferredRender();
    const store = new ThumbStore(render);
    store.request(1, () => {});
    store.request(2, () => {});
    const release = store.request(3, () => {});
    release();
    pending[0].ok();
    await flush();
    expect(render.mock.calls.map((c) => c[0])).toEqual([1, 2]);
  });

  it("evicts the least recently used thumbnails no tile is showing", async () => {
    const { render, pending } = deferredRender();
    const store = new ThumbStore(render, 2);
    store.request(1, () => {}); // held: never evicted
    const free2 = store.request(2, () => {});
    pending[0].ok();
    pending[1].ok();
    await flush();
    free2();
    store.request(3, () => {});
    pending[2].ok();
    await flush();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:2");
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:1");
  });

  it("frees every URL and stops on dispose", async () => {
    const { render, pending } = deferredRender();
    const store = new ThumbStore(render);
    store.request(1, () => {});
    pending[0].ok();
    await flush();
    store.dispose();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:1");
    expect(pending[0].signal.aborted).toBe(true);
  });
});
