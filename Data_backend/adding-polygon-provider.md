# Integrating Polygon — An Architectural Guide

This document explains **how to add Polygon as a provider** while respecting the
clean, layered architecture this relay is built on. It is deliberately
**architectural** — it talks about *which layer owns what* and *what contracts to
honour*, not about specific Polygon endpoints or query parameters. Treat
Polygon as a "massive" provider (broad asset coverage: stocks, options, crypto,
forex, indices, aggregates, snapshots), and let the layering keep that breadth
contained.

---

## 1. The guiding principle

> **Provider-specific knowledge lives in exactly one place: the adapter.**

Every other layer of the system speaks only in *normalized* terms (canonical
tickers, normalized timeframes, `OHLCCandle` / `OptionContractSnapshot`, the
standard response envelope). The moment Polygon's URL shapes, auth scheme,
pagination style, or field names leak past the adapter boundary, the
architecture has been violated. The single test for every line of Polygon code
you write is: *"Could a consumer of this relay ever tell that Polygon — rather
than Kraken or EODHD — served this response?"* The answer must be **no**.

Because Polygon is large, the temptation to special-case it upstream (in
services, controllers, routes) is high. Resist it. Breadth is absorbed *inside*
the adapter by composing capabilities, not by widening the public contract.

---

## 2. The layers, top to bottom

The relay is a strict downward dependency chain. Each layer depends only on the
one below it and on shared `types/` and `utils/`. Polygon work touches the
layers as follows:

```
routes/        validation + wiring          → little/no change
controllers/   HTTP ↔ service translation    → little/no change
services/      orchestration (cache→limit→adapter) → provider-agnostic, no change
providers/     ← THE ADAPTER LIVES HERE → almost all your work
config/        env, db, cache               → add credentials + (maybe) cache keys
types/         normalized contracts         → only if a new capability appears
```

The deeper a change sits, the more carefully it must preserve existing
contracts. Most of your effort should land in `providers/`; the higher layers
should barely notice Polygon exists.

### 2.1 Routes & Controllers (top) — usually untouched

Routes validate input (Joi) and map URLs to controller methods. Controllers
adapt `req`/`res` to a service call and wrap the result in the standard
envelope via `successResponse`. **Neither layer knows provider names beyond a
validation allow-list.** Adding Polygon means, at most:

- widening a Joi `provider` enum (e.g. `valid('kraken', 'eodhd', 'polygon')`),
- nothing else, if Polygon serves an already-modelled asset class.

Controllers stay thin: parse → delegate → envelope → `next(err)`. No Polygon
branching ever appears here.

### 2.2 Services (middle) — provider-agnostic orchestration

Services own the **cache → rate-limit → adapter → cache** flow and are written
against *interfaces*, never concrete adapters. Look at how `ohlcService` and
`optionsService` work:

1. Build a cache key, check the in-memory cache, return early on a hit.
2. Resolve the adapter through the **registry** (`getAdapter(slug)`), never by
   importing a concrete class.
3. Acquire a rate-limit token (`rateLimiterService.acquireToken(slug, cost)`).
4. Call the normalized adapter method.
5. Store the normalized result in the cache with an appropriate TTL.

**Polygon requires no new service** if it serves asset classes the relay
already models. `getAdapter('polygon')` returns your adapter; the existing flow
caches and rate-limits it automatically. You only touch a service if Polygon
introduces a genuinely *new capability* (see §5) — and even then you extend via
a capability interface, not a Polygon-named branch.

### 2.3 Providers (bottom) — where Polygon actually lives

This is the home of all Polygon code. Two artifacts:

- `providers/PolygonAdapter.ts` — extends `BaseAdapter`, implements the
  `ProviderAdapter` contract (and any capability interfaces it supports).
- one line in `providers/registry.ts` — `registerAdapter(new PolygonAdapter())`.

Everything Polygon-shaped — base URL, API-key injection, endpoint paths,
pagination, response parsing, timeframe translation, error-shape handling — is
sealed inside this class.

---

## 3. The adapter contract

### 3.1 Extend `BaseAdapter`

`BaseAdapter` gives you a preconfigured Axios instance (base URL + default
timeout) and a single normalized error path, `handleError()`. Your adapter:

```ts
export class PolygonAdapter extends BaseAdapter {
  constructor() {
    super('polygon', 'https://api.polygon.io');
  }

  mapTimeframe(timeframe: string): string { /* relay → Polygon interval */ }

  async fetchOHLC(params: FetchOHLCParams): Promise<OHLCCandle[]> {
    try {
      // build request, call this.http.get(...), map → OHLCCandle[]
    } catch (err) {
      this.handleError(err); // never throws raw provider/axios errors upward
    }
  }
}
```

Three contracts are non-negotiable:

1. **`slug` is the identity.** It must match the `providers.slug` row in the DB
   and the value consumers pass as `?provider=polygon`. The registry, rate
   limiter, and cache keys all pivot on it.
2. **Return normalized shapes only.** Map Polygon's response into `OHLCCandle`
   (and option types) — including the relay conventions: timestamps in **unix
   ms**, OHLCV as **decimal strings** (preserve precision — never coerce to JS
   `number` and back), and `isClosed` computed as "every candle except the most
   recent is closed."
