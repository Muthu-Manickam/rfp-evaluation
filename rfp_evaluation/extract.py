import io
import re
from difflib import SequenceMatcher

MIN_TEXT = 200


def tidy(text):
    text = re.sub(r"[ \t]+", " ", text or "")
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def read_with_pymupdf(data):
    import pymupdf

    pages = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            blocks = [block[4] for block in page.get_text("blocks", sort=True) if block[6] == 0]
            pages.append(tidy("\n\n".join(blocks)))
    return pages


def read_with_pypdf(data):
    from pypdf import PdfReader

    return [tidy(page.extract_text()) for page in PdfReader(io.BytesIO(data)).pages]


def read_pdf(data):
    for reader in (read_with_pymupdf, read_with_pypdf):
        try:
            pages = reader(data)
        except Exception:
            continue
        if sum(len(page) for page in pages) >= MIN_TEXT:
            return pages, None
    return [], "no readable text (scanned, empty or damaged PDF)"


def simplify(text):
    return re.sub(r"[^a-z0-9%$.]+", " ", (text or "").lower()).strip()


def find_quote(pages, quote):
    wanted = simplify(quote)
    if len(wanted) < 12:
        return False, None
    best_page, best_share = None, 0.0
    for number, page in enumerate(pages, start=1):
        text = simplify(page)
        if wanted in text:
            return True, number
        match = SequenceMatcher(None, text, wanted, autojunk=False).find_longest_match(0, len(text), 0, len(wanted))
        share = match.size / len(wanted)
        if share > best_share:
            best_page, best_share = number, share
    return best_share >= 0.8, best_page
