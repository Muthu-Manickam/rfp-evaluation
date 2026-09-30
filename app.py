import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from rfp_evaluation import store
from rfp_evaluation.evaluate import get_settings
from rfp_evaluation.export import excel_workbook, leaderboard_table, pdf_report, scores_table
from rfp_evaluation.rank import rank_with_weights
from rfp_evaluation.run import override_score, run_evaluation
from rfp_evaluation.validate import Proposal, check_inputs, criteria_problems

DATA = Path(__file__).parent / "data"
SAMPLES = {
    "Meridian_Softworks_Technical_and_Commercial_Proposal.pdf": ("Meridian Softworks", date(2026, 9, 10), 4.5),
    "QuickDesk - Proposal for Sundaram Retail.pdf": ("QuickDesk Solutions", date(2026, 9, 8), 2.0),
    "Tarang Systems_Response_SRL-IT-2026-014.pdf": ("Tarang Systems", date(2026, 9, 9), 4.0),
    "NSCS_Proposal_Final (signed).pdf": ("Northstar Consulting", date(2026, 9, 11), 5.0),
}
BAD_FILES = ["Scan_20260911_0932.pdf", "Proposal_upload_final.pdf"]
SCORE_COLUMNS = {
    "Absolute score": st.column_config.NumberColumn("Absolute score", format="%.2f"),
    "PPI": st.column_config.NumberColumn("PPI", format="%.2f"),
}

st.set_page_config(page_title="RFP Evaluation", page_icon=":material/fact_check:", layout="wide")
st.markdown("""<style>
[data-testid="stToolbarActions"], [data-testid="stMainMenu"], [data-testid="stAppDeployButton"],
[data-testid="stDecoration"], footer {display: none;}
.block-container {padding-top: 2rem; max-width: 1150px;}
</style>""", unsafe_allow_html=True)
store.setup()
settings = get_settings()
state = st.session_state


def completed_runs():
    return [r for r in store.list_runs() if r["status"] == "COMPLETED"]


def short_time(value):
    return (value or "")[:16].replace("T", " ")


def overview_page():
    st.title("Overview")
    st.write("Upload supplier proposals for a tender, score them with an LLM against your criteria, "
             "and get a ranked shortlist with the evidence behind every score.")
    runs = completed_runs()
    if runs:
        latest = store.get_run(runs[0]["rfp_run_id"])
        st.subheader("Latest evaluation")
        st.write(f"**{latest['title']}**, {latest['created_at'][:10]}. "
                 f"Recommended supplier: **{latest['suppliers'][0]['supplier_name']}**")
        st.dataframe(leaderboard_table(latest)[["Rank", "Supplier", "Absolute score", "PPI"]], hide_index=True,
                     width="stretch", column_config=SCORE_COLUMNS)
    else:
        st.info("No evaluations yet.")

    st.subheader("How to use it")
    st.markdown("1. **Criteria**: check the scoring criteria and weights. Active weights must add up to 100%.\n"
                "2. **Evaluate**: upload the supplier PDFs, fill in their details and run the evaluation.\n"
                "3. **Results**: see the ranking and the evidence for each score, adjust or lock the decision, "
                "and download the report.")


def edit_criteria():
    table = pd.DataFrame(store.get_criteria(),
                         columns=["criterion_id", "name", "description", "weight", "max_score", "is_active"])
    table["is_active"] = table["is_active"].astype(bool)
    edited = st.data_editor(table, num_rows="dynamic", hide_index=True, width="stretch", column_config={
        "criterion_id": st.column_config.NumberColumn("ID", min_value=1, step=1, required=True, width="small"),
        "name": st.column_config.TextColumn("Criterion", required=True),
        "description": st.column_config.TextColumn("What to look for"),
        "weight": st.column_config.NumberColumn("Weight %", min_value=0, max_value=100, required=True, width="small"),
        "max_score": st.column_config.NumberColumn("Max score", min_value=1, required=True, default=10,
                                                   width="small"),
        "is_active": st.column_config.CheckboxColumn("Active", default=True, width="small"),
    })
    return [row for row in edited.to_dict("records") if str(row.get("name") or "").strip()]


