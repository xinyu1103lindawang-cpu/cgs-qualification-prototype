# Local API contracts

All endpoints are served from the same local origin. `GET /api/bootstrap` returns workspace data, authoritative assessments, rules, configuration status and a per-server CSRF token. Send that token in `X-CSRF-Token` on every POST. API keys are never returned. Case changes require the current integer `revision`; stale changes return 409. Validation errors return 400, missing records 404 and provider-generation failures 502. JSON responses use `{ "ok": true, "result": ... }` or `{ "error": "..." }`.

Evidence ingestion stores the input before invoking the model. It returns HTTP 200 with a run whose `status` is `Failed` for a model/validation failure; this deliberately distinguishes a saved source from a failed analysis. Review the returned run status. No scores change during ingestion.

| Method and route | Request | Result |
| --- | --- | --- |
| GET `/api/health` | none | `{ok:true}` |
| GET `/api/bootstrap` | none | Cases, results, sources, runs, reminders, rules, mode, clock, config, csrf |
| POST `/api/cases` | company, project, contact, owner | New Contacted case; all six scores null |
| POST `/api/cases/{id}/analyze` | revision, requestId, text, source, kind, date | Saved source and A2/A3 analysis run |
| POST `/api/cases/{id}/analyze` | revision, requestId, sourceId | Re-analysis using original saved text; previous pending suggestions for that source are superseded |
| POST `/api/runs/{id}/review` | revision, proposalId, decision, reason; score or conditionValue for Edit | Updated case and reviewed proposal |
| POST `/api/cases/{id}/review` | revision, kind=score, dimension, score, reason | Reviewed score and version |
| POST `/api/cases/{id}/review` | revision, kind=revenue, revenue={low,high} or null, reason | CGS fee estimate and version |
| POST `/api/cases/{id}/review` | revision, kind=conditions, conditions={all six keys}, blocker or null, reason | Human-reviewed conditions and blocker |
| POST `/api/cases/{id}/review` | revision, kind=problem, problem, reason | Human-reviewed client problem summary; no automatic Need/fit confirmation |
| POST `/api/cases/{id}/review` | revision, kind=source, sourceId, confirmed=true, reason | Manual review acknowledgement; no score changes |
| POST `/api/cases/{id}/outreach` | revision | A1 saved draft, approach, subject, questions, provenance and call trace |
| POST `/api/cases/{id}/outreach` | revision, operation=save, subject, draft | Human-edited draft saved without a model call |
| POST `/api/cases/{id}/actions` | revision | A4 saved action suggestions (maximum three) |
| POST `/api/cases/{id}/tasks` | revision, title, assignee, side=CGS/Client, due, confirmed=true | New agreed task; no score changes |
| POST `/api/cases/{id}/tasks` | revision, operation=toggle, taskId | Complete/reopen task; no score changes |
| POST `/api/cases/{id}/tasks` | revision, operation=reschedule, taskId, due, reason | Updated due date |
| POST `/api/cases/{id}/approval` | revision, operation=submit | Frozen Ready qualification pack |
| POST `/api/cases/{id}/approval` | revision, operation=exception, reason | Separate exception request; readiness unchanged |
| POST `/api/cases/{id}/approval` | revision, operation=approve/return/defer, reason | Local human decision; no CRM write |
| POST `/api/reminders/snooze` | id | Seven-day snooze |
| POST `/api/config` | mode=demo/live; optional clock=demo/system, today=YYYY-MM-DD | Workspace settings; model keys must be configured server-side |
| POST `/api/demo/reset` | confirm=RESET | Explicit reset to seven samples, demo mode and demo date |

Evidence kinds: `Client reply`, `Meeting notes`, `Relationship record`, `Other evidence`. The user classifies an imported item as a client reply; the backend applies Active regardless of whether A2/A3 succeeds. Dates cannot exceed the selected workspace date.

Review decisions: `Accept`, `Edit`, `Reject`. Edits and rejection need a rationale; any potential-conflict acceptance also requires a rationale. Ratings accept only valid anchors; H=2.5 is the explicit first-time neutral rule. A declined or conflicting suggestion never silently becomes confirmed evidence. A separately confirmed conflict resolution is required after accepting a conflict.

A run records `id`, `caseId`, `sourceId`, `baseRevision`, `mode`, `status`, `findings`, `proposals`, `calls` and optional `error`. The schemas and prompts are executable definitions in `backend/agents.py`. A2 validates every quote against the stored original text, including its offsets. A3 may reference only validated finding IDs. The server does not accept an AI-calculated total.

Analysis is synchronous for this local MVP, with one concurrent run per case and two bounded live calls at most for A2/A3. Draft/action generation makes one live call. The frontend displays progress. The source remains stored when the browser connection or the model fails. Restart marks unfinished runs Failed. Re-analysis and retry are explicit operations, each with a new request ID; repeated ingestion using the same request ID returns the existing run without duplicating the source.

Ordinary source, task, rating and review changes increase the case revision. Generated or edited outreach drafts and next-action suggestions are stored as auxiliary records and do not change qualification evidence revision. Advice carries its base revision and must be regenerated after case changes before it can be converted into an agreed task.

Standard approval submission checks: Active + Ready, no pending suggestions, and no latest Failed/Running source without a manual review acknowledgement. A pending pack can be approved only against its unchanged submitted revision. An exception approval needs a human rationale and leaves the rule-based readiness unchanged.

These endpoints are prototype contracts, not Insightly, Microsoft Graph or OpenAI endpoints. The only implemented outbound API is OpenAI Responses. External CRM/mail adapters and multi-user authorization are not present.
