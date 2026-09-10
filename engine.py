"""Day 9-12 engine: Groq/Qwen, PDF reader, grounding, stress tests, PDF export.
Imported by app.py. Reuses your existing Day 6 helpers."""
import json, os, re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from pypdf import PdfReader
from openai import OpenAI
from fpdf import FPDF

GROQ_MODELS = ["qwen/qwen3-32b", "qwen/qwen3.6-27b", "qwen/qwen3.8-27b"]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
VALID_TYPES = ["mcq", "assertion_reason", "case_based", "numerical",
               "essay", "creative_writing", "fill_in_the_blanks"]


# ---------------- GROQ CLIENT ----------------
def get_groq_key() -> Optional[str]:
    try:
        k = st.secrets.get("GROQ_API_KEY")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("GROQ_API_KEY")


def get_client() -> Optional[OpenAI]:
    key = get_groq_key()
    return OpenAI(base_url=GROQ_BASE_URL, api_key=key) if key else None


# ---------------- PDF READER ----------------
def extract_pdf_text(uploaded_file, max_pages: int = 50) -> Tuple[str, int]:
    reader = PdfReader(uploaded_file)
    chunks, total = [], len(reader.pages)
    for i, page in enumerate(reader.pages[:max_pages]):
        txt = (page.extract_text() or "").strip()
        if txt:
            chunks.append("[PAGE %d]\n%s" % (i + 1, txt))
    return "\n\n".join(chunks), total


# ---------------- GROUNDING ----------------
STOP = {"the","a","an","of","and","or","to","in","is","are","was","for","on",
        "with","that","this","which","what","how","why","explain","question",
        "given","it","its","be","as","at","by","from","must","should","only",
        "using","based","text","document","source","answer","correct"}


def content_words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in STOP and len(w) > 3]


def overlap(text: str, source: str) -> float:
    words = set(content_words(text))
    if not words:
        return 1.0
    src = (source or "").lower()
    return sum(1 for w in words if w in src) / len(words)


def is_grounded(text: str, source: str, threshold: float = 0.35) -> bool:
    return overlap(text, source) >= threshold


def parse_json_block(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    s, e = raw.find("{"), raw.rfind("}")
    if s != -1 and e > s:
        try:
            return json.loads(raw[s:e + 1])
        except Exception:
            return None
    return None


# ---------------- GENERATOR ----------------
SCHEMA_HINT = """{
  "exam_metadata": {"source_document":"string","total_marks":0,"generated_at":"string"},
  "questions":[{"id":"Q1","type":"mcq","marks":1,"category":"competency_based",
  "difficulty":"HOTS","question_text":"string","options":["a","b","c","d"],
  "correct_answer":"string","key_points":["k1"],"full_model_answer":"string",
  "source_reference":"PAGE n, \\"exact quote\\"","grading_notes":"string"}]
}"""


def build_prompt(source_text, types, difficulty, total_marks, topic) -> str:
    return ("You are an exam paper setter. Use ONLY the SOURCE TEXT below. "
            "Never use outside knowledge. If the text cannot support a "
            "requested question type, produce fewer questions instead of "
            "inventing one.\n\nREQUIREMENTS\n"
            "- Allowed types: %s\n" % ", ".join(types) +
            "- Difficulty: %s\n" % difficulty +
            "- Target total marks: %d\n" % total_marks +
            "- Topic focus: %s\n" % (topic or "whole document") +
            "- Every question needs source_reference as 'PAGE n, \"exact quote\"'.\n"
            "- full_model_answer must be derivable only from the source text.\n\n"
            "Return ONLY valid JSON, no prose, no markdown fences, matching:\n%s\n\n"
            "SOURCE TEXT\n----------------\n%s\n----------------\n"
            % (SCHEMA_HINT, source_text[:24000]))


def generate_exam_with_qwen(client, model, source_text, types, difficulty,
                            total_marks, topic, doc_name):
    notes: List[str] = []
    QuotaTracker(st.session_state).record()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system",
                       "content": "You output strictly valid JSON only."},
                      {"role": "user",
                       "content": build_prompt(source_text, types, difficulty,
                                               total_marks, topic)}],
            temperature=0.3, max_tokens=4000)
        raw = resp.choices[0].message.content
    except Exception as e:
        return None, ["Groq call failed: %s" % e]

    exam = parse_json_block(raw)
    if not exam:
        return None, ["Model returned invalid JSON. Retry or lower total marks."]

    exam.setdefault("exam_metadata", {})
    exam["exam_metadata"]["source_document"] = doc_name
    exam["exam_metadata"]["generated_at"] = datetime.now(timezone.utc).isoformat()

    kept, dropped = [], []
    for q in exam.get("questions", []):
        probe = "%s %s" % (q.get("question_text", ""), q.get("full_model_answer", ""))
        (kept if is_grounded(probe, source_text) else dropped).append(q)
    if dropped:
        notes.append("Dropped ungrounded questions: %s"
                     % ", ".join(str(q.get("id", "?")) for q in dropped))
    exam["questions"] = kept
    exam["exam_metadata"]["total_marks"] = sum(
        int(q.get("marks", 0) or 0) for q in kept)

    if not kept:
        return None, ["Every question failed the trusted-source check. "
                      "Try a narrower topic or a different document."]
    return exam, notes


