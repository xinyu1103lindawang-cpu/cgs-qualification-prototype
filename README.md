# CGS Qualification Workspace — Functional Prototype v2

An English consultant workspace for steps 5–7, with a local human-review step 8. This version adds a real Python backend, SQLite persistence and a configurable OpenAI Responses API adapter. It preserves the existing CGS policy: **any recorded client reply makes the case Active**. Active and qualification readiness are separate.

## Run

Requires Python 3.10 or newer; the backend uses only the standard library. No package installation or frontend build is required.

```sh
cd /path/to/cgs-prototype
python3 server.py --port 8765
```

Open **http://127.0.0.1:8765/**. Keep the terminal running. On this Mac, `Start CGS.command` also locates the bundled Python runtime or falls back to `python3`.

The previous static `python3 -m http.server` command is no longer sufficient. The application now needs its backend. The old v1 browser-local storage is left untouched; it is not automatically imported into the new database.

## Connect a real model

1. Copy `.env.example` to `.env` beside `server.py`.
2. Set `OPENAI_API_KEY` and `OPENAI_MODEL`. Choose an API model available to your account that supports Structured Outputs; the app does not assume a particular model entitlement.
3. Restart `server.py`.
4. In **Settings & agents**, select **Live · OpenAI Responses API**, then **Save mode**.
5. Add a new source and analyze it. A successful evidence run shows separate A2 and A3 model calls, with request and response IDs under **Model call trace**.

Credentials are read by the server, never entered into the frontend, returned in bootstrap data or stored in localStorage. The endpoint is `https://api.openai.com/v1/responses`; structured output uses `text.format` with `type: json_schema` and `strict: true`. `store` is false. Each call has a 75-second connection/read timeout and a bounded output size. Retries are explicit user actions; there is no automatic retry loop or silent demo fallback.

In live mode, the source text and relevant case context are sent to OpenAI. A2 extracts quotes; A3 proposes rubric judgements. Exact quotation matching, source references, field types, valid scores and proposed condition targets are validated again server-side. Structured JSON does not establish factual truth: the consultant still reviews every suggestion.

**Verification boundary:** live request/response contracts are covered using an injected test transport. No paid live model call was made during this build because no API key and model were configured. Verify real credentials, model access, response quality and latency before a CGS pilot.

## Demo mode

Demo mode is available without any API credentials. It is explicitly labelled **Demo text rules** and is a deterministic English parser, not an LLM. The entered text determines results; there is no scenario selector controlling the answer.

Conservative demo patterns recognize examples such as:

- `Our project budget is approved and covers the initial scope.` → proposed B=4.
- `Our CFO is joining the next discussion.` → proposed A=4.
- `This is our first project together.` → proposed H=2.5.
- `We shared the requested process maps.` → proposed E=3.
- `Thank you for sending this through.` → courtesy reply, proposed E=1 when applicable.
- `We are open to external consulting support.` → external-support condition.
- `We are not considering external consulting support for this project.` → external-support blocker proposals.

Arbitrary wording, complex negation and other languages require live mode or manual review. Some conditional, negative and future-promise phrases are deliberately skipped in demo mode; the demo is not a general natural-language engine. **Load sample text** only fills an editable text field. It does not choose a fixed result. Sample organizations and baseline evidence are marked as samples.

## Five-minute walkthrough (fresh sample database)

1. **Summit Energy → 5 Prepare & contact:** generate an outreach draft, edit and save it. Record a courtesy reply. The case becomes Active before evidence scoring; other qualification details remain pending.
2. **Cedar Bay Logistics → 6 Explore & capture:** add the approved-budget example and the process-map example in one note. Analyze it. Review two separate, source-linked suggestions.
3. Accept only the B suggestion. Q changes **69 → 74** and P changes **73.4 → 76.4**. E stays unchanged until its separate review. The accepted evidence and version explain the change.
4. Review the remaining suggestion. Open **7 Assess readiness** and expand a dimension to see its evidence and scoring anchor. Generate next-action advice; turning a suggestion into a task requires confirmation of its owner and agreed date.
5. Open the qualification pack and submit it. In **Approvals**, review the frozen snapshot and record a local human decision. Changing the case after submission prevents approval until it is returned and resubmitted. Changes after an approval mark the previous decision as historical and allow a new submission, preserving the earlier decision.
6. Open **Atlas Financial**: P=94.6 does not override an explicit refusal of external support. Open **Meridian Industries**: missing Authority remains null, coverage is 80%, Q is 60–80 and P is 68–80; it is not Ready.

If the workspace already contains verification changes, use another database with `CGS_DB_PATH`, or explicitly reset the sample workspace in Settings. Reset deletes only records in this local prototype database; it is a deliberate action with a confirmation screen. Export any packs you want to retain first.

## What is functional

