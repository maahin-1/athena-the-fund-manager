import { MIN_EPOCH_MS, type SessionReason, type SessionState } from './quality';

/** The session policy (audit T3 review R3): one rule, in the **server's clock**, for whether the quotes are current activity, a replay or a cache, or from a feed that has gone quiet. Pure; no clock of its own.
 *
 *  What counts as current activity is a packet that was **received lately** (its receipt, the server's clock, within `liveWindowMs` of the server's now) **and** whose own exchange time is close to its
 *  receipt (`maxEventLagMs`). Neither alone is enough:
 *  - an exchange time that merely advances proves nothing (a replay of yesterday advances too): the receipt must be recent and the exchange time must be near it;
 *  - a receipt that is recent proves nothing about the market (a snapshot of a closed market is received when asked for): the packet's own time must be near it;
 *  - contracts the server holds from earlier have old receipts, so asking for another expiry's cached contracts, or re-delivering held rows after a reconnect, is not activity;
 *  - a packet repeated unchanged carries the same receipt (the server does not renew it), so it is not activity either.
 *  A feed that is quiet has no recent receipt: the market may be shut or the stream stopped, and the page cannot tell which (it has no calendar); it says so rather than guess, and the quotes are labelled
 *  observations. An exchange clock more than `maxEventLagMs` behind the server's cannot be told from a replay and reads as one. Packets with no usable exchange time give no evidence either way: unknown.
 *
 *  The state, the reason and the evidence it was decided from are returned together, so a decision can say what produced it. */

export const SESSION_POLICY = {
  id: 'option_session' as const,
  version: 1,
  /** A receipt within this of the server's now is recent. 2 minutes: a quiet contract still speaks that often in a live market of this kind, and the page polls every few seconds. */
  liveWindowMs: 120_000,
  /** A packet's exchange time within this of its receipt (the exchange clock may be a little ahead: `maxExchangeAheadMs`) is current. */
  maxEventLagMs: 120_000,
  maxReceiptAheadMs: 5_000,
  maxExchangeAheadMs: 60_000,
};
export type SessionPolicy = typeof SESSION_POLICY;

/** What the feed has seen of one contract's newest packet: when the server received it and the exchange's own time on it (both ms, either may be missing). */
export interface Observation {
  receivedAt: number | null | undefined;
  eventTime: number | null | undefined;
}

const valid = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n) && n >= MIN_EPOCH_MS;

export function decideSession(observations: Observation[], serverNow: number, policy: SessionPolicy = SESSION_POLICY): SessionState {
  const tag = { id: policy.id, version: policy.version };
  // a receipt ahead of the server's clock beyond the tolerance is not evidence of anything (a clock that rolled back)
  const receipts = observations.filter((o): o is Observation & { receivedAt: number } => valid(o.receivedAt) && o.receivedAt <= serverNow + policy.maxReceiptAheadMs);
  const eventOk = (o: Observation): o is Observation & { eventTime: number } => valid(o.eventTime) && o.eventTime <= serverNow + policy.maxExchangeAheadMs;
  if (!receipts.length) {
    return { state: 'unknown', referenceMs: null, reason: 'no_receipts', policy: tag, evidence: { serverNow, newestReceiptMs: null, newestEventMs: null, eventLagMs: null, receiptAgeMs: null, contracts: observations.length, current: 0 } };
  }
  const newest = receipts.reduce((a, b) => (b.receivedAt > a.receivedAt ? b : a));
  const recent = receipts.filter((o) => serverNow - o.receivedAt <= policy.liveWindowMs);
  const current = recent.filter((o) => eventOk(o) && o.receivedAt - o.eventTime <= policy.maxEventLagMs);
  const events = receipts.filter((o) => eventOk(o)).map((o) => o.eventTime as number);
  const evidence = {
    serverNow, newestReceiptMs: newest.receivedAt, newestEventMs: events.length ? Math.max(...events) : null,
    eventLagMs: eventOk(newest) ? newest.receivedAt - newest.eventTime : null, receiptAgeMs: serverNow - newest.receivedAt, contracts: observations.length, current: current.length,
  };
  const decided = (state: SessionState['state'], reason: SessionReason): SessionState => ({ state, referenceMs: newest.receivedAt, reason, evidence, policy: tag });
  if (current.length) return decided('live', 'recent_packets_with_current_event_times');
  if (recent.length) return recent.every((o) => !eventOk(o)) ? decided('unknown', 'no_event_time') : decided('last_session', 'event_times_old');
  return decided('last_session', 'no_recent_packets');
}

/** The feed badge's words for a session, said once here so the badge and the decisions never disagree. */
export function sessionBadge(s: SessionState): { text: string; title: string } {
  switch (s.state) {
    case 'live':
      return { text: 'live prices', title: 'Prices, volume and open interest as the broker\'s stream last sent them: packets are arriving and carry current exchange times.' };
    case 'last_session':
      return {
        text: 'last session prices',
        title:
          s.reason === 'event_times_old'
            ? 'Packets are arriving but carry old exchange times: the market is shut, or this is a replay or a clock that is far from the server\'s. These are the last prices the exchange sent, not tradable prices.'
            : 'No packet has arrived lately: the market is shut or the feed has stopped (the page cannot tell which). These are the last prices received, not tradable prices.',
      };
    default:
      return {
        text: 'session unknown',
        title:
          s.reason === 'no_event_time'
            ? 'Packets are arriving but carry no usable exchange time, so the page cannot tell current prices from old ones.'
            : 'No quote has been received yet, or the server sends no receipt times: the page cannot tell whether these prices are current.',
      };
  }
}
