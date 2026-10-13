// Page thumbnails for the page grid: rendered on request (tiles ask when they scroll into view),
// at most POOL at a time, cached as object URLs per source page. The cache holds at most MAX
// thumbnails (about 15 KB of JPEG each); past that the least recently used ones that no tile
// is showing are revoked. `dispose()` stops everything and frees every URL.
export const THUMB_CSS_WIDTH = 120;
const POOL = 2;
const MAX = 300;

type Render = (page: number, signal: AbortSignal) => Promise<Blob>;

export class ThumbStore {
  private urls = new Map<number, string>(); // insertion order = least recently used first
  private inUse = new Map<number, number>();
  private waiters = new Map<number, Set<(url: string | null) => void>>();
  private queue: number[] = [];
  private running = 0;
  private ac = new AbortController();

  constructor(
    private render: Render,
    private max = MAX,
  ) {}

  /** Ask for page `n`'s thumbnail; `done` gets its URL (null if it failed). The URL stays valid
   * until the returned release function runs. Releasing before it arrives cancels the ask. */
  request(n: number, done: (url: string | null) => void): () => void {
    let held = false;
    const got = (url: string | null) => {
      if (url) {
        held = true;
        this.inUse.set(n, (this.inUse.get(n) ?? 0) + 1);
      }
      done(url);
    };
    const cached = this.urls.get(n);
    if (cached) {
      this.urls.delete(n);
      this.urls.set(n, cached); // most recently used
      got(cached);
    } else {
      const set = this.waiters.get(n) ?? new Set();
      set.add(got);
      if (!this.waiters.has(n)) {
        this.waiters.set(n, set);
        this.queue.push(n);
        this.pump();
      }
    }
    return () => {
      if (held) {
        held = false;
        const left = (this.inUse.get(n) ?? 1) - 1;
        if (left > 0) this.inUse.set(n, left);
        else this.inUse.delete(n);
        return;
      }
      const set = this.waiters.get(n);
      set?.delete(got);
      if (set?.size === 0 && this.queue.includes(n)) {
        this.waiters.delete(n);
        this.queue = this.queue.filter((p) => p !== n);
      }
    };
  }

  dispose(): void {
    this.ac.abort();
    this.queue = [];
    this.waiters.clear();
    this.urls.forEach((u) => URL.revokeObjectURL(u));
    this.urls.clear();
  }

  private pump(): void {
    while (this.running < POOL && this.queue.length && !this.ac.signal.aborted) {
      const n = this.queue.shift()!;
      this.running++;
      this.render(n, this.ac.signal)
        .then((blob) => (this.ac.signal.aborted ? null : this.keep(n, URL.createObjectURL(blob))))
        .catch(() => null)
        .then((url) => {
          this.running--;
          const set = this.waiters.get(n);
          this.waiters.delete(n);
          set?.forEach((cb) => cb(url));
          this.pump();
        });
    }
  }

  private keep(n: number, url: string): string {
    this.urls.set(n, url);
    for (const [p, u] of this.urls) {
      if (this.urls.size <= this.max) break;
      if (p === n || this.inUse.has(p)) continue;
      URL.revokeObjectURL(u);
      this.urls.delete(p);
    }
    return url;
  }
}
