/** Cancel superseded requests and ignore stale completions, even when fetch cannot abort. */
export class LatestRequest {
  private generation = 0;
  private controller: AbortController | null = null;

  begin(): { signal: AbortSignal; current: () => boolean } {
    this.cancel();
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    return { signal: controller.signal,
      current: () => generation === this.generation && !controller.signal.aborted };
  }

  cancel() {
    this.generation += 1;
    this.controller?.abort();
    this.controller = null;
  }
}
