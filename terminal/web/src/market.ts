const IST_OFFSET_MIN = 330;
const OPEN = 9 * 60 + 15;
const CLOSE = 15 * 60 + 30;
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export interface MarketState {
  open: boolean;
  text: string;
}

/** Whether the exchange should be trading (weekdays 09:15 to 15:30 IST; holidays are not known) and a line for the top bar. */
export function marketState(now: Date): MarketState {
  const ist = new Date(now.getTime() + IST_OFFSET_MIN * 60_000);
  const day = ist.getUTCDay();
  const minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  const weekday = day >= 1 && day <= 5;
  if (weekday && minutes >= OPEN && minutes <= CLOSE) return { open: true, text: 'market open' };
  let next = day;
  let when = 'today';
  if (!weekday || minutes > CLOSE) {
    do {
      next = (next + 1) % 7;
    } while (next === 0 || next === 6);
    when = DAYS[next];
  }
  return { open: false, text: `market closed · showing the last session · opens ${when} 09:15 IST` };
}
