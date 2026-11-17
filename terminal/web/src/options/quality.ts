import type { Quote } from './chain';

/** Quote quality for the options page (audit T3): a versioned policy that decides, for each contract and for the forward, whether an implied volatility and Greeks may be shown, shown with a label, or
 *  withheld, and records which inputs and which policy produced that decision.
 *
 *  Times are kept apart, per contract, and none stands in for another:
 *  - **trade time** (`Quote.time`): the exchange's time of the last trade, from the packet's own last-traded field. An old trade says nothing about the book: a fresh book beside an old trade is fresh, and
 *    the volatility is read from the book. It is never the packet's time (a new packet need not carry a new trade).
 *  - **book time** (`Quote.bookTime` where a source gives one, else `Quote.bookSeenAt`, the server's receipt time of the last change of the best bid or ask). A new trade does not refresh it. The broker
 *    stream carries no exchange time for the book, so for it the book time is a receipt time, and the decision says so (`bookTimeBasis`).
 *  - **received at** (`Quote.receivedAt`): when the server received the packet that gave the quote its content. Recorded, never used as the book's age (a trade alone moves it).
 *  - **packet time** (`Quote.packetTime`): the exchange's timestamp of the packet. Recorded; it orders packets (on the server) and tells the session whether activity is current; it is not the book's age.
 *  One contract updating never refreshes another: every decision is made from that contract's own times, and the forward is a separate input with its own time.
 *
 *  A time is valid or it is not, and that is decided apart from age: a value that is not a number, not an epoch time, or ahead of the decision's clock by more than the declared tolerance is **invalid**
 *  (recorded with its value and reason in `inputs.timeFaults`), never a fresh time and never a negative age. The tolerance differs by clock: a server-clock receipt may be a few seconds ahead (the page only
 *  estimates the server's clock); an exchange time may be a minute ahead (another clock).
 *
 *  A quote that no timestamp describes is not called fresh: it is labelled `book_time_unknown`. After the session, ages are measured from the newest quote the feed has seen, every quote is labelled
 *  `session_observation` (an observation, not a tradable price), and a quote older than that reference is still stale. Pure; no clock of its own.
 *
 *  Version 2 (review corrections R3–R6): invalid and future times, a forward that carries its inputs' labels and checks that they are in step, required inputs that are always listed.
 *  Version 3 (T3-C1): every invalid time has a declared consequence on its own contract, applied before the forward and the chain are read, and whatever the aggregate session is: a book time that is invalid
 *  withholds (the age of the price is unknowable); a trade time, a receipt or a packet time that is invalid (found here or refused by the server) labels (the book is still read from its own valid time, and
 *  the label travels to the forward and the chain); a session that is unknown for any reason labels. */

export type QualityFlag =
  | 'no_quote'
  | 'no_two_sided_book'
  | 'crossed_book'
  | 'wide_book'
  | 'stale_book'
  | 'book_time_unknown'
  | 'invalid_book_time'
  | 'invalid_trade_time'
  | 'invalid_receipt_time'
  | 'invalid_packet_time'
  | 'old_trade_price'
  | 'delayed_feed'
  | 'out_of_order_discarded'
  | 'session_observation'
  | 'session_unverified'
  | 'no_forward'
  | 'forward_stale'
  | 'forward_unsynced'
  | 'forward_age_unknown'
  | 'forward_time_invalid'
  | 'forward_input_labelled'
  | 'forward_input_withheld'
  | 'atm_side_absent'
  | 'no_atm';

export type Consequence = 'withhold' | 'label';