def criteria_page():
    st.title("Criteria")
    rows = edit_criteria()
    total = sum(float(row["weight"] or 0) for row in rows if row.get("is_active"))
    st.write(f"Total weight: **{total:g}%**")

    problems = criteria_problems(rows)
    ids = [row["criterion_id"] for row in rows]
    if any(pd.isna(i) for i in ids) or len(set(ids)) != len(ids):
        problems.append("Every criterion needs a unique ID.")
    for problem in problems:
        st.warning(problem)

    save, reset, _ = st.columns([1, 1.4, 3])
    if save.button("Save", type="primary", disabled=bool(problems), width="stretch"):
        store.save_criteria(rows)
        st.success("Criteria saved.")
    if reset.button("Restore defaults", width="stretch"):
        store.setup(reset_criteria=True)
        st.rerun()


def default_details(name):
    return SAMPLES.get(name) or (Path(name).stem.replace("_", " "), date.today(), 3.0)


def choose_files():
    state.setdefault("extra_files", {})
    uploads = st.file_uploader("Supplier proposals (PDF)", accept_multiple_files=True)
    samples, bad, clear, _ = st.columns([1.2, 1.4, 1, 2])
    if samples.button("Use sample proposals", width="stretch"):
        state.extra_files.update({name: (DATA / "proposals" / name).read_bytes() for name in SAMPLES})
    if bad.button("Add blank and corrupted PDFs", width="stretch"):
        state.extra_files.update({name: (DATA / "bad_files" / name).read_bytes() for name in BAD_FILES})
    if state.extra_files and clear.button("Clear", width="stretch"):
        state.extra_files = {}
        st.rerun()

    files = dict(state.extra_files)
    for upload in uploads or []:
        files[upload.name] = upload.getvalue()
    return files


def supplier_details(files):
    rows = []
    for name in files:
        supplier, submitted, experience = default_details(name)
        rows.append({"file": name, "supplier": supplier, "submitted": submitted, "experience": experience})
    edited = st.data_editor(pd.DataFrame(rows), hide_index=True, width="stretch", disabled=["file"],
                            key=f"details_{'_'.join(sorted(files))}", column_config={
                                "file": st.column_config.TextColumn("File"),
                                "supplier": st.column_config.TextColumn("Supplier name", required=True),
                                "submitted": st.column_config.DateColumn("Submission date", format="YYYY-MM-DD"),
                                "experience": st.column_config.NumberColumn("Experience (1-5)", step=0.5),
                            })
    proposals = []
    for row in edited.to_dict("records"):
        name = str(row["supplier"] or "").strip()
        proposals.append(Proposal(name, str(row["submitted"])[:10], row["experience"], row["file"], files[row["file"]]))
    return proposals


def run_and_show(proposals, title):
    error = None
    with st.status("Evaluating proposals...", expanded=True) as status:
        try:
            run = run_evaluation(proposals, title, settings, progress=status.write)
            status.update(label="Evaluation finished", state="complete", expanded=False)
        except (ValueError, RuntimeError) as exc:
            error = str(exc)
            status.update(label="Evaluation failed", state="error")
    if error:
        st.error(error)
        return
    state.run_id = run["rfp_run_id"]
    st.success(f"Done. Recommended supplier: **{run['suppliers'][0]['supplier_name']}**. Open **Results** for details.")
    for warning in run["warnings"]:
        st.warning(warning)
    summary = leaderboard_table(run)[["Rank", "Supplier", "Absolute score", "PPI"]]
    st.dataframe(summary, hide_index=True, width="stretch", column_config=SCORE_COLUMNS)


def evaluate_page():
    st.title("Evaluate")
    files = choose_files()
    if not files:
        st.info("Upload at least two supplier proposals, or use the sample set.")
        return

    st.subheader("Supplier details")
    proposals = supplier_details(files)
    problems = check_inputs(proposals) + criteria_problems(store.get_criteria())
    for problem in problems:
        st.warning(problem)

    title = st.text_input("Evaluation name", "Customer support platform - tender SRL/IT/2026/014")
    if not settings.live:
        st.caption("No LLM key is set, so proposals are scored offline by keyword matching (mock mode).")
    if st.button("Run evaluation", type="primary", disabled=bool(problems)):
        run_and_show(proposals, title)


def pick_run():
    runs = completed_runs()
    if not runs:
        st.info("No evaluations yet. Run one on the Evaluate page.")
        return None
    ids = [r["rfp_run_id"] for r in runs]
    labels = {r["rfp_run_id"]: f"{r['title']} ({short_time(r['created_at'])}, {r['rfp_run_id']})" for r in runs}
    current = state.get("run_id") if state.get("run_id") in ids else ids[0]
    state.run_id = st.selectbox("Evaluation", ids, index=ids.index(current), format_func=labels.get)
    return store.get_run(state.run_id)


