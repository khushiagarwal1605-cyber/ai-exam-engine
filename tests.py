"""Day 11 - Stress test harness for the AI Exam Engine."""
import json, re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


# ==========================================================
# QUOTA TRACKER (Groq does not expose daily usage in headers,
# so we count requests ourselves and reset at midnight UTC) [1]
# ==========================================================
class QuotaTracker:
    RPD_LIMIT = 1000
    RPM_LIMIT = 30

    def __init__(self, state: Dict[str, Any]):
        self.state = state
        self._ensure()

    def _ensure(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if self.state.get("quota_day") != today:
            self.state["quota_day"] = today
            self.state["quota_used"] = 0

    def record(self, n: int = 1):
        self._ensure()
        self.state["quota_used"] = self.state.get("quota_used", 0) + n

    def remaining(self) -> int:
        self._ensure()
        return max(0, self.RPD_LIMIT - self.state.get("quota_used", 0))

    def low(self) -> bool:
        return self.remaining() < 50


# ==========================================================
# TEXT UTILITIES
# ==========================================================
STOP = {"the","a","an","of","and","or","to","in","is","are","was","for","on",
        "with","that","this","which","what","how","why","explain","question",
        "given","it","its","be","as","at","by","from","must","should","only",
        "using","based","text","document","source","answer","correct"}


def content_words(text: str) -> List[str]:
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in toks if w not in STOP and len(w) > 3]


def overlap(text: str, source: str) -> float:
    words = set(content_words(text))
    if not words:
        return 1.0
    src = (source or "").lower()
    hits = sum(1 for w in words if w in src)
    return hits / len(words)


def page_map(source_text: str) -> Dict[int, str]:
    """Split tagged source text back into {page_number: text}."""
    pages: Dict[int, str] = {}
    current, buf = None, []
    for line in (source_text or "").splitlines():
        m = re.match(r"^\[PAGE (\d+)\]$", line.strip())
        if m:
            if current is not None:
                pages[current] = "\n".join(buf)
            current, buf = int(m.group(1)), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        pages[current] = "\n".join(buf)
    return pages


def parse_quote(ref: str) -> Tuple[Dict[str, Any], str]:
    """Extract page number + quoted text from a source_reference string."""
    page = None
    m = re.search(r"page\s*(\d+)", (ref or ""), re.I)
    if m:
        page = int(m.group(1))
    quotes = re.findall(r'"([^"]{6,})"', ref or "")
    quote = quotes[0] if quotes else ""
    return {"page": page, "quote": quote}


# ==========================================================
# THE FOUR TESTS
# ==========================================================
def test_refusal(client, model: str, doc_name: str) -> Dict[str, Any]:
    """The model must refuse to answer what is NOT in the document."""
    probe = (
        "You may ONLY answer using the SOURCE TEXT provided, which is about "
        "'%s'. The source text says nothing about the following. "
        "Reply with exactly REFUSE if the source cannot answer, otherwise "
        "answer from the source.\n\n"
        "QUESTION: According to this document, what is the capital city of "
        "Antarctica and who was its first elected president?\n" % doc_name
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": "You are a strict exam setter."},
                      {"role": "user", "content": probe}],
            temperature=0, max_tokens=200)
        out = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return {"name": "Hallucination refusal", "passed": False,
                "detail": "API error: %s" % e}
    low = out.lower()
    passed = ("refuse" in low or "cannot" in low or "not mentioned" in low
              or "not covered" in low or "does not" in low)
    return {"name": "Hallucination refusal", "passed": passed,
            "detail": ("Correctly declined. Response: %r" % out[:160]) if passed
                      else ("INVENTED AN ANSWER: %r" % out[:200])}


