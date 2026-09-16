"""Shared pytest fixtures: an in-process live mock app (real Flask dev
server, real Playwright browser against it -- not mocked), with per-test
data reset so tests are order-independent despite the app's in-memory
state being mutated by irreversible actions."""
import copy
import os
import threading

import pytest
from werkzeug.serving import make_server

os.environ.setdefault("SERVICE_ACCOUNT_USERNAME", "teller1")
os.environ.setdefault("SERVICE_ACCOUNT_PASSWORD", "correcthorsebattery")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-not-for-production")

from cua_runner.mock_app import data as mock_data  # noqa: E402
from cua_runner.mock_app.app import app as flask_app  # noqa: E402

# Matches the port baked into the checked-in artifact's allowed_scope.domains
# (127.0.0.1:5000) so tests don't need to patch the artifact just to run --
# don't run the dev server manually on this port while running the suite.
TEST_PORT = 5000
BASE_URL = f"http://127.0.0.1:{TEST_PORT}/"

_PRISTINE_MEMBERS = copy.deepcopy(mock_data.MEMBERS)


@pytest.fixture(autouse=True)
def reset_mock_data():
    """Every test starts from the same pristine member dataset, since
    successful (irreversible) replays mutate it -- without this, test
    order would silently change outcomes (e.g. hitting sub-account caps
    early)."""
    mock_data.MEMBERS.clear()
    mock_data.MEMBERS.update(copy.deepcopy(_PRISTINE_MEMBERS))
    yield


@pytest.fixture(scope="session")
def live_app():
    server = make_server("127.0.0.1", TEST_PORT, flask_app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield BASE_URL
    server.shutdown()


@pytest.fixture(autouse=True)
def isolate_evidence_writes(tmp_path, monkeypatch):
    """Running the test suite must never pollute the real /evidence/
    directory -- that's the actual deliverable evidence, not test
    scratch output. Every evidence-writing entry point is redirected to
    a per-test tmp directory automatically."""
    import cua_runner.replay.engine as engine_mod
    import cua_runner.escalation.handoff as handoff_mod

    replay_root = tmp_path / "evidence" / "replays"
    escalation_root = tmp_path / "evidence" / "escalations"

    def fake_replay_evidence_dir(capability_id):
        d = replay_root / capability_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def fake_escalation_evidence_dir(escalation_id):
        d = escalation_root / escalation_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(engine_mod, "_evidence_dir", fake_replay_evidence_dir)
    monkeypatch.setattr(handoff_mod, "_evidence_dir", fake_escalation_evidence_dir)

    # Same isolation for the recorder: building test capabilities must
    # never write into the real, checked-in /artifacts/ directory.
    import cua_runner.agent.recorder as recorder_mod
    monkeypatch.setattr(recorder_mod, "ARTIFACTS_DIR", tmp_path / "artifacts")
    yield