export interface QualityPolicy {
  id: 'option_quote_quality';
  /** Bumped whenever a limit, a rule or a consequence changes; every decision records it. */
  version: number;
  /** A book unchanged for longer than this is stale (`age <= limit` is fresh). 15 minutes is the `option_chain` limit of `athena.freshness`. */
  maxBookAgeMs: number;
  /** An old trade matters only where it is the price (the policy does not require a book). */
  maxTradeAgeMs: number;
  /** (ask - bid) / mid above this is a wide book. */
  maxSpreadPct: number;
  requireTwoSidedBook: boolean;
  /** The largest gap between the forward's time and an option's book time, and between the times of the inputs of one forward (`gap <= limit` is in step). */
  maxSyncGapMs: number;
  /** A packet delivered later than this after its exchange time is labelled delayed. The measure includes any difference between the exchange's clock and the server's. */
  maxDeliveryDelayMs: number;
  /** How far ahead of the decision's clock a **server-clock** time (a receipt) may be before it is invalid: the page only estimates the server's clock. `ahead <= limit` is valid. */
  maxReceiptAheadMs: number;
  /** The same for an **exchange-clock** time (a trade time, a book time a source gives): another clock, so a wider tolerance. */
  maxExchangeAheadMs: number;
  /** What each failure does to the volatility and Greeks of the contract it affects. The consequence is the only thing that differs between labelling and withholding. */
  consequences: Record<QualityFlag, Consequence>;
}

export const POLICY_V3: QualityPolicy = {
  id: 'option_quote_quality',
  version: 3,
  maxBookAgeMs: 15 * 60_000,
  maxTradeAgeMs: 15 * 60_000,
  maxSpreadPct: 0.25,
  requireTwoSidedBook: true,
  maxSyncGapMs: 5 * 60_000,
  maxDeliveryDelayMs: 5_000,
  maxReceiptAheadMs: 5_000,
  maxExchangeAheadMs: 60_000,
  consequences: {
    no_quote: 'withhold',
    no_two_sided_book: 'withhold',
    crossed_book: 'withhold',
    wide_book: 'label',
    stale_book: 'withhold',
    book_time_unknown: 'label',
    invalid_book_time: 'withhold',
    invalid_trade_time: 'label',
    invalid_receipt_time: 'label',
    invalid_packet_time: 'label',
    old_trade_price: 'withhold',
    delayed_feed: 'label',
    out_of_order_discarded: 'label',
    session_observation: 'label',
    session_unverified: 'label',
    no_forward: 'withhold',
    forward_stale: 'withhold',
    forward_unsynced: 'withhold',
    forward_age_unknown: 'label',
    forward_time_invalid: 'withhold',
    forward_input_labelled: 'label',
    forward_input_withheld: 'withhold',
    atm_side_absent: 'label',
    no_atm: 'withhold',
  },
};

export type SessionReason = 'recent_packets_with_current_event_times' | 'event_times_old' | 'no_recent_packets' | 'no_event_time' | 'no_receipts';

/** What a session was decided from (all in the server's clock). */
export interface SessionEvidence {
  serverNow: number;
  newestReceiptMs: number | null;
  newestEventMs: number | null;
  /** Receipt less packet time of the newest receipt: how far behind its receipt the exchange's own time was. */
  eventLagMs: number | null;
  receiptAgeMs: number | null;
  contracts: number;
  /** How many contracts had a recent receipt with a packet time close to it. */
  current: number;
}

export interface SessionState {
  state: 'live' | 'last_session' | 'unknown';
  /** The newest quote receipt time the feed has seen (ms): ages are measured from here, not from now, when the session is not live. */
  referenceMs: number | null;
  /** Why (set by `decideSession`): absent for a feed with no notion of a session. */
  reason?: SessionReason;
  evidence?: SessionEvidence;
  policy?: { id: string; version: number };
}

export interface TimeFault {
  /** The quote field: `bookTime`, `bookSeenAt`, `receivedAt`, `tradeTime` or `packetTime`. */
  field: string;
  /** The value as received (a non-number as its text). */
  value: number | string;
  reason: 'not_finite' | 'implausible' | 'future';
  /** For a future time: how far ahead of the decision's clock it was (ms). */
  aheadMs?: number;
  /** Found by this page, or refused by the server before it got here. */
  origin: 'page' | 'server';
}

