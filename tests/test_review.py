"""Phase 8: underwriter review of referred submissions."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from underwriting_agent.api.main import create_app
from underwriting_agent.persistence.repository import entry_hash
from underwriting_agent.samples import load_sample
from underwriting_agent.schemas import DecisionLogEntry

NOTE = "Pool fence and self-latching gate confirmed by inspection photos."


def _referred(client) -> str:
    body = client.post("/underwrite", json=load_sample("high_risk")).json()
    assert body["status"] == "referred"
    return body["submission_id"]


def _review(client, sid, action="approve", note=NOTE, reviewer="A. Underwriter"):
    return client.post(
        f"/submissions/{sid}/review", json={"action": action, "note": note, "reviewer": reviewer}
    )


def test_approving_a_referral_appends_a_linked_entry(client):
    sid = _referred(client)
    automated = client.get(f"/submissions/{sid}/decision").json()

    response = _review(client, sid)
    assert response.status_code == 200
    entry = response.json()
    assert entry["decision"]["action"] == "underwriter_approve"
    assert entry["decision"]["confidence"] is None
    assert entry["decision"]["estimated_premium"] == automated["decision"]["estimated_premium"]
    assert entry["review"]["reviewer"] == "A. Underwriter"
    assert entry["review"]["note"] == NOTE
    assert entry["review"]["supersedes_entry_hash"] == automated["entry_hash"]
    assert entry["prev_hash"] == automated["entry_hash"]  # next link in the chain
    # The review carries the case forward, so the latest entry stands on its own.
    assert entry["risk_memo"]["flags"] == automated["risk_memo"]["flags"]
    assert entry["inputs"] == automated["inputs"]

    assert client.get(f"/submissions/{sid}").json()["status"] == "completed"
    assert client.get(f"/submissions/{sid}/decision").json()["entry_hash"] == entry["entry_hash"]
    assert client.get("/audit/verify").json() == {
        "ok": True,
        "entries_checked": 2,
        "first_bad_entry_id": None,
        "reason": None,
    }


@pytest.mark.parametrize(
    ("action", "recorded", "status"),
    [
        ("decline", "underwriter_decline", "declined"),
        ("request_info", "underwriter_request_info", "needs_more_info"),
    ],
)
def test_decline_and_request_info(client, action, recorded, status):
    sid = _referred(client)
    entry = _review(client, sid, action=action).json()
    assert entry["decision"]["action"] == recorded
    assert entry["decision"]["estimated_premium"] is None  # no quote unless approved
    assert client.get(f"/submissions/{sid}").json()["status"] == status


def test_history_lists_the_automated_decision_then_the_review(client):
    sid = _referred(client)
    _review(client, sid, action="decline")
    history = client.get(f"/submissions/{sid}/history").json()
    assert [h["decision"]["action"] for h in history] == [
        "refer_to_underwriter",
        "underwriter_decline",
    ]
    assert history[0]["review"] is None
    assert history[1]["review"]["action"] == "decline"


def test_queue_shows_who_reviewed(client):
    sid = _referred(client)
    _review(client, sid, reviewer="B. Reviewer")
    row = next(r for r in client.get("/submissions").json()["items"] if r["submission_id"] == sid)
    assert row["reviewed_by"] == "B. Reviewer"
    assert row["action"] == "underwriter_approve"
    assert row["status"] == "completed"


def test_only_referred_submissions_can_be_reviewed(client):
    clean = client.post("/underwrite", json=load_sample("clean")).json()["submission_id"]
    response = _review(client, clean)
    assert response.status_code == 409
    assert "this one is completed" in response.json()["detail"]

    sid = _referred(client)
    assert _review(client, sid).status_code == 200
    assert _review(client, sid, action="decline").status_code == 409  # already resolved
    assert len(client.get(f"/submissions/{sid}/history").json()) == 2


def test_unknown_submission_is_404(client):
    assert _review(client, "NOPE").status_code == 404
    assert client.get("/submissions/NOPE/history").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "approve", "note": "ok", "reviewer": "A. Underwriter"},  # note too short
        {"action": "approve", "note": " " * 20, "reviewer": "A. Underwriter"},  # blank note
        {"action": "approve", "note": NOTE, "reviewer": "  "},  # blank reviewer
        {"action": "escalate", "note": NOTE, "reviewer": "A. Underwriter"},  # unknown action
        {"action": "approve", "reviewer": "A. Underwriter"},  # missing note
    ],
)
def test_invalid_reviews_are_rejected(client, payload):
    sid = _referred(client)
    assert client.post(f"/submissions/{sid}/review", json=payload).status_code == 422
    assert client.get(f"/submissions/{sid}").json()["status"] == "referred"


def test_edited_review_note_is_detected(client, tmp_path):
    sid = _referred(client)
    _review(client, sid)
    with sqlite3.connect(tmp_path / "test.db") as db:
        db.execute(
            "UPDATE decision_log SET review_json = replace(review_json, 'confirmed', 'waived') "
            "WHERE id = 2"
        )
    result = client.get("/audit/verify").json()
    assert (result["ok"], result["first_bad_entry_id"]) == (False, 2)


def test_entries_without_a_review_hash_as_they_did_before_reviews_existed(client):
    sid = _referred(client)
    stored = DecisionLogEntry.model_validate(client.get(f"/submissions/{sid}/decision").json())
    # The pre-Phase 8 hash: the same content with no "review" key at all.
    content = stored.model_dump(mode="json", exclude={"prev_hash", "entry_hash", "review"})
    import hashlib

    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    old_style = hashlib.sha256(f"{stored.prev_hash}\n{canonical}".encode()).hexdigest()
    assert entry_hash(stored, stored.prev_hash) == old_style == stored.entry_hash


def test_a_phase_5_database_gains_the_review_column_in_place(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        sid = _referred(client)
    with sqlite3.connect(tmp_path / "test.db") as db:  # back to the pre-Phase 8 schema
        db.execute("ALTER TABLE decision_log DROP COLUMN review_json")

    with TestClient(create_app(settings)) as client:
        assert client.get("/audit/verify").json() == {
            "ok": True,
            "entries_checked": 1,
            "first_bad_entry_id": None,
            "reason": None,
        }
        assert _review(client, sid).status_code == 200
        assert client.get("/audit/verify").json()["entries_checked"] == 2
