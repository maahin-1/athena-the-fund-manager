export type Group = 'none' | 'red' | 'yellow' | 'green' | 'blue';
export const GROUPS: Group[] = ['none', 'red', 'yellow', 'green', 'blue'];

export const nextGroup = (g: Group): Group => GROUPS[(GROUPS.indexOf(g) + 1) % GROUPS.length];

type Listener = (symbol: string) => void;

/** Panels in the same colour group follow one symbol. `none` is a private panel: it never sends or receives. */
export class LinkBus {
  private symbols = new Map<Group, string>();
  private listeners = new Map<Group, Set<Listener>>();

  setSymbol(group: Group, symbol: string): void {
    if (group === 'none') return;
    this.symbols.set(group, symbol);
    this.listeners.get(group)?.forEach((fn) => fn(symbol));
  }

  symbolOf(group: Group): string | null {
    return group === 'none' ? null : (this.symbols.get(group) ?? null);
  }

  subscribe(group: Group, fn: Listener): () => void {
    if (group === 'none') return () => {};
    const set = this.listeners.get(group) ?? new Set<Listener>();
    set.add(fn);
    this.listeners.set(group, set);
    return () => set.delete(fn);
  }
}
