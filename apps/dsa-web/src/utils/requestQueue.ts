/**
 * Global request queue to limit concurrent HTTP connections to the same origin.
 *
 * Chrome limits concurrent connections to 6 per origin. When the PaperTrading
 * page mounts 13+ polling components plus a 10-request loadAll(), the browser
 * exhausts its connection pool and fires ERR_INSUFFICIENT_RESOURCES.
 *
 * This queue serialises requests so that at most maxConcurrency requests are
 * in-flight at any time, preventing connection pool exhaustion.
 */

type Task<T> = {
  fn: () => Promise<T>;
  resolve: (value: T) => void;
  reject: (err: unknown) => void;
};

class RequestQueue {
  private queue: Task<unknown>[] = [];
  private active = 0;
  private readonly maxConcurrency: number;

  constructor(maxConcurrency = 3) {
    this.maxConcurrency = maxConcurrency;
  }

  /** Enqueue a single request; returns a promise that resolves when the request completes. */
  enqueue<T>(fn: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.queue.push({ fn, resolve, reject } as Task<unknown>);
      this.flush();
    });
  }

  /**
   * Enqueue multiple requests, respecting the concurrency limit.
   * Returns a promise that resolves when ALL requests complete.
   *
   * Uses a mapped tuple type so heterogeneous request return types are
   * preserved (e.g. loadAll's 10 different API responses).
   */
  async enqueueBatch<T extends readonly unknown[]>(
    fns: { [K in keyof T]: () => Promise<T[K]> },
  ): Promise<{ [K in keyof T]: T[K] }> {
    const results = new Array(fns.length) as { -readonly [K in keyof T]: T[K] };
    let nextIndex = 0;

    const worker = async (): Promise<void> => {
      while (nextIndex < fns.length) {
        const idx = nextIndex++;
        results[idx] = (await this.enqueue(fns[idx])) as never;
      }
    };

    const workers = Array.from(
      { length: Math.min(this.maxConcurrency, fns.length) },
      () => worker(),
    );

    const settled = await Promise.allSettled(workers);

    // Surface the first error if any worker failed.
    for (const s of settled) {
      if (s.status === 'rejected') throw s.reason;
    }

    return results;
  }

  /** Get the number of pending requests in the queue. */
  get pending(): number {
    return this.queue.length;
  }

  /** Get the number of active requests. */
  get activeCount(): number {
    return this.active;
  }

  private flush(): void {
    while (this.active < this.maxConcurrency && this.queue.length > 0) {
      const task = this.queue.shift();
      if (!task) break;
      this.active++;
      task
        .fn()
        .then((result) => task.resolve(result))
        .catch((err) => task.reject(err))
        .finally(() => {
          this.active--;
          this.flush();
        });
    }
  }
}

/** Shared singleton — all API calls share the same queue. */
export const requestQueue = new RequestQueue(3);

export default requestQueue;