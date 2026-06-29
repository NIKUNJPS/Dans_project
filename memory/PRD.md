# StructMind AI · Product Requirements (v4.2.0)

## Original problem statement (verbatim)
> Enterprise-grade AI-powered structural-steel intelligence platform for 4XStruct Inc.
> v4.0.0 — 3 roles (super_admin, detailer, fabricator) + granular feature_permissions + Super Admin panel.
> v4.1.0 — Drawing-driven AI cost estimation (rate-band UI tool).
> v4.2.0 — 16 SteelSight analysis modes wired in for Detailer AND Fabricator.
>          All 15 non-estimation modes shared verbatim between roles.
>          Estimation mode differs: Detailer = hours-based ($18/hr fixed), Fabricator = tonnage × per-ton band.

## Architecture (unchanged)
- Frontend: React + Tailwind + shadcn/ui + Framer Motion
- Backend: FastAPI + Motor (Mongo) + ReportLab + Emergent LLM Key (Gemini 2.5 Pro)
- Roles: `super_admin`, `detailer`, `fabricator`

## v4.2.0 — what landed
**16 SteelSight AI analysis modes** now power the Analyze Wizard pipeline. Same 16 IDs for both roles, with one role-specific estimation engine.

| Mode ID | Group | Time | Detailer | Fabricator |
|---|---|---|---|---|
| MASTER_INTAKE | Intake & Index | 12–18 min | ✓ | ✓ (same) |
| PHASE_1 | Intake & Index | 6–10 min | ✓ | ✓ (same) |
| PHASE_2 | Engineering Review | 10–15 min | ✓ | ✓ (same) |
| PHASE_3 | Engineering Review | 8–12 min | ✓ | ✓ (same) |
| **ESTIMATION_PRO** | Estimation & Bid | 10–15 min | ✓ **hours-based, $18/hr [CONFIDENTIAL]** | ✗ |
| **FABRICATOR_ESTIMATION_PRO** | Estimation & Bid | 10–15 min | ✗ | ✓ **tonnage × user per-ton band** |
| BID_STRATEGY | Estimation & Bid | 4–6 min | ✓ | ✓ (same) |
| LANDSCAPE_SPECIALIST | Estimation & Bid | 5–8 min | ✓ | ✓ (same) |
| MTO | Take-off | 15–25 min | ✓ | ✓ (same) |
| ISSUE_DETECTOR | Quality & Checking | 5–8 min | ✓ | ✓ (same) |
| DRAWING_CHECKER | Quality & Checking | 12–20 min | ✓ | ✓ (same) |
| CNC_FILE_CHECKER | Quality & Checking | 4–8 min | ✓ | ✓ (same) |
| DRAWING_SUBMISSION_SCHEDULE | Schedule & Planning | 2–4 min | ✓ | ✓ (same) |
| INTERNAL_SCHEDULE_PLANNER | Schedule & Planning | 6–10 min | ✓ | ✓ (same) |
| POST_AWARD_RISK_TRACKER | Post-Award | 4–6 min | ✓ | ✓ (same) |
| SUMMARIZER | Quick Tools | 2–4 min | ✓ | ✓ (same) |
| CHAT_ASSISTANT | Quick Tools | real-time | ✓ | ✓ (same) |

**Catalog totals**: 32 entries (16 detailer + 16 fabricator) across 8 groups.

DRY: 15 shared modes live as module-level constants in `detailer_prompts.py` and are imported into `fabricator_prompts.py` — edit once, both roles get the update.

## What is implemented (cumulative)
1. 3-Role schema + legacy migration on startup.
2. Granular `feature_permissions` per user (Plan A locked-down defaults).
3. Permission Guard middleware (mode / usage / export / file-size / country gates + audit log).
4. Super Admin REST API (user CRUD, password reset, permission editor, audit log, analytics, modes catalog, 15-min read-only impersonation).
5. Analyses pipeline → `prompts/prompt_router` + permission_guard.
6. **16 SteelSight modes per role** (one estimation differs).
7. Drawing-driven cost estimation tool (`/api/estimation/ai-calculate`) — separate from prompt modes; quick rate-band cost range.
8. Deterministic estimation endpoint (`/api/estimation/calculate`) kept for API/backwards-compat.
9. Dual-mode PDF generator (deterministic + AI-driven).
10. Frontend: full Super Admin Console, role-adaptive AnalyzeWizard, drawing-driven Estimation page, signup limited to detailer/fabricator.

## Testing summary
- iteration_3 → 32/32 backend (v4.0.0 3-role architecture).
- iteration_4 → 20/20 backend + frontend smoke (v4.1.0 AI cost estimation).
- iteration_5 → 11/11 backend (v4.2.0 mode catalog + per-role isolation).
- **Cumulative: 63/63 backend tests passing.**

## Plan A defaults (still in effect for new users)
```
analysesPerMonth      = 10
maxFileSizeMb         = 25
maxFilesPerAnalysis   = 3
allowedModes          = []
allowedExports        = ["markdown", "pdf"]
canRunEstimation      = true if fabricator else false
estimationCountries   = ["USA"]
canSendRfis           = false
canViewAuditLog       = false
blockchainAnchoring   = false
```
Super admin holds a wildcard bundle (`allowedModes: ["*"]`, etc.) resolved on the fly.

## P1 / P2 backlog
- **P1** Apply-Preset quick action on Permissions editor (Trial / Standard / Pro toggle bundles).
- **P1** Redis cache for `feature_permissions`.
- **P1** Polygon blockchain anchoring (SHA-256 ledger in Mongo today).
- **P2** Warning chip on Permissions editor when assigning a cross-role mode ID (e.g. `FABRICATOR_*` to a detailer).
- **P2** Resend branded emails; RFI SLA timers; per-tenant white-labelling.
- **P2** .dwg/.dxf vector → raster pre-conversion for richer AI extraction.
- **P2** Split each prompt into its own file if any individual mode grows past ~5KB.

## Acceptance criteria for v4.2.0 (all met)
- [x] 16 SteelSight modes for Detailer (15 shared + ESTIMATION_PRO hours-based).
- [x] 16 SteelSight modes for Fabricator (15 shared + FABRICATOR_ESTIMATION_PRO tonnage-based).
- [x] Shared 15 modes have byte-identical prompts between roles.
- [x] Estimation mode IDs are distinct so they can be enabled/disabled independently per role.
- [x] Locked-down user sees zero modes (proper empty state).
- [x] Super Admin permissions editor lists all modes for the user's role.
- [x] Cross-role mode assignment fails safely at the analyse-runner layer.