export interface QuoteInputs {
  now: number;
  /** The time ages were measured from: now, or the newest quote seen after the session. */
  ageBase: number;
  receivedAt: number | null;
  bookSeenAt: number | null;
  bookTime: number | null;
  tradeTime: number | null;
  /** The exchange's timestamp of the packet that gave the quote its content (valid values only). */
  packetTime: number | null;
  /** Which of the above the book's age was taken from. */
  bookTimeBasis: 'book_time' | 'book_seen' | 'none';
  /** The book time used (valid values only): null when there is none or it is invalid. */
  bookAt: number | null;
  bookAgeMs: number | null;
  /** How far ahead of the clock the book time was, within the tolerance (ms); the age is then nil, not negative. */
  bookAheadMs: number | null;
  tradeAgeMs: number | null;
  bid: number | null;
  ask: number | null;
  last: number | null;
  spreadPct: number | null;
  deliveryDelayMs: number | null;
  outOfOrder: number;
  /** Every time on the quote that was not valid, with its value and why. */
  timeFaults: TimeFault[];
  forwardAt: number | null;
  syncGapMs: number | null;
}

export interface QuoteDecision {
  /** `withheld`: no volatility or Greeks. `labelled`: shown, with the flags said. */
  state: 'ok' | 'labelled' | 'withheld';
  flags: QualityFlag[];
  withheld: QualityFlag[];
  labelled: QualityFlag[];
  /** What the volatility is read from: the middle of a two-sided book, else the last trade (only where the policy does not require a book). */
  priceBasis: 'mid' | 'last' | null;
  book: 'fresh' | 'stale' | 'none' | 'crossed' | 'wide' | 'unknown' | 'invalid';
  trade: 'recent' | 'old' | 'none' | 'unknown' | 'invalid';
  inputs: QuoteInputs;
  policy: { id: string; version: number };
  notes: string[];
}

/** One input of a forward: where it came from and what its own decision was. */
export interface ForwardInput {
  role: 'future' | 'call' | 'put';
  /** The strike of an option input; null for the future. */
  strike: number | null;
  state: QuoteDecision['state'];
  flags: QualityFlag[];
  bookAt: number | null;
}

/** A source the forward could have been taken from, whether it was used, and why not. */
export interface ForwardCandidate {
  source: 'future' | 'parity' | 'spot';
  outcome: 'used' | 'rejected' | 'absent';
  reason: string;
  flags: QualityFlag[];
}

export interface ForwardDecision {
  source: 'future' | 'parity' | 'spot' | null;
  value: number | null;
  /** When the forward's inputs were last seen (the oldest of them); null when they carry no time (spot), an input's time is unknown or invalid, or none is known. */
  at: number | null;
  state: 'ok' | 'labelled' | 'withheld';
  flags: QualityFlag[];
  /** A source that was passed over because its own quality failed, and why. */
  rejected: { source: 'future' | 'parity'; flags: QualityFlag[] }[];
  policy: { id: string; version: number };
  /** The contracts the forward was read from, each with its own decision. */
  inputs: ForwardInput[];
  /** Every label of every input, so a warning on an input does not disappear because the input became a forward. */
  inputFlags: QualityFlag[];
  /** The span between the oldest and newest book time among the inputs (ms); null with fewer than two known times. */
  spanMs: number | null;
  /** Every source evaluated, in order, with the outcome and the reason. */
  candidates: ForwardCandidate[];
  /** Contracts that would have been parity inputs but failed their own rules (a quote and a price on both sides of a strike, one of them withheld). */
  excluded: { strike: number; kind: 'call' | 'put'; flags: QualityFlag[] }[];
}

export interface AssessContext {
  now: number;
  session: SessionState;
  /** The forward the option's volatility is read against, with its own decision; null while the forward is not yet chosen. */
  forward: ForwardDecision | null;
  policy?: QualityPolicy;
}

/** The earliest time taken for an epoch time in milliseconds (2000-01-01), as on the server's decoder: anything earlier is not a time. */
export const MIN_EPOCH_MS = 946_684_800_000;

const finite = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n);
const orNull = (n: number | null | undefined): number | null => (finite(n) ? n : null);
const present = (v: unknown): boolean => v !== undefined && v !== null;

interface Checked {
  at: number | null;
  present: boolean;
  fault: Omit<TimeFault, 'field' | 'origin'> | null;
  /** Within the tolerance but ahead of the clock (ms), else 0. */
  aheadMs: number;
}