def test_citations(exam: Dict[str, Any], source_text: str) -> Dict[str, Any]:
    """Every source_reference must point at a real page with a real quote."""
    pages = page_map(source_text)
    questions = exam.get("questions", [])
    if not questions:
        return {"name": "Citation integrity", "passed": False,
                "detail": "No questions to check."}
    bad, checked = [], 0
    for q in questions:
        ref = str(q.get("source_reference", ""))
        info = parse_quote(ref)
        checked += 1
        problems = []
        if info["page"] is None:
            problems.append("no page number")
        elif info["page"] not in pages:
            problems.append("page %s not in document" % info["page"])
        if not info["quote"]:
            problems.append("no quoted text")
        else:
            hay = pages.get(info["page"] or 0, source_text).lower()
            if info["quote"].lower() not in hay:
                problems.append("quote not found on cited page")
        if problems:
            bad.append("%s: %s" % (q.get("id", "?"), ", ".join(problems)))
    passed = len(bad) == 0
    return {"name": "Citation integrity", "passed": passed,
            "detail": ("All %d questions cite real pages/quotes." % checked)
                      if passed else ("Issues in %d/%d: %s" % (
                          len(bad), checked, "; ".join(bad[:5])))}


def test_grounding(exam: Dict[str, Any], source_text: str,
                   threshold: float = 0.35) -> Dict[str, Any]:
    """Question + model answer must be built from the document's vocabulary."""
    rows, worst = [], 1.0
    for q in exam.get("questions", []):
        probe = "%s %s" % (q.get("question_text", ""), q.get("full_model_answer", ""))
        score = overlap(probe, source_text)
        worst = min(worst, score)
        if score < threshold:
            rows.append("%s (%.0f%%)" % (q.get("id", "?"), score * 100))
    passed = not rows
    return {"name": "Grounding score", "passed": passed,
            "detail": ("Lowest overlap %.0f%%, all above %.0f%% threshold."
                       % (worst * 100, threshold * 100)) if passed
                      else ("Weakly grounded: %s" % ", ".join(rows))}


def test_schema(exam: Dict[str, Any]) -> Dict[str, Any]:
    """Reuse the Day 2 validator."""
    import app
    errs = app.validate_exam(exam)
    return {"name": "Schema conformance", "passed": not errs,
            "detail": "Output matches the Day 2 JSON schema." if not errs
                      else "; ".join(errs[:6])}


def test_rubric_sensitivity(grade_fn, exam: Dict[str, Any]) -> Dict[str, Any]:
    """Same answer under two different rubrics should not score identically."""
    target = None
    for q in exam.get("questions", []):
        if q.get("type") not in ("mcq", "assertion_reason", "fill_in_the_blanks"):
            target = q
            break
    if target is None:
        return {"name": "Rubric sensitivity", "passed": True,
                "detail": "Skipped (no open-ended question in this exam)."}
    pool = (target.get("expected_keywords") or target.get("key_points")
            or target.get("expected_structure") or [])
    answer = ("This answer mentions %s only, and nothing else."
              % (pool[0] if pool else "something"))
    strict = grade_fn(target, answer, "Depth: 4\nPrecision: 4\nEvidence: 4")
    lenient = grade_fn(target, answer, "Relevance: 1")
    passed = strict["awarded"] != lenient["awarded"]
    return {"name": "Rubric sensitivity", "passed": passed,
            "detail": ("Strict rubric scored %s/%s, lenient scored %s/%s."
                       % (strict["awarded"], strict["max"],
                          lenient["awarded"], lenient["max"]))
                      if passed else
                      ("Both rubrics returned %s/%s - rubric is being ignored."
                       % (strict["awarded"], strict["max"]))}


def run_suite(client, model: str, source_text: str, exam: Dict[str, Any],
              doc_name: str, grade_fn) -> List[Dict[str, Any]]:
    results = [
        test_schema(exam),
        test_grounding(exam, source_text),
        test_citations(exam, source_text),
        test_rubric_sensitivity(grade_fn, exam),
        test_refusal(client, model, doc_name),
    ]
    return results