# ---------------- QUOTA ----------------
class QuotaTracker:
    RPD = 1000

    def __init__(self, state):
        self.s = state
        self._ensure()

    def _ensure(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if self.s.get("quota_day") != today:
            self.s["quota_day"] = today
            self.s["quota_used"] = 0

    def record(self, n: int = 1):
        self._ensure()
        self.s["quota_used"] = self.s.get("quota_used", 0) + n

    def remaining(self) -> int:
        self._ensure()
        return max(0, self.RPD - self.s.get("quota_used", 0))


# ---------------- STRESS TESTS ----------------
def page_map(source_text: str) -> Dict[int, str]:
    pages, cur, buf = {}, None, []
    for line in (source_text or "").splitlines():
        m = re.match(r"^\[PAGE (\d+)\]$", line.strip())
        if m:
            if cur is not None:
                pages[cur] = "\n".join(buf)
            cur, buf = int(m.group(1)), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        pages[cur] = "\n".join(buf)
    return pages


def parse_quote(ref: str) -> Tuple[Optional[int], str]:
    m = re.search(r"page\s*(\d+)", (ref or ""), re.I)
    quotes = re.findall(r'"([^"]{6,})"', ref or "")
    return (int(m.group(1)) if m else None), (quotes[0] if quotes else "")


def test_refusal(client, model, doc_name):
    probe = ("You may ONLY answer from the SOURCE about '%s'. It says nothing "
             "about the following. Reply exactly REFUSE if the source cannot "
             "answer.\n\nQUESTION: According to this document, who was the "
             "first elected president of Antarctica?" % doc_name)
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": "You are a strict exam setter."},
                      {"role": "user", "content": probe}],
            temperature=0, max_tokens=200)
        out = (r.choices[0].message.content or "").strip()
    except Exception as e:
        return {"name": "Hallucination refusal", "passed": False,
                "detail": "API error: %s" % e}
    low = out.lower()
    ok = any(k in low for k in ("refuse", "cannot", "not mentioned",
                                "not covered", "does not"))
    return {"name": "Hallucination refusal", "passed": ok,
            "detail": ("Declined correctly: %r" % out[:150]) if ok
                      else ("INVENTED AN ANSWER: %r" % out[:200])}


def test_citations(exam, source_text):
    pages = page_map(source_text)
    qs = exam.get("questions", [])
    if not qs:
        return {"name": "Citation integrity", "passed": False,
                "detail": "No questions to check."}
    bad = []
    for q in qs:
        page, quote = parse_quote(str(q.get("source_reference", "")))
        probs = []
        if page is None:
            probs.append("no page number")
        elif page not in pages:
            probs.append("page %s not in document" % page)
        if not quote:
            probs.append("no quoted text")
        elif quote.lower() not in pages.get(page or 0, source_text).lower():
            probs.append("quote not found on cited page")
        if probs:
            bad.append("%s: %s" % (q.get("id", "?"), ", ".join(probs)))
    return {"name": "Citation integrity", "passed": not bad,
            "detail": "All %d questions cite real pages/quotes." % len(qs)
            if not bad else "Issues in %d/%d: %s" % (len(bad), len(qs),
                                                      "; ".join(bad[:5]))}