/** Whether a time is usable at the clock `now`, apart from how old it is: absent is not a fault; not a number, not an epoch time, or ahead beyond `limit` is. */
function checkTime(raw: unknown, now: number, limit: number): Checked {
  if (!present(raw)) return { at: null, present: false, fault: null, aheadMs: 0 };
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return { at: null, present: true, fault: { value: String(raw), reason: 'not_finite' }, aheadMs: 0 };
  if (raw < MIN_EPOCH_MS) return { at: null, present: true, fault: { value: raw, reason: 'implausible' }, aheadMs: 0 };
  const ahead = raw - now;
  if (ahead > limit) return { at: null, present: true, fault: { value: raw, reason: 'future', aheadMs: ahead }, aheadMs: 0 };
  return { at: raw, present: true, fault: null, aheadMs: Math.max(0, ahead) };
}

const faultOf = (field: string, c: Checked): TimeFault[] => (c.fault ? [{ field, ...c.fault, origin: 'page' }] : []);

export interface BookTime {
  /** The book time, when present and valid. */
  at: number | null;
  basis: 'book_time' | 'book_seen' | 'none';
  /** The value was present but invalid. */
  fault: TimeFault | null;
  aheadMs: number;
}

/** The time the book was last seen: the exchange's own book time when a source gives one, else the server's receipt of the last change of the best bid or ask. Never the trade time or the packet's
 *  receipt. A value that is present but invalid is a fault, not a fallback to the other. */
export function bookTimeOf(q: Quote, now: number, policy: QualityPolicy = POLICY_V3): BookTime {
  if (present(q.bookTime)) {
    const c = checkTime(q.bookTime, now, policy.maxExchangeAheadMs);
    return { at: c.at, basis: 'book_time', fault: faultOf('bookTime', c)[0] ?? null, aheadMs: c.aheadMs };
  }
  if (present(q.bookSeenAt)) {
    const c = checkTime(q.bookSeenAt, now, policy.maxReceiptAheadMs);
    return { at: c.at, basis: 'book_seen', fault: faultOf('bookSeenAt', c)[0] ?? null, aheadMs: c.aheadMs };
  }
  return { at: null, basis: 'none', fault: null, aheadMs: 0 };
}

const ageBaseOf = (ctx: { now: number; session: SessionState }): number => (ctx.session.state === 'last_session' && finite(ctx.session.referenceMs) ? ctx.session.referenceMs : ctx.now);

function classify(flags: QualityFlag[], policy: QualityPolicy): { state: QuoteDecision['state']; withheld: QualityFlag[]; labelled: QualityFlag[] } {
  const unique = [...new Set(flags)];
  const withheld = unique.filter((f) => policy.consequences[f] === 'withhold');
  const labelled = unique.filter((f) => policy.consequences[f] === 'label');
  return { state: withheld.length ? 'withheld' : labelled.length ? 'labelled' : 'ok', withheld, labelled };
}

const SERVER_FIELDS: Record<string, string> = { t: 'packetTime', lt: 'tradeTime' };