def ranking_tab(run):
    leader = run["suppliers"][0]
    first, second, third = st.columns([2, 1, 1])
    first.metric("Recommended", leader["supplier_name"])
    second.metric("PPI", f"{leader['ppi']:.2f}")
    third.metric("Absolute score", f"{leader['absolute_score']:.2f}")
    table = leaderboard_table(run).drop(columns=["Submitted", "Experience"])
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "PPI": st.column_config.ProgressColumn("PPI", min_value=0, max_value=100, format="%.2f"),
        "Absolute score": st.column_config.ProgressColumn("Absolute score", min_value=0, max_value=100, format="%.2f"),
    })
    st.caption("Absolute score is the sum of (score / max) x weight. PPI is the weighted average of each score as a "
               "percentage of the best score for that criterion. Ties go to the higher PPI, then the earlier "
               "submission, then the higher experience rating, then the supplier name.")


def bullets(items):
    return "\n".join(f"- {item}" for item in items)


def show_criterion(c):
    with st.expander(f"{c['name']}: {c['score']:g} / {c['max_score']:g}"):
        st.write(c.get("justification") or "")
        if c.get("evidence"):
            where = f"page {c['evidence_page']}" if c.get("evidence_page") else "page unknown"
            found = "found in the PDF" if c.get("grounded") else "not found in the PDF"
            st.markdown(f"> {c['evidence']}\n\n*{where}, {found}*")
        for label, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses"),
                           ("Ask the supplier", "missing_information")):
            if c.get(key):
                st.markdown(f"**{label}**\n" + bullets(c[key]))


def scorecards_tab(run):
    suppliers = run["suppliers"]
    names = [s["supplier_name"] for s in suppliers]
    s = suppliers[names.index(st.selectbox("Supplier", names))]
    st.write(f"**Rank {s['final_rank']} of {len(names)}**, {s['file_name']}. {s.get('rank_reason', '')}")
    if s.get("overall_summary"):
        st.write(s["overall_summary"])
    st.dataframe(pd.DataFrame([{
        "Criterion": c["name"], "Weight %": c["weight"], "Score": f"{c['score']:g} / {c['max_score']:g}",
        "Benchmark": c["benchmark"], "Gap": c["gap"], "Relative %": round(c["relative_pct"], 1),
        "Confidence": c.get("confidence"), "Status": c.get("status"),
    } for c in s["criteria"]]), hide_index=True, width="stretch")
    for c in s["criteria"]:
        show_criterion(c)
    if s.get("risks"):
        st.markdown("**Risks**\n" + bullets(s["risks"]))


def compare_tab(run):
    suppliers = run["suppliers"]
    st.caption("Each supplier's score per criterion. The best score for each criterion is the benchmark.")
    matrix = pd.DataFrame({s["supplier_name"]: {c["name"]: c["score"] for c in s["criteria"]} for s in suppliers})
    matrix["Benchmark"] = matrix.max(axis=1)
    st.dataframe(matrix, width="stretch")

    st.subheader("What if the weights were different?")
    st.caption("The saved scores are ranked again with these weights. Nothing is saved and the LLM is not called.")
    weights = {}
    columns = st.columns(len(suppliers[0]["criteria"]))
    for column, c in zip(columns, suppliers[0]["criteria"], strict=True):
        weights[c["criterion_id"]] = column.slider(c["name"], 0, 100, int(round(c["weight"])), 5,
                                                   key=f"w_{run['rfp_run_id']}_{c['criterion_id']}")
    if sum(weights.values()) == 0:
        st.warning("Set at least one weight above 0.")
        return
    before = {s["supplier_name"]: s["final_rank"] for s in suppliers}
    simulated = rank_with_weights(suppliers, weights)
    st.dataframe(pd.DataFrame([{
        "Rank": s["final_rank"], "Change": before[s["supplier_name"]] - s["final_rank"], "Supplier": s["supplier_name"],
        "Absolute score": round(s["absolute_score"], 2), "PPI": round(s["ppi"], 2),
    } for s in simulated]), hide_index=True, width="stretch")
    if simulated[0]["supplier_name"] != suppliers[0]["supplier_name"]:
        st.warning(f"With these weights {simulated[0]['supplier_name']} would be recommended instead.")


