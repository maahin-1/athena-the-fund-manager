export type JobStart = 'started' | 'busy' | 'failed';

/** Ask the server to make a verdict for one symbol. It runs the project's Python command and keeps the result. */
export async function startVerdict(symbol: string): Promise<JobStart> {
  try {
    const r = await fetch(`/api/verdict/${encodeURIComponent(symbol)}`, { method: 'POST' });
    return r.status === 409 ? 'busy' : r.ok ? 'started' : 'failed';
  } catch {
    return 'failed';
  }
}

export interface JobEnd {
  state: string;
  message?: string;
}

/** Poll the one verdict job until it stops running. Returns a function that stops the polling. */
export function watchVerdictJob(onEnd: (end: JobEnd) => void, everyMs = 2000): () => void {
  const timer = setInterval(async () => {
    try {
      const s = (await (await fetch('/api/verdict-job')).json()) as JobEnd;
      if (s.state === 'running') return;
      clearInterval(timer);
      onEnd(s);
    } catch {
      /* the server may be restarting; try again on the next tick */
    }
  }, everyMs);
  return () => clearInterval(timer);
}