/** Whether a contract's book, trade and delivery allow a volatility and Greeks to be read from it, and the record of why. */
export function assessQuote(q: Quote | null, ctx: AssessContext): QuoteDecision {
  const policy = ctx.policy ?? POLICY_V3;
  const ageBase = ageBaseOf(ctx);
  const flags: QualityFlag[] = [];
  const notes: string[] = [];
  const book: BookTime = q ? bookTimeOf(q, ctx.now, policy) : { at: null, basis: 'none', fault: null, aheadMs: 0 };
  const bid = orNull(q?.bid);
  const ask = orNull(q?.ask);
  const last = orNull(q?.last);
  const twoSided = bid !== null && ask !== null && bid > 0 && ask > 0;
  const crossed = twoSided && bid! > ask!;
  const mid = twoSided && !crossed ? (bid! + ask!) / 2 : null;
  const spreadPct = mid !== null ? (ask! - bid!) / mid : null;
  const bookAgeMs = book.at === null ? null : Math.max(0, ageBase - book.at);

  // times other than the book's: valid or recorded as faults (the server's refusals first as it sent them)
  const trade = checkTime(q?.time, ctx.now, policy.maxExchangeAheadMs);
  const received = checkTime(q?.receivedAt, ctx.now, policy.maxReceiptAheadMs);
  const packet = checkTime(q?.packetTime, ctx.now, policy.maxExchangeAheadMs);
  const serverFaults: TimeFault[] = (q?.timeFaults ?? []).map((f) => ({ field: SERVER_FIELDS[f.f] ?? f.f, value: f.v, reason: (f.why === 'future' ? 'future' : 'implausible') as TimeFault['reason'], origin: 'server' }));
  const tradeInvalid = trade.fault !== null || serverFaults.some((f) => f.field === 'tradeTime');
  const tradeTime = tradeInvalid ? null : trade.at;
  const tradeAgeMs = tradeTime === null ? null : Math.max(0, ageBase - tradeTime);
  // (T3-C1) a receipt or a packet time that is invalid, found here or refused by the server, has a consequence on this contract of its own, whatever the rest of the chain says about the session: the book
  // is still read (its age comes from its own time), the contract is labelled. A refusal of a field the page does not know is read as a packet time: it is not allowed to be silent.
  const receiptInvalid = received.fault !== null;
  const packetInvalid = packet.fault !== null || serverFaults.some((f) => f.field !== 'tradeTime');
  const timeFaults: TimeFault[] = [...(book.fault ? [book.fault] : []), ...faultOf('receivedAt', received), ...faultOf('tradeTime', trade), ...faultOf('packetTime', packet), ...serverFaults];
  const priceBasis: QuoteDecision['priceBasis'] = mid !== null ? 'mid' : !crossed && !policy.requireTwoSidedBook && last !== null && last > 0 ? 'last' : null;

  let bookState: QuoteDecision['book'];
  if (q === null) {
    flags.push('no_quote');
    bookState = 'none';
  } else if (crossed) {
    flags.push('crossed_book');
    bookState = 'crossed';
  } else if (!twoSided) {
    bookState = 'none';
    if (policy.requireTwoSidedBook || priceBasis === null) flags.push('no_two_sided_book');
  } else if (book.fault) {
    flags.push('invalid_book_time');
    bookState = 'invalid';
    notes.push(`book time invalid: ${book.fault.reason}${book.fault.aheadMs !== undefined ? ` (${Math.round(book.fault.aheadMs / 1000)} s ahead of the clock)` : ''}`);
  } else if (bookAgeMs === null) {
    flags.push('book_time_unknown');
    bookState = 'unknown';
  } else if (bookAgeMs > policy.maxBookAgeMs) {
    flags.push('stale_book');
    bookState = 'stale';
  } else {
    bookState = 'fresh';
  }
  if (spreadPct !== null && spreadPct > policy.maxSpreadPct) {
    flags.push('wide_book');
    if (bookState === 'fresh' || bookState === 'unknown') bookState = 'wide';
  }

  let tradeState: QuoteDecision['trade'];
  if (tradeInvalid) {
    tradeState = 'invalid';
    flags.push('invalid_trade_time');
  } else if (last === null && tradeTime === null) tradeState = 'none';
  else if (tradeAgeMs === null) tradeState = 'unknown';
  else tradeState = tradeAgeMs <= policy.maxTradeAgeMs ? 'recent' : 'old';
  // an old trade matters only where it is the price: with a fresh book the volatility is read from the book and the trade is just old
  if (priceBasis === 'last' && tradeState !== 'recent') flags.push('old_trade_price');

  const delay = orNull(q?.deliveryDelayMs);
  if (delay !== null && delay > policy.maxDeliveryDelayMs) flags.push('delayed_feed');
  if ((q?.outOfOrder ?? 0) > 0) flags.push('out_of_order_discarded');
  if (receiptInvalid) flags.push('invalid_receipt_time');
  if (packetInvalid) flags.push('invalid_packet_time');
  if (ctx.session.state === 'last_session') flags.push('session_observation');
  // (T3-C1) every unknown session is unverified, whatever the reason (no packet time, no receipt, or no session assessed at all)
  if (ctx.session.state === 'unknown') flags.push('session_unverified');

  // the forward: what it says about itself is passed on (its inputs' labels with it), and the option's book must be in step with the forward's inputs (only a book that is itself fresh is compared: a
  // stale one is already withheld)
  let syncGapMs: number | null = null;
  const fwd = ctx.forward;
  if (fwd) {
    for (const f of fwd.flags) flags.push(f);
    if (fwd.inputFlags.length) notes.push(`forward inputs labelled: ${fwd.inputFlags.map(wording).join(', ')}`);
    if ((fwd.source === 'future' || fwd.source === 'parity') && fwd.at !== null && book.at !== null && (bookState === 'fresh' || bookState === 'wide')) {
      syncGapMs = Math.abs(fwd.at - book.at);
      if (syncGapMs > policy.maxSyncGapMs) flags.push('forward_unsynced');
    }
  }

  const c = classify(flags, policy);
  if (c.withheld.length) notes.unshift(`withheld: ${c.withheld.map(wording).join(', ')}`);
  return {
    state: c.state,
    flags: [...new Set(flags)],
    withheld: c.withheld,
    labelled: c.labelled,
    priceBasis,
    book: bookState,
    trade: tradeState,
    notes,
    policy: { id: policy.id, version: policy.version },
    inputs: {
      now: ctx.now, ageBase, receivedAt: received.at, bookSeenAt: orNull(q?.bookSeenAt), bookTime: orNull(q?.bookTime), tradeTime, packetTime: packet.at, bookTimeBasis: book.basis, bookAt: book.at, bookAgeMs,
      bookAheadMs: book.at === null ? null : book.aheadMs, tradeAgeMs, bid, ask, last, spreadPct, deliveryDelayMs: delay, outOfOrder: q?.outOfOrder ?? 0, timeFaults, forwardAt: fwd?.at ?? null, syncGapMs,
    },
  };
}

