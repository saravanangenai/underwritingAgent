from underwriting_agent.samples import load_sample


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_clean_submission_auto_approves_and_is_logged(client):
    response = client.post("/underwrite", json=load_sample("clean"))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["decision"]["action"] == "auto_approve"
    assert len(body["enrichment"]) == 3

    sid = body["submission_id"]
    assert client.get(f"/submissions/{sid}").json()["status"] == "completed"
    logged = client.get(f"/submissions/{sid}/decision").json()
    assert logged["decision"]["action"] == "auto_approve"
    assert logged["rules_version"] and logged["model_version"]


def test_high_risk_submission_is_referred_with_cited_flags(client):
    body = client.post("/underwrite", json=load_sample("high_risk")).json()
    assert body["status"] == "referred"
    assert body["decision"]["action"] == "refer_to_underwriter"
    rule_ids = {f["rule_id"] for f in body["risk_memo"]["flags"]}
    assert {"SR-010", "SR-011", "SR-012", "HZ-001"} <= rule_ids
    assert all(f["source_field"] for f in body["risk_memo"]["flags"])


def test_old_roof_is_declined(client):
    body = client.post("/underwrite", json=load_sample("decline")).json()
    assert body["status"] == "declined"
    assert body["decision"]["action"] == "auto_decline"
    assert "HS-001" in {f["rule_id"] for f in body["risk_memo"]["flags"]}


def test_incomplete_submission_needs_more_info(client):
    response = client.post("/underwrite", json=load_sample("incomplete"))
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "needs_more_info"
    assert "coverage.coverage_a_limit is required" in body["missing_fields"]

    sid = body["submission_id"]
    assert client.get(f"/submissions/{sid}").json()["status"] == "needs_more_info"
    assert client.get(f"/submissions/{sid}/decision").status_code == 404


def test_duplicate_submission_id_is_rejected(client):
    payload = {**load_sample("clean"), "submission_id": "SUB-DUP-1"}
    assert client.post("/underwrite", json=payload).status_code == 200
    assert client.post("/underwrite", json=payload).status_code == 409


def test_unknown_submission_is_404(client):
    assert client.get("/submissions/NOPE").status_code == 404


def test_acord_text_submission_runs_through_the_llm(llm_client):
    response = llm_client.post("/underwrite", json=load_sample("acord_text"))
    assert response.status_code == 200
    body = response.json()
    assert body["decision"]["action"] == "refer_to_underwriter"  # the pool triggers SR-010
    assert "SR-010" in {f["rule_id"] for f in body["risk_memo"]["flags"]}

    logged = llm_client.get(f"/submissions/{body['submission_id']}/decision").json()
    assert logged["model_version"].startswith("intake=fake/fake-model")


def test_acord_text_without_llm_is_503(client):
    response = client.post("/underwrite", json=load_sample("acord_text"))
    assert response.status_code == 503
    assert "needs an LLM provider" in response.json()["detail"]
