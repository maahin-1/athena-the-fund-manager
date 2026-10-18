/** Edit distance between two strings (insertions, deletions and substitutions each cost one). */
export function distance(a: string, b: string): number {
  const row = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let prev = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const keep = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = keep;
    }
  }
  return row[b.length];
}

/** The known name nearest to `name` (any case) when one is within two edits, else null: for "did you mean" in an error. */
export function nearest(name: string, known: Iterable<string>): string | null {
  let best: [number, string] | null = null;
  for (const candidate of known) {
    const d = distance(name.toLowerCase(), candidate.toLowerCase());
    if (d <= 2 && (!best || d < best[0])) best = [d, candidate];
  }
  return best ? best[1] : null;
}