const WORDS: Record<QualityFlag, string> = {
  no_quote: 'no quote yet',
  no_two_sided_book: 'no two-sided book',
  crossed_book: 'crossed book',
  wide_book: 'wide book',
  stale_book: 'stale book',
  book_time_unknown: 'book time unknown',
  invalid_book_time: 'book time invalid',
  invalid_trade_time: 'trade time invalid',
  invalid_receipt_time: 'receipt time invalid',
  invalid_packet_time: 'packet time invalid',
  old_trade_price: 'old trade as the price',
  delayed_feed: 'delayed feed',
  out_of_order_discarded: 'out-of-order packet discarded',
  session_observation: 'last-session observation, not a tradable price',
  session_unverified: 'session unverified (nothing shows these prices are current)',
  no_forward: 'no forward',
  forward_stale: 'forward stale',
  forward_unsynced: 'forward unsynced',
  forward_age_unknown: 'forward age unknown (from spot)',
  forward_time_invalid: 'forward time invalid',
  forward_input_labelled: 'forward input labelled',
  forward_input_withheld: 'forward input unfit',
  atm_side_absent: 'no contract on this side at the money (one side only)',
  no_atm: 'no at-the-money strike',
};

export const wording = (f: QualityFlag): string => WORDS[f];

export interface ForwardInputs {
  now: number;
  session: SessionState;
  policy?: QualityPolicy;
  /** The source the forward was taken from, its value, and the quotes it was read from (the future's own, the quotes of the strikes the parity used, none for spot). */
  source: ForwardDecision['source'];
  value: number | null;
  used: Quote[];
  /** What each of `used` is (aligned by index); the future by default for a future, else calls. */
  roles?: { role: ForwardInput['role']; strike: number | null }[];
  rejected: ForwardDecision['rejected'];
  candidates?: ForwardCandidate[];
  excluded?: ForwardDecision['excluded'];
}

/** The decision for the forward. Each input is judged by the same rules as an option; the forward carries what they say: an input that is invalid, stale or unfit makes the forward so, an input's labels
 *  are carried (`inputFlags`, `forward_input_labelled`), and the inputs must be in step with each other (the span of their book times within `maxSyncGapMs`), not only each recent. Its time is the oldest of its
 *  inputs; spot has none (labelled); no forward at all is withheld. */
