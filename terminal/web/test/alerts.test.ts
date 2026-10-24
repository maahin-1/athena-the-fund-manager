import { describe, expect, it } from 'vitest';
import type { Quote } from '../src/feed';
import { allHold, describe as say, holds, makeAlert, readAlerts, step, type Condition } from '../src/alerts/model';
import { deliver, type Env } from '../src/alerts/notify';
import { AlertStore, KEY } from '../src/alerts/store';

const quote = (over: Partial<Quote> = {}): Quote => ({ s: 'TCS', l: 3500, pc: 3400, o: null, h: null, lo: null, v: 1_000_000, avg: null, h52: 4000, l52: 3000, lb: null, ub: null, t: null, b: [], a: [], src: 'stream', ...over });

describe('conditions', () => {
  it('test price, change on the previous close, volume and nearness to the 52-week range', () => {
    const q = quote();
    const cases: [Condition, boolean][] = [
      [{ kind: 'price_above', value: 3499 }, true], [{ kind: 'price_above', value: 3500 }, false], [{ kind: 'price_below', value: 3501 }, true],
      [{ kind: 'change_above', pct: 2.9 }, true], [{ kind: 'change_above', pct: 3 }, false], [{ kind: 'change_below', pct: 1 }, false],
      [{ kind: 'volume_above', value: 999_999 }, true], [{ kind: 'near_52w_high', pct: 12.5 }, true], [{ kind: 'near_52w_high', pct: 10 }, false],
      [{ kind: 'near_52w_low', pct: 17 }, true], [{ kind: 'near_52w_low', pct: 10 }, false],
    ];
    for (const [c, expected] of cases) expect(holds(c, q), JSON.stringify(c)).toBe(expected);
    expect(holds({ kind: 'change_below', pct: 2 }, quote({ l: 3300 }))).toBe(true);
  });

  it('a figure the quote lacks makes a condition not hold', () => {
    const q = quote({ v: null, h52: null, l52: null, pc: 0 });
    for (const c of [{ kind: 'volume_above', value: 1 }, { kind: 'near_52w_high', pct: 99 }, { kind: 'near_52w_low', pct: 99 }, { kind: 'change_above', pct: 0 }, { kind: 'change_below', pct: 0 }] as Condition[]) expect(holds(c, q)).toBe(false);
  });

  it('all needs every condition, any needs one, and none never holds', () => {
    const above = { kind: 'price_above', value: 3000 } as Condition;
    const dear = { kind: 'price_above', value: 9000 } as Condition;
    expect(allHold(makeAlert('TCS', [above, dear], { match: 'all' }), quote())).toBe(false);
    expect(allHold(makeAlert('TCS', [above, dear], { match: 'any' }), quote())).toBe(true);
    expect(allHold(makeAlert('TCS', [], {}), quote())).toBe(false);
    expect(say(makeAlert('tcs', [above, dear], { match: 'any' }))).toBe('TCS: price above 3000 or price above 9000');
  });
});

describe('firing', () => {
  const alert = (over = {}) => makeAlert('TCS', [{ kind: 'price_above', value: 3600 }], over, 1000);

  it('fires on the change from not holding to holding, once, and is then done', () => {
    let alerts = [alert()];
    let r = step(alerts, quote({ l: 3500 }), 2000);
    expect(r.fired).toEqual([]);
    expect(r.alerts[0].held).toBe(false);
    r = step(r.alerts, quote({ l: 3650 }), 3000);
    expect(r.fired).toHaveLength(1);
    expect(r.fired[0].message).toContain('TCS: price above 3600 (price 3650)');
    expect(r.alerts[0]).toMatchObject({ done: true, firedAt: 3000 });
    alerts = r.alerts;
    expect(step(alerts, quote({ l: 3700 }), 4000).fired).toEqual([]);
  });

  it('an alert made while its condition already holds waits for it to stop and start again', () => {
    let r = step([alert()], quote({ l: 3700 }), 2000);
    expect(r.fired).toEqual([]);
    r = step(r.alerts, quote({ l: 3500 }), 3000);
    r = step(r.alerts, quote({ l: 3700 }), 4000);
    expect(r.fired).toHaveLength(1);
  });

  it('a repeating alert fires again after it has stopped holding, but not within its cooldown', () => {
    let r = step([alert({ mode: 'repeat', cooldownMs: 10_000 })], quote({ l: 3500 }), 2000);
    r = step(r.alerts, quote({ l: 3650 }), 3000);
    expect(r.fired).toHaveLength(1);
    r = step(r.alerts, quote({ l: 3500 }), 4000);
    r = step(r.alerts, quote({ l: 3650 }), 5000);
    expect(r.fired).toEqual([]);
    r = step(r.alerts, quote({ l: 3500 }), 6000);
    r = step(r.alerts, quote({ l: 3650 }), 14_000);
    expect(r.fired).toHaveLength(1);
  });

  it('ignores quotes for other symbols', () => {
    const a = alert();
    const r = step([a], quote({ s: 'INFY', l: 9999 }), 2000);
    expect(r.alerts[0]).toBe(a);
  });
});

