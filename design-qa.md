# Admin Console Design QA

## Scope

- Target: `方案 1 · 工单指挥台`
- Reference: `/Users/yangyonggang/.codex/generated_images/01a00ec4-ffc3-75e1-9e75-f8358431e6ac/exec-991eaecd-b3c1-40d6-bf14-5688c7bb8b2e.png`
- Implementation: `http://127.0.0.1:8767/admin`
- Desktop viewport: `1487 × 1058`
- Mobile validation viewport: `390 × 844 CSS px`
- Comparison: `qa/admin-command-center-comparison.png`

### Model capability picker refinement

- Reference: `/var/folders/n9/43hzt_kd1sv5smhsltb3mh480000gn/T/codex-clipboard-4dd22ade-53f5-44ea-ae97-4defb552ab2e.png`
- Desktop viewport: `1920 × 992 CSS px`
- Mobile viewport: `390 × 844 CSS px`
- Implementation: `qa/admin-model-capability-picker.png`
- Side-by-side comparison: `qa/admin-model-capability-comparison.png`

### Model connection and read-only capability refinement

- Reference: `/var/folders/n9/43hzt_kd1sv5smhsltb3mh480000gn/T/codex-clipboard-e072787b-5dd5-4f3f-9aae-54df3d0223a8.png`
- Desktop viewport: `1920 × 1080 CSS px`
- Mobile viewport: `390 × 844 CSS px`
- Implementation: `qa/admin-model-connection-capability.png`
- Side-by-side comparison: `qa/admin-model-connection-capability-comparison.png`

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
- Main-model dropdown fetches from the currently edited provider and Base URL: passed.
- Per-model reasoning strength shows only Agent-validated effort levels: passed.
- Per-model vision support uses supported / unsupported / unknown states: passed.
- Unsupported reasoning is disabled; vision is read-only and follows the selected model automatically: passed.
- The active provider's API key is configured beside provider, Base URL and model; the inactive provider key is hidden: passed.
- An unsaved API key can fetch the current Base URL's models without appearing in the response or audit log: passed.
- Supported vision writes `true`; unsupported and unknown vision safely write `false`: passed.
- Provider errors keep the selected model visible and expose a retry action: passed.
- Model search and refresh controls: passed.
- Model picker at `390 × 844`: passed.
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
- P1: model discovery lived in a separate form and did not control the actual model field. Resolved by fetching on main-model dropdown expansion.
- P1: vision was a global toggle even when the selected model was known not to accept images. Resolved with model-specific tri-state capability handling.
- P1: provider API keys were separated from the model connection fields. Resolved by moving the active provider key into the model connection group.
- P1: vision capability could still be manually overridden. Resolved with a read-only capability indicator and automatic persisted runtime value.
- P1: missing provider capability metadata could be mistaken for support. Resolved by preserving `unknown` as a first-class state.
- P2: large model registries were difficult to scan. Resolved with in-picker search, ownership labels, capability badges and refresh.

## Verification

- `node --check src/zd_agent/admin_static/app.js`
- `.venv/bin/ruff check src/zd_agent/services/admin_routes.py tests/entry/test_admin_routes.py`
- `.venv/bin/pytest -q tests/entry/test_admin_routes.py` → 37 passed
- `.venv/bin/pytest -q` → 1630 passed
- `git diff --check`

The model-capability refinement was additionally verified with:

- `.venv/bin/pytest -q tests/services/test_admin_config.py tests/entry/test_admin_routes.py` → 61 passed
- Browser interaction: open picker → fetch 6 models → select `gpt-5.6-terra` → six reasoning levels and vision enabled.
- Browser interaction: select `text-embedding-3-large` → reasoning disabled and vision forced off.
- Browser interaction: preview changes after selecting `text-embedding-3-large` → `llm.enable_vision_input` changes from `true` to `false` automatically.
- Browser interaction: switch provider to Anthropic → OpenAI key hides and Anthropic key appears in the same model connection group.
- Browser console: no errors or warnings.

final result: passed
