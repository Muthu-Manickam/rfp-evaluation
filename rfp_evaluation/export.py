import io
from datetime import datetime
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

STYLES = getSampleStyleSheet()
SMALL = STYLES["BodyText"].clone("small", fontSize=8, leading=10)
PAGE_WIDTH = A4[0] - 32 * mm


def leaderboard_table(run):
    return pd.DataFrame([{
        "Rank": s["final_rank"], "Supplier": s["supplier_name"], "Absolute score": round(s["absolute_score"], 2),
        "PPI": round(s["ppi"], 2),
        "Submitted": s["submission_date"], "Experience": s["experience_rating"], "Reason": s.get("rank_reason", ""),
    } for s in run["suppliers"]])


def scores_table(run):
    return pd.DataFrame([{
        "Supplier": s["supplier_name"], "Criterion": c["name"], "Weight %": c["weight"], "Score": c["score"],
        "Max": c["max_score"], "Benchmark": c["benchmark"], "Gap": c["gap"], "Relative %": c["relative_pct"],
        "Weighted points": c["weighted_points"], "Confidence": c.get("confidence"), "Status": c.get("status"),
        "Evidence page": c.get("evidence_page"), "Evidence": c.get("evidence"), "Justification": c.get("justification"),
    } for s in run["suppliers"] for c in s["criteria"]])


def excel_workbook(run):
    sheets = {
        "Leaderboard": leaderboard_table(run),
        "Scores": scores_table(run),
        "Criteria": pd.DataFrame(run["criteria"]),
        "Warnings": pd.DataFrame({"Warning": run["warnings"] or ["none"]}),
        "Reviews": pd.DataFrame(run["events"] or [{"event_type": "none"}]),
    }
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            for column in sheet.columns:
                width = max(len(str(cell.value or "")) for cell in column[:50])
                sheet.column_dimensions[column[0].column_letter].width = min(max(10, width + 2), 60)
    return buffer.getvalue()


def cell(value, style=SMALL):
    return Paragraph(escape(str(value or "")), style)


def pdf_table(rows, widths):
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0f4")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d4dc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def ranking_table(suppliers):
    rows = [["#", "Supplier", "Absolute", "PPI", "Submitted", "Exp.", "Reason"]]
    for s in suppliers:
        rows.append([s["final_rank"], cell(s["supplier_name"]), f"{s['absolute_score']:.2f}", f"{s['ppi']:.2f}",
                     s["submission_date"], f"{s['experience_rating']:g}", cell(s.get("rank_reason"))])
    return pdf_table(rows, [8 * mm, 34 * mm, 18 * mm, 16 * mm, 22 * mm, 10 * mm, PAGE_WIDTH - 108 * mm])


def criteria_table(supplier):
    rows = [["Criterion", "Weight", "Score", "Benchmark", "Relative %", "Evidence"]]
    for c in supplier["criteria"]:
        evidence = (c.get("evidence") or "-")[:220]
        if c.get("evidence_page"):
            evidence += f" (p.{c['evidence_page']})"
        rows.append([cell(c["name"]), f"{c['weight']:g}%", f"{c['score']:g}/{c['max_score']:g}", f"{c['benchmark']:g}",
                     f"{c['relative_pct']:.0f}", cell(evidence)])
    return pdf_table(rows, [34 * mm, 14 * mm, 16 * mm, 18 * mm, 18 * mm, PAGE_WIDTH - 100 * mm])


def overrides_table(overrides):
    rows = [["Supplier", "Criterion", "Old", "New", "Reason"]]
    for e in overrides:
        rows.append([cell(e["supplier_name"]), e["criterion_id"], f"{e['old_score']:g}", f"{e['new_score']:g}",
                     cell(e["reason"])])
    return pdf_table(rows, [36 * mm, 18 * mm, 12 * mm, 12 * mm, PAGE_WIDTH - 78 * mm])


def pdf_report(run, prepared_by="Procurement team"):
    winner = run["suppliers"][0]
    summary = (f"{run['title']}. Run {run['rfp_run_id']}, model {run['model']}, "
               f"prepared by {prepared_by} on {datetime.now():%d %b %Y}.")
    if run["locked"]:
        summary += " Decision locked."
    story = [
        Paragraph("RFP Evaluation Report", STYLES["Title"]),
        cell(summary, STYLES["BodyText"]),
        Spacer(1, 8),
        Paragraph(f"Recommended supplier: <b>{escape(winner['supplier_name'])}</b> with PPI {winner['ppi']:.2f} and "
                  f"absolute score {winner['absolute_score']:.2f}/100.", STYLES["BodyText"]),
        Spacer(1, 8),
        Paragraph("Ranking", STYLES["Heading2"]),
        ranking_table(run["suppliers"]),
        cell("Tie-break order: higher PPI, earlier submission, higher experience rating, supplier name."),
    ]
    for s in run["suppliers"]:
        story += [Paragraph(f"{s['final_rank']}. {escape(s['supplier_name'])}", STYLES["Heading3"]), criteria_table(s)]
        if s.get("risks"):
            story.append(cell("Risks: " + "; ".join(s["risks"][:4])))
    overrides = [e for e in run["events"] if e["event_type"] == "OVERRIDE"]
    if overrides:
        story += [Paragraph("Reviewer overrides", STYLES["Heading2"]), overrides_table(overrides)]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"RFP evaluation {run['rfp_run_id']}",
                            author=prepared_by, subject=run["title"], creator="RFP Evaluation")
    doc.build(story)
    return buffer.getvalue()
