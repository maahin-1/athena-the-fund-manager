/** What the server's `/api/derivs` routes return, and the page's way of asking. */

export interface UnderlyingInfo {
  underlying: string;
  segment: 'NFO' | 'BFO' | 'MCX';
  class: 'index' | 'stock' | 'commodity';
  /** A test or odd name the page hides by default. */
  odd: boolean;
  expiries: number;
  lot: number;
  futures: boolean;
  options: boolean;
}

export interface ExpiryInfo {
  date: string;
  futures: boolean;
  options: boolean;
  series: 'weekly' | 'monthly';
}

export interface ContractRef {
  token: string;
  symbol: string;
  lot: number;
}

export interface StructureRow {
  strike: number;
  call: ContractRef | null;
  put: ContractRef | null;
}

export interface ChainStructure {
  underlying: string;
  expiry: string;
  segment: string | null;
  class: string | null;
  lot: number | null;
  tick: number | null;
  future: ContractRef | null;
  strikes: StructureRow[];
}

export interface FutureInfo {
  expiry: string;
  token: string;
  symbol: string;
  lot: number;
  tick: number;
  segment: string;
}

export interface OptionsApi {
  underlyings(): Promise<UnderlyingInfo[]>;
  expiries(underlying: string): Promise<ExpiryInfo[]>;
  chain(underlying: string, expiry: string): Promise<ChainStructure>;
  futures(underlying: string): Promise<FutureInfo[]>;
}

/** A failure with the server's own words, so the page can say what happened (the contract list loading, an unknown underlying). */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export function serverOptionsApi(fetcher: typeof fetch = fetch): OptionsApi {
  async function get<T>(path: string): Promise<T> {
    let r: Response;
    try {
      r = await fetcher(path);
    } catch {
      throw new ApiError('the server did not answer', 0);
    }
    if (!r.ok) throw new ApiError((await r.text().catch(() => '')) || `HTTP ${r.status}`, r.status);
    return (await r.json()) as T;
  }
  const q = encodeURIComponent;
  return {
    underlyings: async () => (await get<{ underlyings: UnderlyingInfo[] }>('/api/derivs/underlyings?segment=ALL')).underlyings,
    expiries: async (u) => (await get<{ expiries: ExpiryInfo[] }>(`/api/derivs/expiries?underlying=${q(u)}`)).expiries,
    chain: (u, e) => get<ChainStructure>(`/api/derivs/chain?underlying=${q(u)}&expiry=${q(e)}`),
    futures: async (u) => (await get<{ futures: FutureInfo[] }>(`/api/derivs/futures?underlying=${q(u)}`)).futures,
  };
}
