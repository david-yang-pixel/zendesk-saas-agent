# Admin Console Design QA

## Scope

- Target: `方案 1 · 工单指挥台`
- Reference: `/Users/yangyonggang/.codex/generated_images/01a00ec4-ffc3-75e1-9e75-f8358431e6ac/exec-991eaecd-b3c1-40d6-bf14-5688c7bb8b2e.png`
- Implementation: `http://127.0.0.1:8767/admin`
- Desktop viewport: `1487 × 1058`
- Mobile validation viewport: `390 × 844 CSS px`
- Comparison: `qa/admin-command-center-comparison.png`

## Visual comparison

| Area | Result | Notes |
| --- | --- | --- |
| Information architecture | Passed | Seven-item navigation, command center, ticket flow, activity and operations rail match the selected target. |
| Layout and density | Passed | Narrow left rail, wide operational canvas, three live slots and narrow right overview are aligned to the reference proportions. |
| Typography | Passed | Compact system typography, restrained weights and high-density labels remain legible at the target viewport. |
| Color and surfaces | Passed | Warm white canvas, near-black text, blue active state and low-contrast borders match the selected direction. |
| Responsive behavior | Passed | Navigation collapses, live slots stack and ticket data keeps the essential fields visible on a 390 px viewport. |

## Functional validation

- Primary navigation: passed.
- Command-center refresh and partial-source failure state: passed.
- Live concurrency, queue, outbox and background-task data: passed.
- Ticket center and run-log navigation: passed.
- Model provider/Base URL/API key form: passed.
- Server-side model discovery, sorting and deduplication: passed.
- Friendly authentication/network/response errors: passed.
- Model selection back into the editable model field: passed.
- Strategy and system hubs: passed.
- Browser console: no JavaScript exceptions; one non-blocking Chromium password-form accessibility recommendation only.

## Resolved findings

- P1: initial sidebar and operations rail were wider than the reference. Resolved by matching the target proportions.
- P1: the first pass lacked the reference panel borders. Resolved with low-contrast operational surfaces.
- P1: mobile descendant selector hid status badges. Resolved with direct-child responsive selectors.
- P2: raw run metadata displaced useful summaries. Resolved by preferring explicit event summaries.
- P2: provider errors exposed internal error codes. Resolved with customer-readable Chinese error states.
- P2: discovered model IDs were interpolated into inline JavaScript. Resolved with escaped data attributes.
- P2: switching away from a long configuration page retained its scroll position. Resolved by resetting navigation transitions to the top of the destination page.
- P2: overview metrics and hub links read as a wall of separate cards. Resolved by consolidating them into calm, continuous information surfaces with responsive separators.

## Verification

- `node --check src/zd_agent/admin_static/app.js`
- `.venv/bin/ruff check src/zd_agent/services/admin_routes.py tests/entry/test_admin_routes.py`
- `.venv/bin/pytest -q tests/entry/test_admin_routes.py` → 37 passed
- `.venv/bin/pytest -q` → 1627 passed
- `git diff --check`

final result: passed