describe('storage', () => {
  it('keeps alerts in the browser, tells the feed which symbols to send, and announces what fires', () => {
    const disk = new Map<string, string>();
    const storage = { getItem: (k: string) => disk.get(k) ?? null, setItem: (k: string, v: string) => void disk.set(k, v) };
    let now = 1000;
    const store = new AlertStore(storage, () => now);
    store.add(makeAlert('tcs', [{ kind: 'price_above', value: 3600 }], {}, 1000));
    store.add(makeAlert('infy', [{ kind: 'price_below', value: 1000 }], {}, 1000));
    expect(store.symbols().sort()).toEqual(['INFY', 'TCS']);
    const heard: string[] = [];
    store.onFired((f) => heard.push(f.alert.symbol));
    store.quotes([quote({ l: 3500 })]);
    now = 2000;
    expect(store.quotes([quote({ l: 3650 })])).toHaveLength(1);
    expect(heard).toEqual(['TCS']);
    expect(store.symbols()).toEqual(['INFY']); // a done alert needs no more quotes
    const again = new AlertStore(storage, () => now);
    expect(again.alerts.map((a) => [a.symbol, a.done])).toEqual([['TCS', true], ['INFY', false]]);
    again.clearDone();
    expect(JSON.parse(disk.get(KEY)!).length).toBe(1);
  });

  it('drops anything in storage that is not an alert, and a webhook that is not a web address', () => {
    expect(readAlerts('nope')).toEqual([]);
    expect(readAlerts([{ id: 'x' }, null, { id: 'a', symbol: 'TCS', conditions: [{ kind: 'bogus' }] }])).toEqual([]);
    const [ok] = readAlerts([{ id: 'a', symbol: 'TCS', conditions: [{ kind: 'price_above', value: 1 }], channels: { webhook: 'javascript:alert(1)' }, match: 'any' }]);
    expect(ok).toMatchObject({ match: 'any', mode: 'once', held: null, channels: { browser: true, sound: true, webhook: null } });
    const store = new AlertStore({ getItem: () => '{broken', setItem: () => { throw new Error('full'); } });
    expect(store.alerts).toEqual([]);
    store.add(makeAlert('TCS', [{ kind: 'price_above', value: 1 }]));
    expect(store.alerts).toHaveLength(1); // a store that cannot save still keeps the alert in memory
  });
});

describe('delivery', () => {
  const fired = (channels: object) => ({ alert: { ...makeAlert('TCS', [{ kind: 'price_above', value: 1 }], {}, 1), channels: { browser: true, sound: true, webhook: null, ...channels } }, message: 'm', at: 0 });
  const env = (over: Partial<Env> = {}): Env & { sent: string[] } => {
    const sent: string[] = [];
    return { sent, notification: { permission: 'granted', create: (t) => void sent.push(`notify:${t}`) }, beep: () => void sent.push('beep'), post: async (u, b) => void sent.push(`post:${u}:${JSON.parse(b).symbol}`), log: () => {}, ...over };
  };

  it('uses the channels asked for and reports which', async () => {
    const e = env();
    expect(await deliver(fired({ webhook: 'https://hooks.example/x' }), e)).toEqual(['browser', 'sound', 'webhook']);
    expect(e.sent).toEqual(['notify:Alert: TCS', 'beep', 'post:https://hooks.example/x:TCS']);
    expect(await deliver(fired({ browser: false, sound: false }), env())).toEqual([]);
  });

  it('skips the browser when permission is not granted, and one channel failing does not stop the others', async () => {
    expect(await deliver(fired({}), env({ notification: { permission: 'denied', create: () => {} } }))).toEqual(['sound']);
    const logged: string[] = [];
    const sent = await deliver(fired({ webhook: 'https://hooks.example/x' }), env({ beep: () => { throw new Error('no audio'); }, post: async () => { throw new Error('HTTP 500'); }, log: (m) => void logged.push(m) }));
    expect(sent).toEqual(['browser']);
    expect(logged).toEqual(['sound failed: Error: no audio', 'webhook failed: Error: HTTP 500']);
  });
});
