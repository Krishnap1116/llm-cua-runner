# LLM Computer-Use Automation Runner

A small, real implementation of the "record once, replay forever" pattern for automating legacy web applications: an LLM drives a live browser to discover how to accomplish a goal, that run is distilled into a typed, versioned, reusable **capability artifact**, and the artifact is replayed deterministically afterward — no LLM in the loop — with a real error taxonomy, safety guardrails, and a human-escalation path.

See [`REPORT.md`](REPORT.md) for the full design write-up (architecture, schema, determinism, safety, escalation, cuts).

## What's included

- **A deliberately hostile-but-accessible mock bank app** (`cua_runner/mock_app/`) — a Flask "teller console" with table-based legacy markup, no test IDs, real session/CSRF/auth mechanics, and real business rules (validation, status gating, sub-account caps, a genuine unhandled edge case).
- **An LLM-driven discovery agent** (`cua_runner/agent/`) that perceives the live page via its accessibility tree, decides via Claude tool-calling, and records a capability artifact — with a human-in-the-loop parameterization flow, credential exclusion, and PII/secret redaction.
- **A deterministic replay engine** (`cua_runner/replay/`) that runs a saved artifact with no LLM, using a locator fallback chain, a declarative outcome taxonomy (business outcome / recoverable / hard failure), guardrail enforcement, an approval gate for irreversible capabilities, and an optional bounded LLM-assisted fallback for a single failed step.
- **Human escalation & handoff** (`cua_runner/escalation/`) — pauses automation, hands a human the same live browser session, records what they did, and resumes.
- **63 automated tests** (`tests/`) covering all of the above against a real, in-process Flask app and real Playwright browser (no mocking of the app itself).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
cp .env.example .env
```

Edit `.env`:
```
ANTHROPIC_API_KEY=your-real-key       # required only for `discover` (a real LLM call)
ANTHROPIC_MODEL=claude-sonnet-5       # or another tool-use-capable Claude model
FLASK_SECRET_KEY=                     # optional; a random one is generated if blank
SERVICE_ACCOUNT_USERNAME=teller1              # the automation's own login (never the caller's)
SERVICE_ACCOUNT_PASSWORD=correcthorsebattery  # matches the mock app's built-in demo account
```

`replay`, `stability`, and the full test suite require **no API key and no external services** — they only need the local mock app, which the tests start automatically in-process. Only `discover` makes a real, billed LLM call.

## Demo path

**1. Start the mock app** (needed for `discover` and manual `replay`/`stability` runs — the test suite starts its own copy automatically):
```bash
python3 -m cua_runner.mock_app.app
```

**2. Run the agent on a goal** (a real LLM call — this is what produced the checked-in `artifacts/open_savings_subaccount.json` and the evidence in `evidence/runs/`):
```bash
python3 -m cua_runner.cli discover \
  --goal "Open a new sub-account for member 10234 with an initial deposit of 500 and nickname 'Vacation Fund'. Confirm the action, then read and report the resulting account number." \
  --base-url "http://127.0.0.1:5000/" \
  --capability-id "open_savings_subaccount" \
  --name "Open Savings Sub-Account"
```
The agent authenticates automatically using the `SERVICE_ACCOUNT_*` credentials in `.env` before it ever sees a page — the goal never needs to mention logging in. It will pause interactively to ask whether each new field should become a reusable parameter (only the first time it's ever seen — subsequent runs reuse the cached decision), and again at the end to review and confirm the risk level before saving.

**3. Replay the resulting artifact** — deterministically, with a *different* member than discovery used, proving genuine parameterization:
```bash
python3 -m cua_runner.cli replay \
  --artifact artifacts/open_savings_subaccount.json \
  --params '{"member_id":"11045","initial_deposit":250,"nickname":"Emergency Reserve"}'
```

**4. See the error-handling paths** (no API key needed):
```bash
# a business outcome, not a crash
python3 -m cua_runner.cli replay --artifact artifacts/open_savings_subaccount.json \
  --params '{"member_id":"99999","initial_deposit":250,"nickname":"X"}'

# a genuine hard failure (a real, undiscovered $1,000,000 edge-case bug in the mock app)
python3 -m cua_runner.cli replay --artifact artifacts/open_savings_subaccount.json \
  --params '{"member_id":"11045","initial_deposit":1000000,"nickname":"Big"}'
```

**5. A second, simpler capability** — `lookup_member_balance`, a `SAFE`/read-only lookup (the brief's own first example goal: "look up member 12345 and read their current savings balance"), proving the design generalizes beyond the harder, irreversible capability above:
```bash
python3 -m cua_runner.cli replay --artifact artifacts/lookup_member_balance.json --params '{"member_id":"11045"}'
```

**6. Multi-run stability check** (zero API calls — replay is deterministic):
```bash
python3 -m cua_runner.cli stability \
  --artifact artifacts/open_savings_subaccount.json \
  --param-pool '[{"member_id":"10391","initial_deposit":50,"nickname":"Stability"},{"member_id":"11045","initial_deposit":50,"nickname":"Stability"}]' \
  --n 6
```

**7. See a human handoff** (add `--headed` to actually watch it, or omit it — the mechanics are identical headless):
```bash
python3 -m cua_runner.cli replay --artifact artifacts/open_savings_subaccount.json \
  --params '{"member_id":"11045","initial_deposit":250,"nickname":"X"}' \
  --allow-escalation --headed
```

**8. Verify the prompt-injection defense live** (real API call; plants a manipulative field in the mock app's data *in memory only*, never touching the committed source, and confirms both the model's own reasoning resists it and the risky-click confirmation gate independently still fires):
```bash
python3 scripts/verify_injection_defense.py
```

**9. Run the automated test suite** (starts its own mock app instance, no manual server needed, no API key needed — the fastest way to verify everything above is genuinely working):
```bash
pytest
```

## Evidence

- `evidence/runs/` — a real discovery run: full raw transcript (secrets/PII redacted), per-step screenshots, and result summary.
- `evidence/replays/` — a saved result + final-state screenshot for every replay invocation.
- `evidence/escalations/` — intervention requests and human-operator notes from escalation events.
- `artifacts/` — the saved, versioned capability artifact(s), plus `field_decisions.json`: the persisted cache of which fields have already been classified as reusable parameters vs. fixed values (see REPORT.md, Section 2 "Parameterization") — committed so the recording history is reproducible, not because it needs to be read directly.

## Repository layout

```
cua_runner/
  schema.py           # the typed, versioned Capability artifact contract
  mock_app/           # the target application (Flask)
  agent/              # discovery: perception, tools, the LLM loop, recorder, field memory
  replay/             # deterministic execution: engine, locator resolver, conditions, auth, stability
  guardrails/         # the allowlist + risky-action policy
  escalation/         # human handoff
  cli.py              # discover / replay / stability entry points
tests/                # 63 automated tests
artifacts/            # saved capability artifacts
evidence/             # discovery, replay, and escalation evidence
```
