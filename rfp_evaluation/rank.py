from datetime import date


def r4(x):
    return round(float(x), 4)


def as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def sort_key(s):
    return (-s["ppi"], as_date(s["submission_date"]), -float(s["experience_rating"]), s["supplier_name"].casefold())


def best_scores(suppliers):
    best = {}
    for s in suppliers:
        for c in s["criteria"]:
            best[c["criterion_id"]] = max(best.get(c["criterion_id"], 0.0), float(c["score"]))
    return best


def score_supplier(s, best):
    rows, absolute, weighted_relative, total_weight = [], 0.0, 0.0, 0.0
    for c in s["criteria"]:
        weight, max_score, score = float(c["weight"]), float(c["max_score"]), float(c["score"])
        benchmark = best[c["criterion_id"]]
        points = score / max_score * weight
        relative = score / benchmark * 100 if benchmark > 0 else 0.0
        absolute += points
        weighted_relative += relative * weight
        total_weight += weight
        rows.append({**c, "benchmark": r4(benchmark), "gap": r4(score - benchmark),
                     "relative_pct": r4(relative), "weighted_points": r4(points)})
    ppi = weighted_relative / total_weight if total_weight else 0.0
    return {**s, "criteria": rows, "absolute_score": r4(absolute), "ppi": r4(ppi)}


def rank(suppliers):
    if not suppliers:
        return []
    best = best_scores(suppliers)
    ranked = sorted([score_supplier(s, best) for s in suppliers], key=sort_key)
    for position, s in enumerate(ranked, start=1):
        s["final_rank"] = position
    ranked[0]["rank_reason"] = "Highest PPI in the batch." if len(ranked) > 1 else "Only supplier in the batch."
    for above, below in zip(ranked, ranked[1:], strict=False):
        below["rank_reason"] = why_below(above, below)
    return ranked


def why_below(above, below):
    name = above["supplier_name"]
    if above["ppi"] != below["ppi"]:
        return f"Lower PPI than {name} ({below['ppi']:.2f} vs {above['ppi']:.2f})."
    if as_date(above["submission_date"]) != as_date(below["submission_date"]):
        return f"Same PPI as {name}, who submitted earlier ({above['submission_date']})."
    if float(above["experience_rating"]) != float(below["experience_rating"]):
        return f"Same PPI and date as {name}, who has a higher experience rating."
    return f"Same PPI, date and experience as {name}; ordered by name."


def rank_with_weights(ranked, weights):
    total = sum(weights.values()) or 1.0
    return rank([{**s, "criteria": [{**c, "weight": weights[c["criterion_id"]] / total * 100}
                                    for c in s["criteria"] if weights.get(c["criterion_id"], 0) > 0]}
                 for s in ranked])
