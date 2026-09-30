"""Phase 5: the hash-chained decision log and what it records."""

import asyncio
import sqlite3

from fastapi.testclient import TestClient

from tests.conftest import _seed, sign_in
from tests.fakes import ACORD_EXTRACTION, RoutingLLM, risk_review
from underwriting_agent.api.main import create_app
from underwriting_agent.persistence.repository import GENESIS_HASH
from underwriting_agent.samples import load_sample


def _post_three(client) -> list[dict]:
    ids = [
        client.post("/underwrite", json=load_sample(name)).json()["submission_id"]
        for name in ("clean", "high_risk", "decline")
    ]
    return [client.get(f"/submissions/{sid}/decision").json() for sid in ids]


def test_decisions_form_a_hash_chain(client):
    first, second, third = _post_three(client)
    assert first["prev_hash"] == GENESIS_HASH
    assert second["prev_hash"] == first["entry_hash"]
    assert third["prev_hash"] == second["entry_hash"]
    assert len({first["entry_hash"], second["entry_hash"], third["entry_hash"]}) == 3
    assert client.get("/audit/verify").json() == {
        "ok": True,
        "entries_checked": 3,
        "first_bad_entry_id": None,
        "reason": None,
    }


def test_empty_log_verifies(client):
    assert client.get("/audit/verify").json()["ok"] is True


def test_record_keeps_the_inputs_for_replay(client):
    entry = _post_three(client)[0]
    assert entry["inputs"]["submission"]["property_address"].startswith("22 Willow Creek Dr")
    sources = [e["source"] for e in entry["inputs"]["enrichment"]]
    assert sources == ["hazard_mock", "loss_history_mock", "property_data_mock"]
    assert entry["prompt_versions"] == {}  # no LLM touched this decision
    assert entry["llm_review_raw"] is None


def test_llm_decision_records_prompts_and_raw_reply(settings, users):
    settings.llm_risk_assessment = True
    llm = RoutingLLM(intake=ACORD_EXTRACTION, risk=risk_review("refer", 0.8))
    with TestClient(create_app(settings, llm=llm)) as client:
        sign_in(client)
        body = client.post("/underwrite", json=load_sample("acord_text")).json()
        entry = client.get(f"/submissions/{body['submission_id']}/decision").json()
        assert client.get("/audit/verify").json()["ok"] is True
    assert set(entry["prompt_versions"]) == {"acord_intake", "risk_review"}
    assert all(len(v) == 12 for v in entry["prompt_versions"].values())
    assert entry["llm_review_raw"]["recommendation"] == "refer"
    # The raw reply lives in the audit record, not in the memo returned to callers.
    assert "raw_review" not in body["risk_memo"]
    assert "raw_review" not in entry["risk_memo"]


def _db(tmp_path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "test.db")


def test_edited_entry_is_detected(client, tmp_path):
    _post_three(client)
    with _db(tmp_path) as db:  # someone quietly turns a decline into an approval
        db.execute("UPDATE decision_log SET action = 'auto_approve' WHERE id = 3")
    result = client.get("/audit/verify").json()
    assert result["ok"] is False
    assert result["first_bad_entry_id"] == 3
    assert "edited" in result["reason"]


def test_edit_inside_json_fields_is_detected(client, tmp_path):
    _post_three(client)
    with _db(tmp_path) as db:
        db.execute(
            "UPDATE decision_log SET risk_memo_json = replace(risk_memo_json, 'SR-010', 'SR-099') "
            "WHERE id = 2"
        )
    result = client.get("/audit/verify").json()
    assert (result["ok"], result["first_bad_entry_id"]) == (False, 2)


def test_deleted_entry_is_detected(client, tmp_path):
    _post_three(client)
    with _db(tmp_path) as db:
        db.execute("DELETE FROM decision_log WHERE id = 2")
    result = client.get("/audit/verify").json()
    assert result["ok"] is False
    assert result["first_bad_entry_id"] == 3
    assert "removed or reordered" in result["reason"]


def test_rewriting_one_hash_does_not_hide_an_edit(client, tmp_path):
    # Recomputing the edited row's hash still breaks the link to the next row.
    _post_three(client)
    with _db(tmp_path) as db:
        db.execute("UPDATE decision_log SET action = 'auto_approve', entry_hash = 'f' WHERE id = 2")
    result = client.get("/audit/verify").json()
    assert result["ok"] is False and result["first_bad_entry_id"] == 2


def test_legacy_decision_log_is_set_aside(settings, tmp_path):
    with _db(tmp_path) as db:  # a pre-Phase 5 database
        db.executescript(
            """
            CREATE TABLE submissions (id VARCHAR PRIMARY KEY, status VARCHAR, raw_payload TEXT,
                                      created_at DATETIME, updated_at DATETIME);
            CREATE TABLE decision_log (id INTEGER PRIMARY KEY, submission_id VARCHAR,
                action VARCHAR, confidence FLOAT, rationale TEXT, estimated_premium FLOAT,
                risk_memo_json TEXT, model_version VARCHAR, rules_version VARCHAR,
                created_at DATETIME);
            CREATE INDEX ix_decision_log_submission_id ON decision_log (submission_id);
            INSERT INTO decision_log (submission_id, action) VALUES ('OLD-1', 'auto_approve');
            """
        )
    asyncio.run(_seed(settings.database_url))  # users, added after the old schema exists
    with TestClient(create_app(settings)) as client:
        sign_in(client)
        assert client.post("/underwrite", json=load_sample("clean")).status_code == 200
        assert client.get("/audit/verify").json()["entries_checked"] == 1

    with _db(tmp_path) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        legacy = next(t for t in tables if t.startswith("decision_log_legacy_"))
        assert db.execute(f"SELECT submission_id FROM {legacy}").fetchall() == [("OLD-1",)]
