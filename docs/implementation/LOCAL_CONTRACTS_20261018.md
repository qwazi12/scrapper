# Local evidence, budget and lease contracts — 2026-10-18

This checkpoint follows the owner's requested order. It remains local, on the isolated feature branch. No push, deployment, publication, live account change or paid request was performed. Tests use disposable SQLite and fake provider boundaries; external spend is $0.

## Independent evidence retrieval and review

```mermaid
flowchart LR
  G[Generated research suggestions] --> U[Unverified; excluded from web narration]
  U --> F[Owner requests HTTPS retrieval]
  F --> P[Public-IP pin; TLS host check; validate every redirect]
  P --> R[Immutable bounded text record + hash + retrieval time]
  R --> H[Owner reviews exact excerpt and semantic support]
  H --> C[Exact project, claim, title, year, medium, territory]
  C --> V[Seven-day freshness and conflict checks]
  V --> B[Citable storyboard web finding]
  H --> I[Invalidate saved narration, plan, render and approval]
```

Source records and claim reviews are separate database rows. Model-provided excerpts, hashes and review IDs cannot independently authorize a fact. Retrieval removes scripts/styles and accepts only bounded uncompressed HTML or plain text from the existing editorial allowlist. Network tests replace the transport and DNS; no real article was fetched in this session.

A phrase appearing in a denial is not automatically support. Semantic support is an explicit owner decision with rationale and a context confirmation. This is independent retrieval plus human review, not an autonomous truth engine or evidence that every TMDB fact is independently correct. Structured TMDB facts, trailer analysis, provider availability and native opportunity briefs still have distinct provenance and require broader material-claim verification. There is no general cross-source contradiction resolver.

Changing reviews invalidates narration checks and downstream work while preserving owner words. Stored web findings are rechecked for freshness before plan/render/enqueue through the saved storyboard gate. Raw TMDB details remain visible. Owner-reviewed verdicts are labeled as such, with freshness checked at use time.

## Atomic budget reservations

```mermaid
stateDiagram-v2
  [*] --> reserved: DB mutex + integer micro-dollar admission
  reserved --> dispatched: before provider request
  reserved --> released: lease lost before dispatch
  dispatched --> settled: complete usage event in same transaction
  dispatched --> unknown: timeout, failure or missing receipt
  dispatched --> breach: actual exceeds declared ceiling
  unknown --> settled: explicit audited receipt reconciliation
  breach --> settled: explicit audited reconciliation
```

SQLite writer serialization / PostgreSQL row locking protects admission across sessions and processes. Daily and monthly budgets include every unsettled USD hold, including holds from previous days/months. Monthly admission includes fixed costs. Each retry reserves separately. Accounting errors propagate; work does not silently continue without a durable receipt. Missing usage is recorded as unresolved rather than zero-cost success. Chirp free-tier accounting is serialized.

Known text calls derive a conservative token bound from UTF-8 request bytes and the request's output-token limit. Unknown models, image/audio requests and grounding fan-out require explicit positive `costs.request_bounds["service:model"]` ceilings. These settings describe operator-declared cost contracts; they do not certify current provider pricing or guarantee an external invoice. No values were configured on any live account. Requests without a bound stop before provider IO, including the existing unbounded Gemini speech routes.

The spending card includes reserved funds and explains that uncertain holds survive midnight. Owner-authenticated local API: `GET /api/costs/reservations`; `POST /api/costs/reservations/{id}/resolve` requires `actual_usd`, a provider receipt reference and a substantive rationale. Reconciliation is recorded once in `budget_resolutions`; it cannot silently repeat.

vidIQ has an independent integer-credit ledger/hold table under the same DB mutex. Unknown tool prices fail closed; timeouts preserve the hold and paid tool calls are not blindly retried. Existing price entries are estimates, not a verified current provider tariff.

Remaining budget work: provider invoice reconciliation and confirmed price/usage contracts; orphan reserved/dispatched holds after a hard process crash; credit-hold reconciliation UI/API; full spending settings UI for declared bounds; refund allocation across calendar boundaries. These holds deliberately remain conservative rather than expiring into available money.

## Worker leases and fencing

```mermaid
flowchart LR
  A[Atomic resource claim] --> T[Owner + increasing generation + expiry]
  T --> K[Invocation keepalive]
  T --> D[Fenced ORM and bulk DB writes]
  T --> P[Guard paid dispatch and checkpoint]
  T --> F[Reject stale completion and renewal]
  T --> S[Generation-specific Studio output folders]
  O[Owner stop] --> X[Revoke generation]
  X --> D
```

Applied to resumable Studio/Drive/bulk jobs, Countdown research/render and synchronous native campaign renders. Resume claims compare the scanned job state and acquire the expired resource in one transaction. A process cannot launch the same owned invocation twice. Expired leases cannot revive themselves. Worker-thread helpers inherit the parent lease context. Keepalive also operates for owner-triggered work in web-only mode.

Studio assets, footage, stills, narration and render files use attempt folders. Old workers can leave abandoned files in their own folder, but cannot promote their database pointer over a later generation. Previous immutable segment-cache entries are copied into the new attempt to preserve reuse. Archive discovery recognizes the new folders. Native exports already have separate immutable version folders; a render left running by a dead process can recover after lease expiry without replacing old exports.

Limits: legacy scrape/compile worker claims and residential download handoffs are not fully integrated; generic transfer/cache/deletion leases remain open. External services cannot be fenced by a local database token: in-flight delivery remains governed by persisted intent, idempotency keys and unresolved outcomes. SQLite lease concurrency and separate-process USD reservation tests ran; PostgreSQL behavior, clock-skew handling and rolling deployment with old worker versions have not been exercised. Do not deploy this as an all-workers release contract.

## Verification

Use the latest logs referenced by `SCRAPPER_COMPLETION_REPORT.md`. New tests cover unsafe source URLs and mixed private DNS, pinned redirect retrieval/content bounds, denial-versus-support review, changed identities and stale records; concurrent USD reservations in separate sessions and Python processes; timeout holds across month boundaries; atomic settlement and explicit audited resolution; simultaneous credit attempts; stale worker writes, bulk writes, renewal, completion and paid admission; stop revocation; duplicate local invocation; and native crash recovery.

The browser corpus adds the real review UI: retrieve fixture record, inspect it, submit an explicit verdict, invalidate narration, recheck saved words and reload the durable record. Transport fixtures are declared as fixtures; this does not prove live article extraction or live provider compatibility.


Final verified results: backend **395 passed /1 optional skip /37.16s**, production frontend build5 and explicit TypeScript check exit 0; browser3 **6 passed /2.4min**. Backend thread exceptions are test errors. Final isolation has zero external attempts, no scheduler threads, five explicit fake receipts and $0 spend. The broader live-provider, PostgreSQL, kill/restart and deployment limitations above remain.