- Case creation, search, readiness and owner filtering, and priority sorting with revenue-pending cases in a separately labelled group.
- Clear step 5/6/7 navigation and separate Active/readiness indicators.
- Text paste or `.txt` import with source type, label and evidence date.
- Original-source persistence independent of analysis success; future-dated sources are rejected.
- One source → multiple factual findings → multiple reviewable rating, condition or explicit-blocker proposals.
- Per-proposal Accept/Edit/Reject with mandatory rationale for edits, rejection and potential conflicts.
- Source quotations, human-review provenance, rating anchors and versioned before/after totals.
- Manual score, revenue and qualification-condition reviews, with null preserved.
- Manual source-review acknowledgement when analysis is unavailable; the consultant records any assessment changes separately.
- Server-authoritative calculations, validation and optimistic case revision checks.
- Saved and editable outreach drafts; up to three contextual next-action suggestions.
- Suggested actions remain separate from agreed tasks; completion never automatically changes a score.
- Background overdue/stalled-case monitoring every 30 seconds while the server runs, including when the browser is closed; reminders are persisted and snoozable.
- Qualification-pack submission, frozen snapshots, local human decisions and stale-pack checks.
- CSV queue export and printable HTML pack export.
- Real provider failure, refusal and incomplete-response handling; interrupted analyses become retryable Failed records after restart.

Standard submission requires an Active case that meets the Ready rules, no unreviewed proposals, and no latest failed/running source analysis without a manual source review. Re-analysis supersedes old unreviewed proposals **from the same source**, preserving old reviewed decisions and analysis history. It does not silently resolve conflicts or clear blockers. Only one analysis may run per case at a time.

## Proposed scoring rules

Weights: B25, A20, N25, T15, H5, E10; scores 0–5, with H=2.5 only for explicitly confirmed first-time collaboration. Unknown is null, not zero.

`Q = Σ(weight × score / 5)`; missing dimensions retain lower–upper bounds and weighted coverage. Revenue R is 100 / 80 / 60 / 40 for tiers 5 / 4 / 3 / 2. Unknown revenue has no tier or observed R; [0,100] are uncertainty bounds only. `P = 0.6Q + 0.4R` retains the resulting interval.

Ready requires: Q lower bound ≥70; coverage ≥80%; B≥2, A≥2, N≥3, T≥2 and E≥2; all six qualification conditions confirmed; no explicit blocker. Mandatory dimensions already carry 95% of weight, so 80% coverage cannot bypass an unconfirmed mandatory dimension. A high P does not bypass qualification gates.

Revenue currency USD, first-12-month scope, weights, thresholds, 14-day progress window and service-fit decisions are **prototype assumptions pending CGS validation**. They are not claimed to be approved CGS policy. Read **Scoring guide** for every anchor and reason code.

## Agent and component boundaries

| Component | Type | Responsibility |
| --- | --- | --- |
| A1 Preparation & outreach | Context-grounded drafting | Confirmed case → editable approach, subject, email and questions |
| A2 Evidence & engagement | Structured extraction | Untrusted original note → exact-quote factual findings |
| A3 Qualification advisory | Rubric-based judgement | Validated findings + current case + anchors → suggested changes, gaps and conflicts |
| A4 Action & reporting | Bounded recommendation | Confirmed assessment + gaps + tasks → up to three suggested actions |
| Workflow coordinator | Ordinary backend code | Calls roles, stores sources/runs, validates boundaries and handles errors |
| Qualification rules | Deterministic code | Q/R/P intervals, floors, coverage, readiness and gaps |
| Approval-pack builder | Deterministic frontend template over server snapshot | Compiles the frozen confirmed evidence; does not invent a narrative |
| Monitor | Background Python thread | Creates/removes reminder records without modifying scores |

These are four logical roles sharing one configurable model adapter, not four independently deployed autonomous services. No external retrieval index or approved CGS capability/fee knowledge base is connected yet; A1 uses the supplied case context. Add approved knowledge retrieval before relying on automated service-fit research.

## Source layout

```text
server.py              HTTP routes, SQLite store, coordinator, local monitor
backend/agents.py      Four role prompts, JSON schemas, live adapter and demo parser
backend/rules.py       Authoritative rule calculations and score validation
data/rules.json        Weights, labels, full anchors, conditions, reason codes
data/seed.json         Seven sample cases
dist/                  English HTML/CSS/JS frontend
.env.example           Server configuration template (no credentials)
tests/test_backend.py  Persistence, workflows, provider contracts, HTTP boundaries
test/rules.test.cjs    Original browser-rule regression cases
docs/API.md            Backend endpoint contracts
DEMO_GUIDE_ZH.md        Chinese team walkthrough
runtime/               Generated database, excluded from source archive
```

## Tests

From this folder:

```sh
python3 -m unittest discover -s tests -v
node --test test/rules.test.cjs
node --check dist/app.js
```

Current automated validation: 38 Python tests and 13 original rule tests. Browser checks also covered source entry, individual acceptance, score changes, reload/restart persistence, the approval queue and a local approval decision.

## Deployment boundaries

This is a **single-user local functional prototype**, bound to `127.0.0.1`, with local CSRF/origin checks. It has no authenticated multi-user roles, production security review, CRM write adapter, mail-sync adapter, cloud scheduler or production hosting. Do not expose this Python development server publicly. Background checks stop if the server stops; they resume on startup. Sample reset and the demo clock are demonstration controls.

External integrations and production authentication remain implementation work; adding a model key enables live model operations, not those other integrations. Approval records remain separate from CGS’s production Opportunity conversion.

Official API implementation reference: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