export function decideForward(a: ForwardInputs): ForwardDecision {
  const policy = a.policy ?? POLICY_V3;
  const flags: QualityFlag[] = [];
  let at: number | null = null;
  let spanMs: number | null = null;
  const inputs: ForwardInput[] = [];
  const inputFlags = new Set<QualityFlag>();
  if (a.source === null || a.value === null) {
    flags.push('no_forward');
  } else if (a.source === 'spot') {
    flags.push('forward_age_unknown');
  } else {
    const own: AssessContext = { now: a.now, session: a.session, forward: null, policy };
    const decisions = a.used.map((q) => assessQuote(q, own));
    decisions.forEach((d, i) => inputs.push({ role: a.roles?.[i]?.role ?? (a.source === 'future' ? 'future' : 'call'), strike: a.roles?.[i]?.strike ?? null, state: d.state, flags: d.flags, bookAt: d.inputs.bookAt }));
    const invalid = decisions.some((d) => d.flags.includes('invalid_book_time'));
    const stale = decisions.some((d) => d.flags.includes('stale_book'));
    const unfit = decisions.some((d) => d.withheld.some((f) => f !== 'invalid_book_time' && f !== 'stale_book'));
    const times = decisions.map((d) => d.inputs.bookAt).filter((t): t is number => t !== null);
    const unknown = !invalid && (decisions.length === 0 || times.length < decisions.length);
    if (invalid) flags.push('forward_time_invalid');
    if (stale) flags.push('forward_stale');
    if (unknown) flags.push('forward_age_unknown');
    if (!invalid && !unknown && times.length) at = Math.min(...times);
    if (times.length >= 2) {
      spanMs = Math.max(...times) - Math.min(...times);
      if (spanMs > policy.maxSyncGapMs) flags.push('forward_unsynced');
    }
    if (unfit) flags.push('forward_input_withheld');
    for (const d of decisions) for (const f of d.labelled) if (f !== 'book_time_unknown' && f !== 'session_observation' && f !== 'session_unverified') inputFlags.add(f);
    if (inputFlags.size) flags.push('forward_input_labelled');
  }
  if (a.session.state === 'last_session' && a.source !== null) flags.push('session_observation');
  if (a.session.state === 'unknown' && a.source !== null) flags.push('session_unverified');
  const c = classify(flags, policy);
  return {
    source: a.source, value: a.value, at, state: c.state, flags: [...new Set(flags)], rejected: a.rejected, policy: { id: policy.id, version: policy.version },
    inputs, inputFlags: [...inputFlags], spanMs, candidates: a.candidates ?? [], excluded: a.excluded ?? [],
  };
}

export type RequiredInput = 'forward' | 'atm_call' | 'atm_put';

export interface RequiredEntry {
  input: RequiredInput;
  state: QuoteDecision['state'];
  flags: QualityFlag[];
  /** `present`: the input exists and was judged. `absent`: the chain has an at-the-money strike but no contract on this side of it. `no_atm`: there is no at-the-money strike to look at. */
  presence: 'present' | 'absent' | 'no_atm';
}

export interface ChainQuality {
  policy: { id: string; version: number };
  now: number;
  session: SessionState;
  forward: ForwardDecision;
  /** The worst state of the inputs the headline statistics (forward, at-the-money volatility) need: the chain's badge. Not the newest quote and not the best row. */
  status: 'ok' | 'labelled' | 'withheld';
  worst: { input: RequiredInput; state: 'labelled' | 'withheld'; flags: QualityFlag[] } | null;
  /** Always the forward, the at-the-money call and the at-the-money put, whatever is missing: a missing one is listed with its flag. */
  required: RequiredEntry[];
  /** Which sides of the at-the-money strike the headline can be read from. */
  coverage: 'both' | 'call_only' | 'put_only' | 'none';
  /** How many call and put sides are in each state; a withheld wing is not a required input and does not decide the status. */
  rows: { ok: number; labelled: number; withheld: number };
  /** Age of the newest valid quote time of any contract: shown for context, never used to judge the chain, never negative. */
  newestQuoteAgeMs: number | null;
}

const SEVERITY = { ok: 0, labelled: 1, withheld: 2 } as const;

const INPUT_WORDS: Record<RequiredInput, string> = { forward: 'forward', atm_call: 'at-the-money call', atm_put: 'at-the-money put' };
const COVERAGE_WORDS: Record<ChainQuality['coverage'], string> = { both: 'both sides', call_only: 'call only', put_only: 'put only', none: 'neither side' };

