import type { Fired } from './model';

/** What delivering an alert needs from the page, so the delivery can be tested without a browser. */
export interface Env {
  notification: { permission: string; create(title: string, body: string): void } | null;
  beep(): void;
  post(url: string, body: string): Promise<void>;
  log(message: string): void;
}

export const WEBHOOK_TIMEOUT_MS = 8_000;

/** Delivers a fired alert on the channels it asked for. A channel that fails is logged and never stops the others. The webhook gets a small JSON body (symbol, message, time) and nothing else. */
export async function deliver(f: Fired, env: Env): Promise<string[]> {
  const sent: string[] = [];
  const { channels } = f.alert;
  if (channels.browser && env.notification && env.notification.permission === 'granted') {
    try {
      env.notification.create(`Alert: ${f.alert.symbol}`, f.message);
      sent.push('browser');
    } catch (e) {
      env.log(`browser notification failed: ${String(e)}`);
    }
  }
  if (channels.sound) {
    try {
      env.beep();
      sent.push('sound');
    } catch (e) {
      env.log(`sound failed: ${String(e)}`);
    }
  }
  if (channels.webhook) {
    try {
      await env.post(channels.webhook, JSON.stringify({ symbol: f.alert.symbol, message: f.message, at: new Date(f.at).toISOString() }));
      sent.push('webhook');
    } catch (e) {
      env.log(`webhook failed: ${String(e)}`);
    }
  }
  return sent;
}

/** The real thing in a browser page. */
export function browserEnv(): Env {
  return {
    notification: typeof Notification === 'undefined' ? null : { get permission() { return Notification.permission; }, create: (title, body) => void new Notification(title, { body }) },
    beep() {
      const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      osc.frequency.value = 880;
      osc.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
    },
    async post(url, body) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), WEBHOOK_TIMEOUT_MS);
      try {
        const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body, signal: controller.signal, mode: 'no-cors' });
        if (r.type !== 'opaque' && !r.ok) throw new Error(`HTTP ${r.status}`);
      } finally {
        clearTimeout(timer);
      }
    },
    log: (m) => console.warn(m),
  };
}
