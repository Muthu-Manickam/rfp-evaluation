import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field

MAX_FILE_MB = 20


class CriterionResult(BaseModel):
    criterion_id: int
    name: str
    weight: float
    max_score: float = Field(gt=0)
    score: float = Field(ge=0)
    status: str = "OK"
    confidence: float | None = Field(default=None, ge=0, le=1)
    justification: str = ""
    evidence: str = ""
    evidence_page: int | None = None
    grounded: bool | None = None
    strengths: list[str] = []
    weaknesses: list[str] = []
    missing_information: list[str] = []


class Scorecard(BaseModel):
    supplier_name: str
    criteria: list[CriterionResult]
    risks: list[str] = []
    overall_summary: str = ""
    warnings: list[str] = []


@dataclass
class Proposal:
    supplier_name: str
    submission_date: str
    experience_rating: float
    file_name: str
    data: bytes


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def to_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(value) else float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def text_list(value):
    items = value if isinstance(value, list) else [value] if value else []
    return [str(item).strip() for item in items if str(item).strip()][:5]


def check_score(item, criterion, supplier, warnings):
    max_score = float(criterion["max_score"])
    raw = item.get("score")
    score = to_number(raw)
    if score is None:
        warnings.append(f"{supplier}: '{criterion['name']}' has no usable score ({raw!r}), scored 0.")
        return 0.0, "INVALID"
    status = "OK" if isinstance(raw, (int, float)) else "COERCED"
    if score < 0 or score > max_score:
        warnings.append(f"{supplier}: '{criterion['name']}' score {score:g} is outside 0 to {max_score:g}, clipped.")
        return min(max(score, 0.0), max_score), "CLIPPED"
    return score, status


def check_confidence(value):
    confidence = to_number(value)
    if confidence is None:
        return None
    if confidence > 1:
        confidence = confidence / 100
    return round(min(max(confidence, 0.0), 1.0), 2)


def read_reply(reply, supplier, warnings):
    if isinstance(reply, dict):
        return reply
    try:
        return parse_json(reply)
    except ValueError as error:
        warnings.append(f"{supplier}: the reply was not valid JSON ({error}), every criterion scored 0.")
        return None


def answers_by_criterion(reply, criteria, supplier, warnings):
    known = {c["criterion_id"] for c in criteria}
    answers = {}
    for item in reply.get("criteria") or []:
        number = to_number(item.get("criterion_id")) if isinstance(item, dict) else None
        if number is None or int(number) not in known:
            warnings.append(f"{supplier}: dropped a result for an unknown criterion.")
            continue
        answers.setdefault(int(number), item)
    return answers


def missing_result(criterion, status):
    return CriterionResult(criterion_id=criterion["criterion_id"], name=criterion["name"], weight=criterion["weight"],
                           max_score=criterion["max_score"], score=0, status=status, confidence=0,
                           justification="No result returned for this criterion.")


def checked_result(item, criterion, supplier, warnings):
    score, status = check_score(item, criterion, supplier, warnings)
    page = to_number(item.get("evidence_page"))
    return CriterionResult(
        criterion_id=criterion["criterion_id"], name=criterion["name"], weight=criterion["weight"],
        max_score=criterion["max_score"], score=round(score, 4), status=status,
        confidence=check_confidence(item.get("confidence")), justification=str(item.get("justification") or ""),
        evidence=str(item.get("evidence") or ""), evidence_page=int(page) if page else None,
        strengths=text_list(item.get("strengths")), weaknesses=text_list(item.get("weaknesses")),
        missing_information=text_list(item.get("missing_information")))


def clean_reply(reply, criteria, supplier):
    warnings = []
    reply = read_reply(reply, supplier, warnings)
    if reply is None:
        results = [missing_result(c, "INVALID") for c in criteria]
        return Scorecard(supplier_name=supplier, criteria=results, warnings=warnings)

    answers = answers_by_criterion(reply, criteria, supplier, warnings)
    results = []
    for c in criteria:
        item = answers.get(c["criterion_id"])
        if item is None:
            warnings.append(f"{supplier}: '{c['name']}' is missing from the reply, scored 0.")
            results.append(missing_result(c, "MISSING"))
        else:
            results.append(checked_result(item, c, supplier, warnings))
    return Scorecard(supplier_name=supplier, criteria=results, risks=text_list(reply.get("risks")),
                     overall_summary=str(reply.get("overall_summary") or ""), warnings=warnings)


def is_valid_rating(value):
    try:
        return 1 <= float(value) <= 5
    except (TypeError, ValueError):
        return False


def is_past_date(value):
    try:
        return date.fromisoformat(str(value)) <= date.today()
    except ValueError:
        return False


def check_proposal(p, names, files):
    problems = []
    name = (p.supplier_name or "").strip()
    if not name:
        problems.append(f"{p.file_name}: supplier name is required.")
    elif name.casefold() in names:
        problems.append(f"{p.file_name}: supplier name '{name}' is used more than once.")
    names.add(name.casefold())
    if not is_past_date(p.submission_date):
        problems.append(f"{p.file_name}: submission date must be a valid date that is not in the future.")
    if not is_valid_rating(p.experience_rating):
        problems.append(f"{p.file_name}: experience rating must be between 1 and 5.")
    if not p.file_name.lower().endswith(".pdf"):
        problems.append(f"{p.file_name}: only PDF files are accepted.")
    elif not p.data or len(p.data) > MAX_FILE_MB * 1024 * 1024:
        problems.append(f"{p.file_name}: the file is empty or larger than {MAX_FILE_MB} MB.")
    else:
        digest = hashlib.sha256(p.data).hexdigest()
        if digest in files:
            problems.append(f"{p.file_name}: same file as {files[digest]}.")
        files.setdefault(digest, p.file_name)
    return problems


def check_inputs(proposals):
    problems, names, files = [], set(), {}
    if len(proposals) < 2:
        problems.append("Upload at least two proposals so the suppliers can be compared.")
    for p in proposals:
        problems += check_proposal(p, names, files)
    return problems


def criterion_problems(c):
    problems = []
    if float(c.get("max_score") or 0) <= 0:
        problems.append(f"{c.get('name')}: max score must be above 0.")
    if not 0 <= float(c.get("weight") or 0) <= 100:
        problems.append(f"{c.get('name')}: weight must be between 0 and 100.")
    return problems


def criteria_problems(criteria):
    problems = []
    active = [c for c in criteria if c.get("is_active")]
    names = [str(c.get("name") or "").strip().lower() for c in criteria]
    if not active:
        problems.append("At least one criterion must be active.")
    if not all(names):
        problems.append("Every criterion needs a name.")
    if len(set(names)) != len(names):
        problems.append("Criterion names must be unique.")
    for c in criteria:
        problems += criterion_problems(c)
    total = sum(float(c.get("weight") or 0) for c in active)
    if active and abs(total - 100) > 1e-6:
        problems.append(f"Active weights add up to {total:g}%, they must add up to 100%.")
    return problems
