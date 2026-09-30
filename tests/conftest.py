from pathlib import Path

import pytest

from rfp_evaluation import store
from rfp_evaluation.evaluate import Settings
from rfp_evaluation.validate import Proposal

DATA = Path(__file__).resolve().parent.parent / "data"
MOCK = Settings("gpt-6-luna", None, None, False)
SAMPLES = [
    ("Meridian Softworks", "2026-09-10", 4.5, "Meridian_Softworks_Technical_and_Commercial_Proposal.pdf"),
    ("QuickDesk Solutions", "2026-09-08", 2.0, "QuickDesk - Proposal for Sundaram Retail.pdf"),
    ("Tarang Systems", "2026-09-09", 4.0, "Tarang Systems_Response_SRL-IT-2026-014.pdf"),
    ("Northstar Consulting", "2026-09-11", 5.0, "NSCS_Proposal_Final (signed).pdf"),
]


@pytest.fixture(autouse=True)
def fresh_database(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    store.setup()


def sample_proposals():
    return [Proposal(name, day, rating, file, (DATA / "proposals" / file).read_bytes())
            for name, day, rating, file in SAMPLES]


def bad_file(name):
    return (DATA / "bad_files" / name).read_bytes()


def criterion(number, weight, name=None, max_score=10):
    return {"criterion_id": number, "name": name or f"C{number}", "description": "", "weight": weight,
            "max_score": max_score, "is_active": 1}


def supplier(name, scores, weights, submitted="2026-09-01", experience=3.0):
    criteria = []
    for number, (score, weight) in enumerate(zip(scores, weights, strict=True), start=1):
        criteria.append({"criterion_id": number, "name": f"C{number}", "weight": weight, "max_score": 10,
                         "score": score})
    return {"supplier_name": name, "submission_date": submitted, "experience_rating": experience, "criteria": criteria}