def run_details_tab(run):
    st.dataframe(pd.DataFrame([
        ("RFP_RUN_ID", run["rfp_run_id"]), ("Name", run["title"]), ("Status", run["status"]), ("Model", run["model"]),
        ("Started (UTC)", short_time(run["created_at"])), ("Finished (UTC)", short_time(run["completed_at"])),
        ("Decision", f"Locked on {run['locked_at'][:10]}" if run["locked"] else "Open"),
    ], columns=["Field", "Value"]), hide_index=True, width="stretch")

    st.subheader(f"Warnings ({len(run['warnings'])})")
    if run["warnings"]:
        st.markdown(bullets(run["warnings"]))

    st.subheader("Download")
    run_id = run["rfp_run_id"]
    first, second, third, fourth = st.columns(4)
    first.download_button("Full result (JSON)", json.dumps(run, indent=2, default=str), f"{run_id}.json",
                          "application/json", type="primary", width="stretch")
    second.download_button("PDF report", pdf_report(run), f"{run_id}.pdf", "application/pdf", width="stretch")
    third.download_button("Excel", excel_workbook(run), f"{run_id}.xlsx", width="stretch")
    fourth.download_button("Scores (CSV)", scores_table(run).to_csv(index=False), f"{run_id}.csv", "text/csv",
                           width="stretch")


def review_tab(run):
    if run["locked"]:
        st.info("This decision is locked, so scores can no longer be changed.")
    else:
        st.subheader("Change a score")
        with st.form("override"):
            who, what, value = st.columns([2, 2, 1])
            supplier = who.selectbox("Supplier", [s["supplier_name"] for s in run["suppliers"]])
            chosen = next(s for s in run["suppliers"] if s["supplier_name"] == supplier)
            by_id = {c["criterion_id"]: c for c in chosen["criteria"]}
            criterion = what.selectbox("Criterion", list(by_id), format_func=lambda i: by_id[i]["name"])
            new_score = value.number_input("New score", 0.0, 100.0, float(by_id[criterion]["score"]), 0.5)
            reason = st.text_input("Reason", placeholder="For example: clarification call confirmed ISO 27001")
            if st.form_submit_button("Save change"):
                try:
                    override_score(run["rfp_run_id"], supplier, criterion, new_score, reason)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        st.subheader("Lock the decision")
        note = st.text_input("Approval note", placeholder="For example: approved by the evaluation committee")
        if st.button("Lock decision", disabled=not note.strip()):
            store.lock_run(run["rfp_run_id"], note.strip())
            st.rerun()

    if run["events"]:
        st.subheader("Review log")
        log = pd.DataFrame(run["events"])[["created_at", "event_type", "supplier_name", "criterion_id", "old_score",
                                           "new_score", "reason", "actor"]]
        st.dataframe(log, hide_index=True, width="stretch")


def results_page():
    st.title("Results")
    run = pick_run()
    if not run:
        return
    tabs = st.tabs(["Ranking", "Scorecards", "Compare", "Run details", "Review"])
    with tabs[0]:
        ranking_tab(run)
    with tabs[1]:
        scorecards_tab(run)
    with tabs[2]:
        compare_tab(run)
    with tabs[3]:
        run_details_tab(run)
    with tabs[4]:
        review_tab(run)


def history_page():
    st.title("History")
    runs = store.list_runs()
    if not runs:
        st.info("No evaluations yet.")
        return
    st.dataframe(pd.DataFrame([{
        "Name": r["title"], "Date": short_time(r["created_at"]), "Status": r["status"],
        "Suppliers": r["suppliers"], "Recommended": r["winner"], "Locked": bool(r["locked"]),
    } for r in runs]), hide_index=True, width="stretch")

    labels = {r["rfp_run_id"]: f"{r['title']} ({short_time(r['created_at'])})" for r in runs}
    run_id = st.selectbox("Run", list(labels), format_func=labels.get)
    run = next(r for r in runs if r["rfp_run_id"] == run_id)
    open_button, delete_button, confirm_box = st.columns([1, 1, 2])
    if open_button.button("Open in Results", width="stretch", disabled=run["status"] != "COMPLETED"):
        state.run_id = run_id
        state.go_to = "Results"
        st.rerun()
    confirmed = confirm_box.checkbox("Yes, delete this run", disabled=bool(run["locked"]), key=f"confirm_{run_id}")
    if delete_button.button("Delete", width="stretch", disabled=bool(run["locked"]) or not confirmed):
        store.delete_run(run_id)
        st.rerun()


PAGES = {"Overview": overview_page, "Criteria": criteria_page, "Evaluate": evaluate_page,
         "Results": results_page, "History": history_page}

if "go_to" in state:
    state.page = state.pop("go_to")

with st.sidebar:
    st.header("RFP Evaluation")
    page = st.radio("Go to", list(PAGES), key="page", label_visibility="collapsed")
    st.divider()
    st.caption(f"Model: {settings.label}")

PAGES[page]()