3. **Funnel all failures through `handleError()`.** Wrap your request body in
   `try/catch` and call `this.handleError(err)` so every upstream failure
   becomes a `ProviderError` carrying `providerSlug` + status. Polygon's error
   envelope differs from EODHD's and Kraken's — *teach `handleError` (or a local
   pre-parse) to read Polygon's shape*, then throw the same normalized error.
   The global `errorHandler` middleware turns that into the standard envelope.

### 3.2 Timeframe & symbol translation stay inside the adapter

`mapTimeframe()` is the only place that knows Polygon's interval encoding —
mirror the `TIMEFRAME_MAP` pattern in the existing adapters. Likewise,
**symbol mapping is data, not code**: the relay's canonical ticker → Polygon's
native ticker mapping lives in the `provider_symbols` table
(`provider_ticker`, plus `extra_meta` for anything provider-specific). The
service hands your adapter the already-resolved `providerTicker`; the adapter
never reverse-engineers symbols.

---

## 4. Registration, config, rate limits, cache

These are the "wire it in" steps — each is a small, well-defined touch in a
layer *below* the services.

- **Registry** (`providers/registry.ts`): add
  `registerAdapter(new PolygonAdapter())`. This is the *only* place a concrete
  Polygon class is instantiated; everywhere else resolves it by slug. This is
  the seam that keeps the rest of the system from depending on Polygon.

- **Credentials** (`config/env.ts` + `.env.example`): add `POLYGON_API_KEY` as a
  validated env var and read it via `config.polygon.apiKey` inside the adapter.
  **Provider secrets live in the environment, never in the database** — the DB
  holds only metadata.

- **Provider metadata (DB + seed):** insert a `providers` row (`slug='polygon'`,
  `base_url`, `rate_limit_rpm`) and the relevant `provider_symbols` mappings via
  a migration/seed. The rate limiter and `providerService` read this row; no
  code hard-codes Polygon's limits.

- **Rate limiting** (`services/rateLimiter.ts`): nothing to write — it builds a
  per-slug token bucket from `rate_limit_rpm` automatically. Your only decision
  is the **cost** weight per request: if a Polygon call consumes more than one
  unit of its real quota (as EODHD options bill ~10), pass that `cost` into
  `acquireToken(slug, cost)` from the service. This keeps the relay honest about
  Polygon's actual budget without leaking Polygon details upward.

- **Caching** (`config/cache.ts`): existing OHLC/options key builders already
  namespace by `providerSymbolId`/`provider`, so Polygon coexists with no
  collisions. Add a new `build*Key` + TTL constant **only** if Polygon brings a
  new data type. Mind cardinality — `maxKeys` bounds memory, and a "massive"
  provider with many high-cardinality keys (per-strike, per-expiry) can pressure
  it; choose TTLs that match how fast the data moves.

---

## 5. Handling Polygon's breadth: capability interfaces, not god-objects

This is the crux of integrating a *massive* provider cleanly. The base
`ProviderAdapter` contract is intentionally **minimal** (`fetchOHLC`,
`mapTimeframe`). Anything beyond that is modelled as an **optional capability
interface** that an adapter may also implement — exactly how options support
works today:

```ts
export interface OptionsCapableAdapter extends ProviderAdapter { /* … */ }

export function supportsOptions(a: ProviderAdapter): a is OptionsCapableAdapter {
  return typeof (a as OptionsCapableAdapter).searchOptionContracts === 'function';
}
```

Services *narrow* with the `supportsOptions(adapter)` guard before using the
extra methods, and return a clean `OPTIONS_NOT_SUPPORTED` error otherwise. Apply
the same pattern to Polygon:

- Polygon serves OHLC for stocks/crypto/forex → it implements `ProviderAdapter`.
- Polygon serves options → it *also* implements `OptionsCapableAdapter`.
- Polygon brings something genuinely new the relay should expose (e.g. a new
  instrument family or data shape) → define a **new capability interface** +
  its `supportsX` type guard in `types/provider.ts`, add the normalized result
  types in `types/`, and have the relevant service narrow on it.

**Never** widen the base `ProviderAdapter` with Polygon-only methods, and never
add `if (provider === 'polygon')` branches in services. Breadth is expressed as
*more capabilities a provider opts into*, which keeps Kraken/EODHD untouched and
each adapter implementing only what it actually does. The capability set grows;
the existing contracts don't break.

---

## 6. Definition of done — the architectural checklist

A Polygon integration is "clean" when all of these hold:

- [ ] All Polygon-specific logic is inside `PolygonAdapter` (+ one registry line).
- [ ] No layer above `providers/` mentions `"polygon"` except a Joi allow-list
      and the DB metadata row.
- [ ] No service contains provider-name branching; capabilities are resolved via
      `supportsX()` guards.
- [ ] Adapter returns only normalized types (unix-ms timestamps, decimal-string
      OHLCV, correct `isClosed`).
- [ ] Every upstream failure exits through `handleError()` as a `ProviderError`.
- [ ] Credentials are in env/`config`, metadata + rate limit + symbol maps are in
      the DB, and request `cost` reflects Polygon's real quota usage.
- [ ] Caching reuses existing key builders (or adds clearly-namespaced new ones
      with TTLs matched to data volatility).
- [ ] New behaviour is expressed as capability interfaces, never by widening the
      base `ProviderAdapter`.

If you can swap `?provider=eodhd` for `?provider=polygon` and the consumer sees
the same envelope, the same normalized candles, and the same error semantics —
just sourced from Polygon — the integration honours the architecture.
```