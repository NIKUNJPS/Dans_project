# StructMind AI · Runbook (v5.0.0)

> Everything you need to run, configure, and deploy the platform end-to-end.

---

## PART 1 · What YOU still need to provide

These are the only manual inputs the platform expects from you:

### ① Paste your AI prompts (✅ **DONE** in v4.2.0 — 16 SteelSight modes already wired)
The Detailer and Fabricator roles ship with the full 16-mode SteelSight catalog already integrated into `/app/backend/prompts/`:

| File | Status |
|------|--------|
| `prompts/shared_rules.py` | ✅ 10 baseline output rules — edit if you change the global formatting policy |
| `prompts/detailer_prompts.py` | ✅ All 16 SteelSight modes for Detailer (15 shared + `ESTIMATION_PRO` hours-based) |
| `prompts/fabricator_prompts.py` | ✅ Imports the 15 shared modes + own `FABRICATOR_ESTIMATION_PRO` tonnage-based |
| `prompts/prompt_router.py` | ✅ Wired — no edits needed |

The 15 shared modes are stored as module-level constants in `detailer_prompts.py` and imported into `fabricator_prompts.py`. **Editing a prompt updates both roles automatically.** Only the estimation mode differs (intentional).

**To tweak any prompt:** open the corresponding constant in `detailer_prompts.py` (e.g. `MASTER_INTAKE = """..."""`), edit, restart backend (`sudo supervisorctl restart backend`). Super Admin → Permissions editor will immediately reflect the change.

> **v5:** the MTO Engine and both Estimation Pro modes run on deterministic engines. Their prompts live
> next to the code that parses them — `estimation/takeoff.py` (`takeoff_system_prompt`, used by MTO and
> Fabricator Estimation Pro) and `estimation/detailing_estimate.py` (`detailing_system_prompt`, Detailer
> Estimation Pro). The registry entries in `detailer_prompts.py` / `fabricator_prompts.py` point at them.

**To add a brand-new mode:** add a new `NEW_MODE_NAME = """..."""` constant, then add an entry to the `DETAILER_MODES` dict (and the `FABRICATOR_MODES` dict if it should be shared). Restart backend.

---

### ② Production secrets (only needed for live deploy, not preview)
The preview environment already has everything wired. For production, set these in your deployment's environment:

| Variable | Purpose | Where to get it |
|----------|---------|-----------------|
| `MONGO_URL` | Mongo connection string | Mongo Atlas → cluster → Connect → Application |
| `DB_NAME` | Mongo database name | Your choice (e.g. `structmind_prod`) |
| `ANTHROPIC_API_KEY` | **Required.** Claude API key that runs every analysis | console.anthropic.com → API Keys |
| `ANTHROPIC_MODEL` / `ANTHROPIC_FALLBACK_MODEL` | Optional. Default `claude-sonnet-5-5` for every analysis, no fallback. Leave both unset (or set `ANTHROPIC_MODEL=claude-sonnet-5-5`); an old `ANTHROPIC_MODEL=claude-opus-5-5` overrides the default. The server logs `claude_models chain=[...]` at startup | — |
| `ANTHROPIC_EFFORT` | Optional. Reasoning depth, default `high` (`xhigh`/`max` = slower, deeper) | — |
| `ANTHROPIC_SERVER_FALLBACK` | Optional, default `true`. Set `false` on Bedrock / Vertex / Foundry | — |
| `BINDER_BATCH_PAGES` / `BINDER_BATCH_MB` | Optional. Page batch size, default 25 pages / 28 MB per Claude call | — |
| `BATCH_CONCURRENCY` / `MAX_PARALLEL_CLAUDE_CALLS` | Optional. Batches read in parallel per analysis (4) and process-wide (8). Lower them if you hit API rate limits | — |
| `JWT_SECRET` | JWT signing key | Generate: `python -c "import secrets;print(secrets.token_urlsafe(64))"` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | First seeded super admin | Your choice |
| `ADMIN_EMAIL_2` / `ADMIN_PASSWORD_2` | Second seeded super admin (optional) | Your choice |
| `RESEND_API_KEY` | **Primary** OTP / email delivery (recommended) | [resend.com](https://resend.com) → API Keys |
| `SMTP_FROM` / `SMTP_FROM_NAME` | Verified sender address + display name | e.g. `noreply@4xstruct.com` / `STRUCTMIND` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | OTP email fallback (used only if `RESEND_API_KEY` is unset) | Gmail App Password, SendGrid, etc. |
| `REACT_APP_BACKEND_URL` (frontend) | Public backend URL | Your deployment URL (e.g. `https://api.4xstruct.com`) |
| `CORS_ORIGINS` (backend) | Comma-separated allowed origins | `https://app.4xstruct.com,https://www.4xstruct.com` |

> 🔐 **NEVER** commit any of these to git. Use your hosting platform's secret manager.

---

### ③ Per-user permission setup (one-time, per user)
After a Detailer/Fabricator signs up (or you create them via Admin → Users → Create User), they land in **lockdown mode** by Plan A defaults:

| Default | Value |
|---------|-------|
| Analyses/month | 10 |
| Max file size | 25 MB |
| Max files per analysis | 3 |
| Allowed modes | **none** (you must enable each one) |
| Allowed exports | markdown, pdf |
| Estimation enabled | Yes for Fabricator, **No for Detailer** |
| Estimation countries | USA only |

To onboard a new user, go to **Admin → Permissions → click "Edit permissions" on their row** and toggle:
- ✅ The specific AI modes they should access
- ✅ Additional export formats (Word, Excel, CSV)
- ✅ Additional countries
- ✅ Feature flags (canSendRfis, canRunEstimation, blockchainAnchoring, etc.)
- ✅ Bump their monthly cap if needed (-1 = unlimited)

Click **Save permissions**. They see the new modes/features on next page reload.

---

## PART 2 · Running locally (development)

```bash
# 1. Clone (or pull) the repo
git clone <your-repo-url> structmind && cd structmind

# 2. Backend — Python 3.11+
cd backend
pip install -r requirements.txt
cp .env.example .env       # fill in MONGO_URL, JWT_SECRET, ANTHROPIC_API_KEY, ADMIN_EMAIL/PASSWORD, SMTP_*
# Start the server (uvicorn is in requirements)
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# 3. Frontend — Node 18+, Yarn
cd ../frontend
yarn install
cp .env.example .env       # set REACT_APP_BACKEND_URL=http://localhost:8001
yarn start                 # opens http://localhost:3000

# 4. Mongo — local dev
docker run -d --name structmind-mongo -p 27017:27017 mongo:7
# Then MONGO_URL=mongodb://localhost:27017
```

After boot, log in with one of the seeded super admins (`ADMIN_EMAIL` / `ADMIN_PASSWORD`).

---

## PART 3 · Deploying to production

The platform is preconfigured to deploy with **one click on Emergent**:

### Option A — Deploy on Emergent (recommended, zero config)
1. Click **"Deploy"** (top-right of the Emergent chat).
2. Pick a subdomain or attach your custom domain (Cloudflare DNS instructions are shown).
3. Set the 4 required secrets in the deploy dialog: `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SMTP_*`.
4. Emergent provisions a Mongo Atlas cluster and sets `MONGO_URL` automatically — add your `ANTHROPIC_API_KEY` as a secret — then ships your build.
5. First request seeds your super admin. Done.

### Option B — Self-host (Vercel + Render + Atlas)
| Layer | Provider | Build command | Start command |
|-------|----------|---------------|---------------|
| Frontend | Vercel | `cd frontend && yarn install && yarn build` | (static) |
| Backend | Render / Railway / Fly | `pip install -r requirements.txt` | `uvicorn server:app --host 0.0.0.0 --port $PORT` |
| Database | Mongo Atlas | n/a | n/a |
| File storage | Persistent disk on backend host (`/data/uploads`) | mount volume | `UPLOAD_DIR=/data/uploads` |

Then point the frontend's `REACT_APP_BACKEND_URL` env to the backend public URL.

> ⚠️ **For self-host**: make sure the backend host has a **persistent disk** mounted at the path you set in `UPLOAD_DIR` — uploaded drawings need to survive deploys (file IDs are referenced by AI extraction).

### Option C — Save to GitHub (CI/CD)
Click **"Save to Github"** in the Emergent chat input. You get a private repo with the full source, and you can wire any CI/CD pipeline you want (GitHub Actions, etc.).

---

## PART 4 · How the AI estimation flow works (end-to-end)

```
UI /estimate ── POST /api/estimation/ai-calculate {role, rate_low, rate_high, file_ids, country}
                 → record saved with status "processing", background job starts, UI polls GET /api/estimation/{id}
background job:  file_prep (Excel→CSV, Word→text) → binder.plan_binder (page batches)
                 → pipeline.run_takeoff (Claude reads each batch in parallel; Python computes every weight)
                   — or reuses the cached take-off the MTO Engine produced for the same files
                 → engine.apply_band_to_extracted (tons × your LOW/HIGH band, per short ton in the USA)
                 → status "complete" (or "failed" with the reason)
UI ── GET /api/estimation/{id}/pdf → full PDF
```

**Latency**: a single 25-page batch takes a few minutes; a 300-page binder runs ~12 batches, 4 at a time.
Progress is shown live ("Taking off members — batch 5 of 12").

---

## PART 5 · Test credentials

**Seeded super admins (auto-created on first boot):**
- `niks.shah236@gmail.com` / `Nikunj@1405`
- `lalit@4xstruct.com` / `Lalit@4xstruct`

Change these via env vars (`ADMIN_EMAIL`, `ADMIN_PASSWORD`, etc.) before your real production deploy.

---

## PART 6 · Common operations

| Operation | How |
|-----------|-----|
| Add a new mode | Edit `prompts/{detailer,fabricator}_prompts.py`, restart backend, enable it per-user via Admin → Permissions |
| Create a user manually | Admin → Users → Create user |
| Reset a user's password | Admin → Users → Reset (icon) next to user row |
| Impersonate a user (read-only, 15 min) | Admin → Users → "View as" — banner appears, all writes blocked, click "Exit impersonation" to return |
| Disable a user | Admin → Users → click Active pill to toggle |
| Inspect what users have done | Admin → Audit Log (filter by user_id or action prefix like `admin.user.`) |
| Run a quick manual estimate (no AI) | `POST /api/estimation/calculate` with full deterministic inputs (Postman/curl) |
| Run an AI estimate | Use the UI at `/estimate` — upload drawings + LOW + HIGH + Calculate |

---

## PART 7 · Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Login works but "/admin/users 403" | User role is detailer/fabricator, not super_admin | Re-seed via env vars or `db.users.updateOne({email}, {$set:{role:"super_admin"}})` |
| Analysis / estimate fails with "All STRUCTMIND CORE tiers failed" | Claude API errors after retries (invalid key, rate limit, outage) | Check `ANTHROPIC_API_KEY`, your Anthropic console usage limits, and lower `BATCH_CONCURRENCY` if the log shows 429 rate limits |
| "No analysis modes enabled" empty state | Super admin hasn't enabled any modes for that user | Admin → Permissions → toggle modes in AI modes section |
| OTP not arriving | Email provider not configured | Set `RESEND_API_KEY` (+ verified `SMTP_FROM`) in the backend env. With no provider the code logs the OTP to the backend console as a dev fallback; in production a failed send now returns a 502 so the user is told to retry rather than getting a silent "code sent". |
| File upload returns 413 | User's `maxFileSizeMb` cap exceeded | Admin → Permissions → increase Max file size |
| Backend won't start | Bad Mongo URL or missing requirement | `tail -n 100 /var/log/supervisor/backend.err.log` and fix |

---

## PART 8 · File map (where everything lives)

```
/app
├── backend/
│   ├── server.py                       (FastAPI entry · mounts routers · seeds admin)
│   ├── models.py                       (Pydantic models · Role enum locked)
│   ├── permissions.py                  (default + super_admin permissions)
│   ├── security.py                     (JWT, OTP, password hashing, impersonation)
│   ├── seed.py                         (super_admin seeding + legacy role migration)
│   ├── db.py                           (Mongo client + indexes)
│   ├── config.py                       (env loader)
│   ├── claude_engine.py                (Claude API: Files API, streaming, continuation, model + refusal fallback)
│   ├── binder.py                       (splits a multi-PDF binder into page batches + manifest)
│   ├── pipeline.py                     (one binder in → ONE report out: take-off, estimates, map→consolidate)
│   ├── pdf_repair.py                   (rewrite / rasterize tiers for PDFs Claude rejects)
│   ├── middleware/permission_guard.py  (every feature gate lives here)
│   ├── prompts/                        ◀── ★ YOU PASTE YOUR PROMPTS HERE
│   │   ├── shared_rules.py
│   │   ├── detailer_prompts.py
│   │   ├── fabricator_prompts.py
│   │   └── prompt_router.py
│   ├── routes/
│   │   ├── auth.py                     (signup, login, OTP, refresh, password reset)
│   │   ├── admin.py                    (super_admin console API)
│   │   ├── projects.py
│   │   ├── files.py
│   │   ├── analyses.py                 (uses prompt_router + permission_guard)
│   │   ├── rfis.py
│   │   ├── estimation.py               (AI + legacy paths)
│   │   └── platform.py                 (notifications, usage, dashboard stats)
│   └── estimation/
│       ├── weights.py                  (unit weights from designation / section geometry, length parser, joist filter)
│       ├── takeoff.py                  (take-off prompt, parsing, consolidation, MTO report)
│       ├── fab_estimate.py             (Fabricator Estimation Pro — tonnage × rate band × factors × tax)
│       ├── detailing_estimate.py       (Detailer Estimation Pro — piece-count hours, buffer, confidential cost)
│       ├── tonnage.py                  (take-off lock shared by MTO / estimation for the same drawing set)
│       ├── ai_extract.py               (estimate-page adapter over the pipeline)
│       ├── engine.py                   (deterministic + apply_band_to_extracted)
│       ├── rates.py                    (country/currency table)
│       ├── sanity.py                   (estimate sanity check)
│       └── pdf.py                      (dual-mode PDF renderer)
│
├── frontend/src/
│   ├── App.js                          (router · public + protected + admin routes)
│   ├── pages/
│   │   ├── Estimation.jsx              ◀── ★ NEW slimmed AI-driven UI (uploads + LOW + HIGH)
│   │   ├── AnalyzeWizard.jsx           (hides locked modes)
│   │   ├── Dashboard.jsx
│   │   ├── Outputs.jsx · Projects.jsx · …
│   │   └── admin/
│   │       ├── AdminUsers.jsx
│   │       ├── AdminPermissions.jsx
│   │       ├── AdminAuditLog.jsx
│   │       ├── AdminAnalytics.jsx
│   │       └── AdminLayout.jsx
│   ├── hooks/usePermissions.js
│   ├── context/AuthContext.jsx         (login, logout, startImpersonation)
│   └── components/
│       ├── auth/ProtectedRoute.jsx     (AdminRoute checks role==='super_admin')
│       └── layout/AppShell.jsx         (sidebar + topbar + impersonation banner)
│
└── memory/
    ├── PRD.md                          (product status — read first)
    ├── test_credentials.md             (seeded super admin creds)
    └── (this file)
```

---

## PART 9 · v4.3 — Accurate itemized take-off + auto-cleanup (what changed)

### ① The estimation engine is now a real, deterministic material take-off
The old estimator asked the model for a single bulk tonnage and multiplied by a rate —
that is why the number felt like a guess. It now works the way a human estimator does:

- **Member-by-member take-off.** STRUCTMIND CORE returns one line per piece
  (type, mark, profile, qty, length, grade, source sheet). Long take-offs are streamed
  and stitched — no truncation.
- **Weights are re-computed in Python**, not trusted from the model. Every linear member
  is `Qty × Length(m) × published unit weight (kg/m)` from the AISC / CISC / AS-NZS tables
  in `backend/estimation/weights.py`. If the model's arithmetic disagrees by >3 % the row
  is corrected and flagged `WEIGHT-RECONCILED`. **The same drawings always yield the same
  tonnage** — it is a calculation, not an opinion.
- **Full-fledged report.** Each estimate now carries: itemized line items, a category
  roll-up, a cost build-up (material / shop labour / coatings / consumables / equipment /
  overhead / margin), a **confidence score**, **assumptions**, and **open RFIs / gaps** —
  all rendered in the UI and the PDF.

Files: `estimation/weights.py` (new), `estimation/mto.py` (new engine),
`estimation/ai_extract.py` (now a thin wrapper), `estimation/engine.py` (cost build-up +
confidence), `estimation/pdf.py` (itemized + category + build-up + RFI sections).

### ② New Estimate page (the engine is finally wired to the UI)
`frontend/src/pages/Estimation.jsx` → route **`/estimate`**, in the sidebar as **Estimate**.
Upload drawings, set the LOW/HIGH rate band, and get the full report with a PDF download.
Gated by the existing `canRunEstimation` permission and `estimationCountries`.

> Bring your own key: set `ANTHROPIC_API_KEY` in the backend env (see PART 1 ②). The engine
> uses Claude (Sonnet 5.5 primary → Opus 5.5 fallback) and reads that key automatically.

### ③ Unused projects are cleaned up
- **Hard delete:** `DELETE /api/projects/{id}?hard=true` permanently removes a project and
  everything under it (files on disk + DB, analyses, RFIs, estimates, tonnage locks).
  Without `?hard=true` it still just archives, as before.
- **Auto-sweep:** a background loop archives (or deletes) projects that were created but
  never used — no files, analyses, RFIs or estimates — once they pass a grace period, and
  removes orphaned uploaded files. Super admins can also run it on demand:
  `POST /api/projects/maintenance/sweep?dry_run=true` (preview) then without `dry_run`.

Control it with these env vars (all optional; safe defaults shown):

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUTO_SWEEP_ENABLED` | `true` | Turn the background sweep on/off |
| `AUTO_SWEEP_GRACE_HOURS` | `72` | Only touch records older than this |
| `AUTO_SWEEP_INTERVAL_HOURS` | `12` | How often the sweep runs |
| `AUTO_SWEEP_HARD` | `false` | `true` = permanently delete; `false` = archive (safe) |

Files: `backend/cleanup.py` (new), `routes/projects.py`, `server.py`, `config.py`.

### ④ Tests
`backend/tests/test_estimation_engine.py` — offline unit tests (no server/Mongo needed)
covering weight lookup, the take-off parser, weight reconciliation, the cost build-up,
confidence banding and the cleanup date logic. Run: `pytest tests/test_estimation_engine.py`.


---

## PART 10 · v5.0 — Binder-scale accuracy, one report, history & print

### ① Tonnage is now an exact calculation (MTO Engine, Estimation, Estimate page)
- Claude reads the drawings and reports every piece (profile, Qty, printed length, sheet, method).
  **Python computes every number**: length from the printed dimension, unit weight, weight =
  Qty × Length × kg/m, **member count = Σ Qty**, category / role roll-ups, allowances and tonnage.
- Unit weights come from the profile itself — rolled shapes from the designation (W12x26 = 26 lb/ft,
  W310x52 = 52 kg/m, 310UB46.2 = 46.2 kg/m) and HSS / pipe / angles / plates from section geometry on
  nominal wall at 7,850 kg/m³. Verified against AISC / CISC / OneSteel tables within ±1.5 %.
  (The previous hard-coded HSS, angle and pipe tables were 20-45 % low.)
- A piece shown on a schedule/shop drawing AND counted on a plan in another part of the binder is
  counted **once** (logged in the reconciliation register). Model arithmetic that disagrees with the
  computed weight by > 3 % is flagged for review; computed weights are always used.
- Allowances are shown as separate lines: bolts & weld metal 3 %, plus 5 % connection material when
  connections are not detailed on the drawings.
- **Joists removed**: open-web steel joists, joist girders and joist bridging (SJI K/LH/DLH/SLH/KCS,
  OWSJ) are never taken off, counted, priced or put in scope registers — one exclusion line only.
- USA pricing is per **short ton (2,000 lb)**; Canada / Australia per **tonne**. Reports show both.
- MTO, Fabricator Estimation Pro and the Estimate page share one cached take-off per drawing set, so
  they always report the same tonnage. Running (or re-running) the MTO Engine refreshes it.
- Both Estimation Pro modes compute their manifests deterministically — every section agrees to the
  dollar / hour by construction. Detailer rates stay `[CONFIDENTIAL]`.

### ② 50 MB+ binders, processed in page batches, ONE report
- Every PDF is split by PAGE (`BINDER_BATCH_PAGES`, default 25) and packed in binder order; batches are
  read in parallel. Each call is told the whole binder manifest and which pages it holds.
- MTO / estimates: batch rows are consolidated in Python. All other modes (Master Intake, Issue Detector,
  Drawing Checker, …): a consolidation call writes the single deliverable — large registers are merged
  losslessly in Python, RFI ids renumbered across the document. No "batch" artefacts reach the client.
- A binder that fits one batch is analysed in a single call. A PDF Claude rejects is isolated (bisected)
  and repaired (rewrite → rasterize) without failing the rest of the binder.
- Live progress ("Taking off members — batch 5 of 8") on the report page, project page and Reports list.
  The Estimate page now runs as a background job (no gateway time-outs on large binders).

### ③ Claude engine
- Default `claude-sonnet-5-5` for every call (adaptive thinking, effort `high`, 128K streamed output),
  no cross-model fallback unless `ANTHROPIC_FALLBACK_MODEL` is set, server-side refusal fallback, Files API v1 (`client.files`), prompt caching on
  the system prompt and drawings. SDK pinned: `anthropic==1.11.0` (requires `idna>=3.18`).

### ④ History & print
- Delete one report (report page / project page / Reports list), **Clear history** for a project
  (reports + estimates; drawings kept), **Clear quick-analysis history**, delete estimates, delete files,
  and permanently delete a project. Report exports on disk are removed with the report.
- **Print** on every report and on the Estimate page: no app chrome, every table fully expanded,
  A4 landscape. API: `POST /api/analyses/bulk-delete` with `{ids}` | `{project_id}` | `{scope:"quick"}`.

### ⑤ Prompts
- One missing-data convention (em-dash + RFI unless a mode defines its own token), no 7-column cap,
  tri-jurisdiction fabricator persona, executive snapshot at the top of both Master Intakes, verified
  unit-weight reference in Drawing Checker / Phase 3, joists removed from every scope list.

### ⑥ Tests (offline — no server, Mongo or API key)
```
cd backend
pytest tests/test_estimation_engine.py tests/test_binder_pipeline.py tests/test_routes_offline.py
```
`test_routes_offline.py` needs `mongomock-motor` (dev only) and drives the real FastAPI routes with Claude stubbed.
