from conftest import supplier

from rfp_evaluation.rank import rank, rank_with_weights


def test_worked_example_from_the_readme():
    first, second = rank([supplier("A", [8, 6, 10], [50, 30, 20]), supplier("B", [10, 3, 5], [50, 30, 20])])
    assert first["supplier_name"] == "A"
    assert first["absolute_score"] == 78 and first["ppi"] == 90
    assert second["absolute_score"] == 69 and second["ppi"] == 75


def test_benchmark_gap_and_relative():
    first, second = rank([supplier("A", [8, 6, 10], [50, 30, 20]), supplier("B", [10, 3, 5], [50, 30, 20])])
    assert [c["benchmark"] for c in first["criteria"]] == [10, 6, 10]
    assert [c["gap"] for c in second["criteria"]] == [0, -3, -5]
    assert [c["relative_pct"] for c in second["criteria"]] == [100, 50, 50]


def test_zero_benchmark_gives_zero_relative():
    ranked = rank([supplier("A", [0, 8], [50, 50]), supplier("B", [0, 4], [50, 50])])
    assert all(s["criteria"][0]["relative_pct"] == 0 for s in ranked)


def test_ties_go_to_the_earlier_submission():
    ranked = rank([supplier("Late", [5, 5], [50, 50], "2026-09-05"), supplier("Early", [5, 5], [50, 50], "2026-09-01")])
    assert [s["supplier_name"] for s in ranked] == ["Early", "Late"]
    assert "submitted earlier" in ranked[1]["rank_reason"]


def test_then_to_the_higher_experience_rating():
    ranked = rank([supplier("Low", [5, 5], [50, 50], experience=2), supplier("High", [5, 5], [50, 50], experience=4)])
    assert [s["supplier_name"] for s in ranked] == ["High", "Low"]


def test_then_to_the_supplier_name():
    ranked = rank([supplier("Beta", [5, 5], [50, 50]), supplier("alpha", [5, 5], [50, 50])])
    assert [s["supplier_name"] for s in ranked] == ["alpha", "Beta"]
    assert [s["final_rank"] for s in ranked] == [1, 2]


def test_other_weights_can_change_the_leader():
    ranked = rank([supplier("Tech", [10, 4], [70, 30]), supplier("Price", [6, 10], [70, 30])])
    assert ranked[0]["supplier_name"] == "Tech"
    assert rank_with_weights(ranked, {1: 10, 2: 90})[0]["supplier_name"] == "Price"