/** The badge's tooltip: each required input that is not fit and why, the forward's source (its inputs' labels, any source passed over), how many contract sides are in each state, and the policy. */
export function qualitySummary(q: ChainQuality): string {
  const lines: string[] = [];
  for (const r of q.required) if (r.state !== 'ok') lines.push(`${INPUT_WORDS[r.input]} (${r.state}): ${r.flags.map(wording).join(', ')}`);
  const f = q.forward;
  const passed = f.rejected.map((x) => `${x.source} passed over: ${x.flags.map(wording).join(', ')}`);
  lines.push(`Forward from ${f.source ?? 'nothing'}${passed.length ? ` (${passed.join('; ')})` : ''}.`);
  if (f.inputFlags.length) lines.push(`The forward's inputs are labelled: ${f.inputFlags.map(wording).join(', ')}.`);
  if (f.spanMs !== null) lines.push(`The forward's inputs span ${Math.round(f.spanMs / 1000)} s.`);
  if (q.coverage !== 'both') lines.push(`At-the-money coverage: ${COVERAGE_WORDS[q.coverage]}.`);
  lines.push(`Contract sides: ${q.rows.ok} fit, ${q.rows.labelled} labelled, ${q.rows.withheld} withheld.`);
  if (q.newestQuoteAgeMs !== null) lines.push(`The newest quote is ${Math.round(q.newestQuoteAgeMs / 1000)} s old; that does not decide the chain.`);
  lines.push(`Policy ${q.policy.id} version ${q.policy.version}.`);
  return lines.join('\n');
}

export function chainQuality(a: {
  now: number; session: SessionState; policy?: QualityPolicy; forward: ForwardDecision;
  /** The at-the-money strike, or null when there is none (no strikes, or no forward to find it from). */
  atm: number | null; atmCall: QuoteDecision | null; atmPut: QuoteDecision | null; sides: QuoteDecision[]; quotes: Quote[];
}): ChainQuality {
  const policy = a.policy ?? POLICY_V3;
  const entry = (input: 'atm_call' | 'atm_put', d: QuoteDecision | null, other: QuoteDecision | null): RequiredEntry => {
    if (d) return { input, state: d.state, flags: d.flags, presence: 'present' };
    const flag: QualityFlag = a.atm !== null && other ? 'atm_side_absent' : 'no_atm';
    return { input, state: classify([flag], policy).state, flags: [flag], presence: flag === 'no_atm' ? 'no_atm' : 'absent' };
  };
  const required: RequiredEntry[] = [{ input: 'forward', state: a.forward.state, flags: a.forward.flags, presence: 'present' }, entry('atm_call', a.atmCall, a.atmPut), entry('atm_put', a.atmPut, a.atmCall)];
  let worstItem: RequiredEntry | null = null;
  for (const r of required) if (SEVERITY[r.state] > SEVERITY[worstItem?.state ?? 'ok']) worstItem = r;
  const rows = { ok: 0, labelled: 0, withheld: 0 };
  for (const d of a.sides) rows[d.state] += 1;
  // only times that are valid at this moment: a future time is not a negative age
  const times = a.quotes.flatMap((q) => [q.receivedAt, q.bookSeenAt, q.bookTime, q.time]).filter((t): t is number => finite(t) && t >= MIN_EPOCH_MS && t <= a.now);
  const coverage: ChainQuality['coverage'] = a.atmCall && a.atmPut ? 'both' : a.atmCall ? 'call_only' : a.atmPut ? 'put_only' : 'none';
  return {
    policy: { id: policy.id, version: policy.version },
    now: a.now,
    session: a.session,
    forward: a.forward,
    status: worstItem ? (worstItem.state as 'labelled' | 'withheld') : 'ok',
    worst: worstItem ? { input: worstItem.input, state: worstItem.state as 'labelled' | 'withheld', flags: worstItem.flags } : null,
    required,
    coverage,
    rows,
    newestQuoteAgeMs: times.length ? a.now - Math.max(...times) : null,
  };
}
