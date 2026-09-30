import json
import threading

import pytest
from conftest import MOCK, bad_file, sample_proposals

from rfp_evaluation import run as run_module
from rfp_evaluation import store
from rfp_evaluation.evaluate import Settings
from rfp_evaluation.export import excel_workbook, pdf_report
from rfp_evaluation.run import override_score, run_evaluation
from rfp_evaluation.validate import Proposal


def test_default_criteria_are_seeded():
    rows = store.get_criteria()
    assert len(rows) == 5 and sum(r["weight"] for r in rows) == 100


def test_full_run_in_mock_mode():
    result = run_evaluation(sample_proposals(), "Test", MOCK)
    assert result["status"] == "COMPLETED"
    assert [s["final_rank"] for s in result["suppliers"]] == [1, 2, 3, 4]
    assert all(len(s["criteria"]) == 5 for s in result["suppliers"])
    assert any(c["grounded"] for s in result["suppliers"] for c in s["criteria"])
    listed = store.list_runs()[0]
    assert listed["suppliers"] == 4 and listed["winner"] == result["suppliers"][0]["supplier_name"]


def test_blank_and_corrupted_pdfs_score_zero():
    proposals = sample_proposals()[:2] + [
        Proposal("Blank Co", "2026-09-01", 3, "blank.pdf", bad_file("Scan_20260911_0932.pdf")),
        Proposal("Broken Ltd", "2026-09-01", 3, "broken.pdf", bad_file("Proposal_upload_final.pdf")),
    ]
    result = run_evaluation(proposals, "Bad files", MOCK)
    unreadable = [s for s in result["suppliers"] if s["supplier_name"] in ("Blank Co", "Broken Ltd")]
    assert all(s["absolute_score"] == 0 and s["attempts"] == 0 for s in unreadable)
    assert sum("no readable text" in w for w in result["warnings"]) == 2


def test_a_reply_that_is_not_json_is_asked_again(monkeypatch):
    good = json.dumps({"criteria": [{"criterion_id": i, "score": 8} for i in range(1, 6)]})
    replies = {}

    def fake_model(messages, settings):
        supplier = messages[1]["content"].splitlines()[0]
        replies[supplier] = replies.get(supplier, 0) + 1
        return "not json" if replies[supplier] == 1 else good

    monkeypatch.setattr(run_module, "ask_model", fake_model)
    result = run_evaluation(sample_proposals()[:2], "Retry", Settings("groq/openai/gpt-oss-120b", "key", None, True))
    assert all(s["attempts"] == 2 for s in result["suppliers"])
    assert all(c["score"] == 8 for s in result["suppliers"] for c in s["criteria"])


def test_a_failed_run_does_not_show_the_key(monkeypatch):
    def failing_model(messages, settings):
        raise RuntimeError("401 bad key sk-secret-123 <html><h1>Blocked</h1> You are unable to access "
                           "example.com</html>" + " padding" * 100)

    monkeypatch.setattr(run_module, "ask_model", failing_model)
    with pytest.raises(RuntimeError) as error:
        settings = Settings("groq/openai/gpt-oss-120b", "sk-secret-123", "https://llm.internal.example.com/v1", True)
        run_evaluation(sample_proposals()[:2], "Fail", settings)
    message = str(error.value)
    assert "sk-secret-123" not in message and "example.com" not in message
    assert "<" not in message and len(message) <= 300
    assert store.list_runs()[0]["status"] == "FAILED"


def test_progress_is_reported_from_the_main_thread():
    threads = []
    run_evaluation(sample_proposals(), "Progress", MOCK,
                   progress=lambda message: threads.append(threading.current_thread() is threading.main_thread()))
    assert len(threads) == 7 and all(threads)


def test_score_change_and_lock():
    result = run_evaluation(sample_proposals(), "Override", MOCK)
    last = result["suppliers"][-1]["supplier_name"]
    updated = override_score(result["rfp_run_id"], last, 1, 10, "Clarified on a call")
    changed = next(s for s in updated["suppliers"] if s["supplier_name"] == last)
    assert changed["criteria"][0]["status"] == "OVERRIDDEN"
    assert updated["events"][0]["event_type"] == "OVERRIDE"
    with pytest.raises(ValueError):
        override_score(result["rfp_run_id"], last, 1, 5, "")
    store.lock_run(result["rfp_run_id"], "Approved")
    with pytest.raises(ValueError):
        override_score(result["rfp_run_id"], last, 1, 5, "Too late")


def test_exports_are_valid_files():
    result = run_evaluation(sample_proposals(), "Exports", MOCK)
    assert pdf_report(result).startswith(b"%PDF")
    assert excel_workbook(result).startswith(b"PK")
