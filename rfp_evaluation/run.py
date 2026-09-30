import operator
import secrets
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from rfp_evaluation import store
from rfp_evaluation.evaluate import RETRY_PROMPT, ask_model, first_messages, get_settings, hide_secrets, mock_reply
from rfp_evaluation.extract import find_quote, read_pdf
from rfp_evaluation.rank import rank
from rfp_evaluation.validate import check_inputs, clean_reply, criteria_problems, parse_json

MAX_ATTEMPTS = 2


class SupplierState(TypedDict, total=False):
    proposal: object
    criteria: list
    settings: object
    pages: list
    read_error: str
    messages: list
    reply: str
    attempts: int
    result: dict


class BatchState(TypedDict, total=False):
    results: Annotated[list, operator.add]
    ranked: list


def new_run_id():
    return f"RFP-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(2).upper()}"


def reply_is_json(text):
    try:
        parse_json(text)
        return True
    except ValueError:
        return False


def extract(state):
    proposal = state["proposal"]
    pages, error = read_pdf(proposal.data)
    messages = first_messages(proposal.supplier_name.strip(), state["criteria"], pages)
    return {"pages": pages, "read_error": error, "messages": messages, "attempts": 0}


def evaluate(state):
    settings, messages = state["settings"], state["messages"]
    if settings.live:
        reply = ask_model(messages, settings)
    else:
        reply = mock_reply(state["proposal"].supplier_name.strip(), state["pages"], state["criteria"])
    if not reply_is_json(reply):
        messages = messages + [{"role": "assistant", "content": reply}, {"role": "user", "content": RETRY_PROMPT}]
    return {"reply": reply, "messages": messages, "attempts": state["attempts"] + 1}


def check_quotes(card, pages):
    for result in card.criteria:
        if not result.evidence:
            continue
        result.grounded, page = find_quote(pages, result.evidence)
        if result.grounded:
            result.evidence_page = page


def validate(state):
    proposal, criteria = state["proposal"], state["criteria"]
    name = proposal.supplier_name.strip()
    if state.get("read_error"):
        card = clean_reply({"criteria": []}, criteria, name)
        card.warnings = [f"{name}: {state['read_error']}, every criterion scored 0."]
        card.risks = ["The proposal could not be read."]
    else:
        card = clean_reply(state["reply"], criteria, name)
    check_quotes(card, state["pages"])
    result = {"supplier_name": name, "submission_date": str(proposal.submission_date),
              "experience_rating": float(proposal.experience_rating), "file_name": proposal.file_name,
              "page_count": len(state["pages"]), "attempts": state.get("attempts", 0)}
    return {"result": {**result, **card.model_dump()}}


def after_extract(state):
    return "validate" if state.get("read_error") else "evaluate"


def after_evaluate(state):
    if not reply_is_json(state["reply"]) and state["attempts"] < MAX_ATTEMPTS:
        return "evaluate"
    return "validate"


def supplier_graph():
    graph = StateGraph(SupplierState)
    graph.add_node("extract", extract)
    graph.add_node("evaluate", evaluate)
    graph.add_node("validate", validate)
    graph.add_edge(START, "extract")
    graph.add_conditional_edges("extract", after_extract, ["evaluate", "validate"])
    graph.add_conditional_edges("evaluate", after_evaluate, ["evaluate", "validate"])
    graph.add_edge("validate", END)
    return graph.compile()


def batch_graph(run_id, proposals, criteria, settings):
    def start(state):
        return {}

    def send_suppliers(state):
        return [Send("supplier", {"index": i}) for i in range(len(proposals))]

    per_supplier = supplier_graph()

    def supplier(state):
        start_state = {"proposal": proposals[state["index"]], "criteria": criteria, "settings": settings}
        result = per_supplier.invoke(start_state)["result"]
        return {"results": [{**result, "order": state["index"]}]}

    def rank_suppliers(state):
        results = sorted(state["results"], key=lambda r: r.pop("order"))
        return {"ranked": rank(results)}

    def save(state):
        warnings = [w for s in state["ranked"] for w in s["warnings"]]
        store.save_results(run_id, state["ranked"])
        store.finish_run(run_id, "COMPLETED", warnings)
        return {}

    graph = StateGraph(BatchState)
    graph.add_node("start", start)
    graph.add_node("supplier", supplier)
    graph.add_node("rank", rank_suppliers)
    graph.add_node("store", save)
    graph.add_edge(START, "start")
    graph.add_conditional_edges("start", send_suppliers, ["supplier"])
    graph.add_edge("supplier", "rank")
    graph.add_edge("rank", "store")
    graph.add_edge("store", END)
    return graph.compile()


def run_evaluation(proposals, title="", settings=None, progress=None):
    settings = settings or get_settings()
    progress = progress or (lambda message: None)
    problems = check_inputs(proposals) + criteria_problems(store.get_criteria())
    if problems:
        raise ValueError("\n".join(problems))

    criteria = store.get_criteria(active_only=True)
    run_id = new_run_id()
    store.create_run(run_id, title or f"{len(proposals)} suppliers", settings.label, criteria)
    progress(f"Run {run_id}: sending {len(proposals)} proposals to {settings.label}")
    try:
        graph = batch_graph(run_id, proposals, criteria, settings)
        for update in graph.stream({"results": []}, {"max_concurrency": 4}, stream_mode="updates"):
            for result in (update.get("supplier") or {}).get("results", []):
                progress(f"{result['supplier_name']} scored")
            if "rank" in update:
                progress("Suppliers ranked")
        progress("Results saved")
    except Exception as error:
        message = hide_secrets(f"{type(error).__name__}: {error}", settings)
        if type(error).__name__ == "RateLimitError":
            message = "The model's free rate limit was reached. Wait a minute and run again."
        store.finish_run(run_id, "FAILED", [], error=message)
        raise RuntimeError(message) from None
    return store.get_run(run_id)


def override_score(run_id, supplier, criterion_id, new_score, reason, actor="reviewer"):
    if not reason.strip():
        raise ValueError("Please give a reason for the change.")
    run = store.get_run(run_id)
    if run["locked"]:
        raise ValueError("This run is locked.")
    old_score = None
    for s in run["suppliers"]:
        for c in s["criteria"]:
            if s["supplier_name"] == supplier and c["criterion_id"] == criterion_id:
                old_score = c["score"]
                c["score"] = min(max(float(new_score), 0.0), c["max_score"])
                c["status"] = "OVERRIDDEN"
                c["override_reason"] = reason.strip()
    if old_score is None:
        raise ValueError("That criterion was not found for this supplier.")
    store.save_results(run_id, rank(run["suppliers"]))
    store.add_event(run_id, "OVERRIDE", reason.strip(), supplier, criterion_id, old_score, float(new_score), actor)
    return store.get_run(run_id)
