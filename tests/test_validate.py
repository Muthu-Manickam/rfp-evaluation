from conftest import criterion, sample_proposals

from rfp_evaluation.extract import find_quote, read_pdf
from rfp_evaluation.validate import Proposal, check_inputs, clean_reply, criteria_problems


def test_bad_scores_are_repaired():
    criteria = [criterion(1, 40), criterion(2, 30), criterion(3, 30)]
    reply = {"criteria": [{"criterion_id": 1, "score": 15, "confidence": 90},
                          {"criterion_id": 2, "score": "7/10"},
                          {"criterion_id": 99, "score": 5}]}
    card = clean_reply(reply, criteria, "S")
    assert [(c.score, c.status) for c in card.criteria] == [(10, "CLIPPED"), (7, "COERCED"), (0, "MISSING")]
    assert card.criteria[0].confidence == 0.9
    assert any("unknown criterion" in w for w in card.warnings)


def test_text_that_is_not_json_scores_zero():
    card = clean_reply("Sure, here is my evaluation!", [criterion(1, 100)], "S")
    assert card.criteria[0].score == 0 and card.criteria[0].status == "INVALID"
    assert "not valid JSON" in card.warnings[0]


def test_json_inside_a_code_block_is_read():
    card = clean_reply('```json\n{"criteria": [{"criterion_id": 1, "score": 6}]}\n```', [criterion(1, 100)], "S")
    assert card.criteria[0].score == 6


def test_non_numeric_score_scores_zero():
    card = clean_reply({"criteria": [{"criterion_id": 1, "score": "excellent"}]}, [criterion(1, 100)], "S")
    assert card.criteria[0].score == 0 and card.criteria[0].status == "INVALID"


def test_criteria_rules():
    assert criteria_problems([criterion(1, 60), criterion(2, 40)]) == []
    assert any("110%" in p for p in criteria_problems([criterion(1, 60), criterion(2, 50)]))
    assert any("unique" in p for p in criteria_problems([criterion(1, 50, "X"), criterion(2, 50, "x")]))


def test_good_inputs_pass():
    assert check_inputs(sample_proposals()) == []


def test_bad_inputs_are_reported():
    bad = [Proposal("A", "2026-09-01", 9, "a.pdf", b"1"),
           Proposal("a", "2999-01-01", 3, "b.txt", b"2"),
           Proposal("B", "2026-09-01", 3, "c.pdf", b"1")]
    text = " ".join(check_inputs(bad))
    for expected in ("between 1 and 5", "more than once", "not in the future", "only PDF", "same file"):
        assert expected in text
    assert "at least two" in " ".join(check_inputs(sample_proposals()[:1]))


def test_sample_pdf_text_is_read():
    pages, error = read_pdf(sample_proposals()[0].data)
    assert error is None and len(pages) >= 2


def test_quotes_are_found_in_the_pdf():
    pages = ["Our platform scales to 12,000 concurrent agents across three regions."]
    assert find_quote(pages, "scales to 12,000 concurrent agents") == (True, 1)
    assert find_quote(pages, "we hold ISO 27001 and SOC 2 Type II")[0] is False


def test_two_column_slides_keep_sentences_together():
    deck = next(p for p in sample_proposals() if p.supplier_name == "Tarang Systems")
    pages, _ = read_pdf(deck.data)
    quote = "Data stays in India; encryption at rest and in transit; role-based access with audit logs."
    assert find_quote(pages, quote) == (True, 4)