def test_grounding(exam, source_text, threshold=0.35):
    worst, rows = 1.0, []
    for q in exam.get("questions", []):
        score = overlap("%s %s" % (q.get("question_text", ""),
                                   q.get("full_model_answer", "")), source_text)
        worst = min(worst, score)
        if score < threshold:
            rows.append("%s (%.0f%%)" % (q.get("id", "?"), score * 100))
    return {"name": "Grounding score", "passed": not rows,
            "detail": "Lowest overlap %.0f%% (threshold %.0f%%)."
                      % (worst * 100, threshold * 100) if not rows
                      else "Weakly grounded: %s" % ", ".join(rows)}


def run_suite(client, model, source_text, exam, doc_name, validate_fn):
    errs = validate_fn(exam)
    return [
        {"name": "Schema conformance", "passed": not errs,
         "detail": "Output matches the Day 2 JSON schema."
                   if not errs else "; ".join(errs[:6])},
        test_grounding(exam, source_text),
        test_citations(exam, source_text),
        test_refusal(client, model, doc_name),
    ]


# ---------------- PDF EXPORT ----------------
class ExamPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 10, "AI Exam Engine", 0, 1, "L")
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, "Generated: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
                  0, 1, "R")
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, "Page %d/{nb}" % self.page_no(), 0, 0, "C")

    def add_question(self, q):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 9, "%s | %s | %s mark(s)" % (q.get("id"), q.get("type"),
                                                  q.get("marks")), 0, 1)
        if q.get("context_text"):
            self.set_font("Helvetica", "I", 10)
            self.multi_cell(0, 5.5, q["context_text"])
            self.ln(1)
        qt = q.get("question_text")
        self.set_font("Helvetica", "", 11)
        if isinstance(qt, dict):
            self.multi_cell(0, 5.5, "Assertion: %s" % qt.get("assertion", ""))
            self.multi_cell(0, 5.5, "Reason: %s" % qt.get("reason", ""))
        else:
            self.multi_cell(0, 5.5, str(qt))
        if q.get("options"):
            self.ln(1)
            for i, opt in enumerate(q["options"]):
                self.multi_cell(0, 5.5, "%s. %s" % (chr(65 + i), opt))
        self.ln(2)
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(110, 110, 110)
        self.multi_cell(0, 5, "Source: %s" % q.get("source_reference", ""))
        self.set_text_color(0, 0, 0)
        self.ln(6)


def generate_exam_pdf(exam) -> bytes:
    pdf = ExamPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Exam Paper", 0, 1, "C")
    meta = exam.get("exam_metadata", {})
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, "Source: %s" % meta.get("source_document", ""))
    pdf.multi_cell(0, 6, "Total Marks: %s" % meta.get("total_marks", 0))
    pdf.ln(6)
    for q in exam.get("questions", []):
        pdf.add_question(q)
    return bytes(pdf.output())


def generate_rubric_pdf(exam) -> bytes:
    pdf = ExamPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Grading Rubric", 0, 1, "C")
    meta = exam.get("exam_metadata", {})
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, "For: %s" % meta.get("source_document", ""))
    pdf.ln(4)
    for q in exam.get("questions", []):
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 9, "%s: %s" % (q.get("id"), q.get("type")), 0, 1)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, "Model Answer: %s" % q.get("full_model_answer", ""))
        pdf.ln(1)
        pdf.multi_cell(0, 6, "Grading Notes: %s" % q.get("grading_notes", ""))
        if q.get("key_points"):
            pdf.multi_cell(0, 6, "Key Points: %s" % ", ".join(q["key_points"]))
        if q.get("expected_keywords"):
            pdf.multi_cell(0, 6, "Expected Keywords: %s" % ", ".join(q["expected_keywords"]))
        pdf.ln(6)
    return bytes(pdf.output())
