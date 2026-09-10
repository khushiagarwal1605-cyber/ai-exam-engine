import streamlit as st
import json, os, re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pypdf import PdfReader
from openai import OpenAI
from fpdf import FPDF

import engine, os, re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pypdf import PdfReader
from openai import OpenAI
from fpdf import FPDF

import engine, os, re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pypdf import PdfReader
from openai import OpenAI
from fpdf import FPDF

import engine
from engine import *, re, base64
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from fpdf import FPDF

# ==========================================================
# 1. SCHEMA VALIDATION (Day 2)
# ==========================================================
REQUIRED_FIELDS = ["id", "type", "marks", "category", "question_text",
                   "full_model_answer", "source_reference", "grading_notes"]
MCQ_TYPES = ("mcq", "assertion_reason", "fill_in_the_blanks")
OPEN_TYPES = ("essay", "case_based", "creative_writing", "numerical")

def validate_exam(exam: Dict[str, Any]) -> List[str]:
    errors = []
    if not isinstance(exam, dict):
        return ["Top level must be a JSON object."]
    meta = exam.get("exam_metadata")
    if not isinstance(meta, dict):
        errors.append("Missing 'exam_metadata' object.")
    else:
        for f in ("source_document", "total_marks", "generated_at"):
            if f not in meta:
                errors.append("exam_metadata missing '%s'." % f)
    qs = exam.get("questions")
    if not isinstance(qs, list) or not qs:
        errors.append("'questions' must be a non-empty list.")
        return errors
    for i, q in enumerate(qs):
        tag = "questions[%d]" % i
        if not isinstance(q, dict):
            errors.append(tag + ": not an object.")
            continue
        for f in REQUIRED_FIELDS:
            if f not in q:
                errors.append("%s: missing '%s'." % (tag, f))
        t = q.get("type")
        if t in MCQ_TYPES:
            if not q.get("options"):
                errors.append("%s: type '%s' needs non-empty 'options'." % (tag, t))
            if not q.get("correct_answer"):
                errors.append("%s: type '%s' needs 'correct_answer'." % (tag, t))
        if t in OPEN_TYPES:
            if not (q.get("key_points") or q.get("expected_keywords")
                    or q.get("expected_structure")):
                errors.append("%s: open-ended type needs key_points/expected_keywords/"
                              "expected_structure." % tag)
        if "marks" in q:
            m = q["marks"]
            if not isinstance(m, int) or m <= 0:
                errors.append("%s: 'marks' must be a positive integer." % tag)
    return errors

# ==========================================================
# 2. GRADER + GAP ANALYSIS (Day 5)
# ==========================================================
def grade_answer(question: Dict[str, Any], user_answer: str,
                 rubric_text: Optional[str] = None) -> Dict[str, Any]:
    user = (user_answer or "").strip().lower()
    qtype = question.get("type")
    marks = question.get("marks", 0)
    model = question.get("full_model_answer", "")

    if not user:
        return {
            "awarded": 0, "max": marks, "verdict": "No answer provided",
            "feedback": "", "model_answer": model,
            "gap_analysis": None
        }

    if qtype in MCQ_TYPES:
        correct = str(question.get("correct_answer", "")).strip().lower()
        if user == correct or (correct and (correct in user or user in correct)):
            fb = "Exact match with the correct option."
            if question.get("grading_notes"):
                fb += " Grading note: " + str(question["grading_notes"])
            return {
                "awarded": marks, "max": marks, "verdict": "Correct",
                "feedback": fb, "model_answer": model,
                "gap_analysis": None
            }
        return {
            "awarded": 0, "max": marks, "verdict": "Incorrect",
            "feedback": "Expected: %s" % question.get("correct_answer"),
            "model_answer": model, "gap_analysis": None
        }

    pool = question.get("expected_keywords") or question.get("key_points") or question.get("expected_structure") or []
    hits = [k for k in pool if str(k).lower() in user]
    base_ratio = len(hits) / len(pool) if pool else 0.0
    base_awarded = int(round(base_ratio * marks))
    awarded = base_awarded

    missed = [k for k in pool if k not in hits]
    fb = "Matched %d/%d key points: %s." % (len(hits), len(pool), hits) if pool else "No rubric points defined."
    if missed:
        fb += " Missing: %s." % missed
    if question.get("grading_notes"):
        fb += " Grading note: " + str(question["grading_notes"])

    verdict = "Correct" if awarded == marks else ("Partially correct" if awarded > 0 else "Needs work")

    # GAP ANALYSIS
    gap = gap_analysis(question, user, model)

    return {
        "awarded": awarded, "max": marks, "verdict": verdict,
        "feedback": fb, "model_answer": model,
        "gap_analysis": gap
    }

def gap_analysis(question: Dict[str, Any], user_answer: str, model_answer: str) -> Dict[str, Any]:
    user = (user_answer or "").lower()
    pool = question.get("expected_keywords") or question.get("key_points") or question.get("expected_structure") or []
    missing = [k for k in pool if str(k).lower() not in user]
    model_words = (model_answer or "").lower().split()
    unique = list(dict.fromkeys([w for w in model_words if len(w) > 4]))
    not_used = [w for w in unique if w not in user][:8]
    return {
        "missingKeyPoints": missing,
        "vocabularyGaps": not_used,
        "wordCountUser": len((user_answer or "").split()),
        "wordCountModel": len((model_answer or "").split())
    }

# ==========================================================
# 3. SHELF (Day 3)
# ==========================================================
def init_state():
    ss = st.session_state
    if "shelves" not in ss:
        ss.shelves = {}
    if "current_shelf" not in ss:
        ss.current_shelf = None

def create_shelf(name: str, visibility: str = "private") -> Optional[str]:
    if not name or not name.strip():
        return None
    slug = name.strip().lower().replace(" ", "-").replace("_", "-")[:40]
    existing = list(st.session_state.shelves.keys())
    while slug in existing:
        slug = slug + "-1"
    st.session_state.shelves[slug] = {
        "slug": slug, "name": name.strip(), "visibility": visibility,
        "created_at": datetime.now(timezone.utc).isoformat(), "exams": []
    }
    return slug

def get_shelf(slug: str) -> Optional[Dict[str, Any]]:
    return st.session_state.shelves.get(slug)

def list_shelves(visibility: Optional[str] = None) -> List[Dict[str, Any]]:
    return [s for s in st.session_state.shelves.values()
            if visibility is None or s["visibility"] == visibility]

def save_exam_to_shelf(slug: str, exam: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errs = validate_exam(exam)
    if errs:
        return False, errs
    shelf = get_shelf(slug)
    if not shelf:
        return False, ["Shelf '%s' does not exist." % slug]
    shelf["exams"].append(exam)
    return True, []

# ==========================================================
# 4. SAMPLE EXAM (Placeholder)
# ==========================================================
def sample_exam(source: str, total_marks: int) -> Dict[str, Any]:
    src = source or "Untitled source"
    return {
        "exam_metadata": {
            "source_document": src,
            "total_marks": total_marks,
            "generated_at": datetime.now(timezone.utc).isoformat()
        },
        "questions": [
            {
                "id": "Q1", "type": "mcq", "marks": 1, "category": "competency_based",
                "difficulty": "HOTS",
                "question_text": "What is the primary function of the mitochondria?",
                "options": ["Energy production", "Protein synthesis", "Cell division", "Waste removal"],
                "correct_answer": "Energy production",
                "key_points": ["Mitochondria", "ATP", "Cellular respiration"],
                "full_model_answer": "The mitochondria is the powerhouse of the cell; it generates most of the cell's ATP through cellular respiration.",
                "source_reference": src + ", Chapter 3, Page 45",
                "grading_notes": "Accept 'Energy production' or 'ATP generation'."
            },
            {
                "id": "Q2", "type": "case_based", "marks": 4, "category": "comprehension_based",
                "context_text": "A farmer uses a new fertilizer. Yield rises 20%, but soil pH drops sharply after 6 months.",
                "question_text": "Analyze the long-term impact on soil health and suggest a remedial measure.",
                "expected_keywords": ["Soil acidity", "Liming", "Organic matter", "pH balance"],
                "full_model_answer": "The fertilizer caused soil acidification, leading to nutrient lockout and reduced microbial activity. Apply lime or organic compost to neutralise pH.",
                "source_reference": src + ", Case Study, Page 8",
                "grading_notes": "1 mark Acidification, 1 mark Nutrient Lockout, 1 mark Liming/Compost, 1 mark logical link."
            },
            {
                "id": "Q3", "type": "essay", "marks": 6, "category": "HOTS",
                "question_text": "Write an essay on 'The Future of AI in Education'.",
                "expected_structure": ["Introduction", "Benefits", "Challenges", "Conclusion"],
                "full_model_answer": "AI is a paradigm shift in education. Benefits: adaptive, personalised pacing. Challenges: data privacy and loss of human interaction. Conclusion: AI augments rather than replaces teachers.",
                "source_reference": src + ", Chapter 12",
                "grading_notes": "Structure 1, Content depth 2, Critical thinking 2, Language 1."
            }
        ]
    }

# ==========================================================
# 5. UI: DAY 6 FEATURES (Model Answer & Comparison)
# ==========================================================
def render_comparison(q: Dict[str, Any], user_answer: str, res: Dict[str, Any]):
    st.markdown("### 📊 Side-by-Side Comparison")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 🧑‍🎓 Your Answer")
        if user_answer:
            st.markdown(user_answer)
        else:
            st.markdown("*No answer provided.*")

        st.markdown("#### 📏 Score")
        st.metric("Score", "%s / %s" % (res["awarded"], res["max"]),
                  "Verdict: %s" % res["verdict"])

    with col2:
        st.markdown("#### 🤖 Model Answer (Ideal)")
        st.markdown(res["model_answer"])

        st.markdown("#### 🔍 Gap Analysis")
        ga = res.get("gap_analysis")
        if ga:
            st.markdown("**Missing Key Points:** " + ", ".join(ga["missingKeyPoints"]) if ga["missingKeyPoints"] else "✅ None")
            st.markdown("**Vocabulary Gaps:** " + ", ".join(ga["vocabularyGaps"]) if ga["vocabularyGaps"] else "✅ None")
            st.markdown("**Word Count:** You: %d | Model: %d" % (ga["wordCountUser"], ga["wordCountModel"]))

        st.markdown("#### 💡 Feedback")
        st.info(res["feedback"])

def main():
    st.set_page_config(page_title="AI Exam Engine", layout="wide", page_icon="📚")
    st.title("📚 AI Exam Engine — Day 10/11/12")
    st.markdown("*Trusted Source Engine (PDF + Qwen via Groq) | Stress Tested | PDF Export*")

    init_state()

    # Sidebar
    with st.sidebar:
        st.header("📁 Shelves")
        with st.form("new_shelf"):
            name = st.text_input("Shelf name", placeholder="Class 10 Physics")
            vis = st.selectbox("Visibility", ["private", "public"])
            if st.form_submit_button("Create shelf"):
                slug = create_shelf(name, vis)
                if slug:
                    st.session_state.current_shelf = slug
                    st.success("Created: " + slug)
                else:
                    st.error("Enter a valid name.")

        shelves = list_shelves()
        if shelves:
            labels = ["%s  (%s)" % (s["name"], s["visibility"]) for s in shelves]
            cur = st.session_state.current_shelf
            idx = 0
            if cur:
                slugs = [s["slug"] for s in shelves]
                idx = slugs.index(cur) if cur in slugs else 0
            sel = st.selectbox("Open shelf", labels, index=idx)
            st.session_state.current_shelf = shelves[labels.index(sel)]["slug"]

    slug = st.session_state.current_shelf
    shelf = get_shelf(slug) if slug else None

    if not shelf:
        st.warning("Create a shelf in the left sidebar to begin.")
        return

    st.subheader(shelf["name"])
    st.caption("Slug: `%s` | Visibility: **%s** | Exams: %d" % (
        shelf["slug"], shelf["visibility"], len(shelf["exams"])))

    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs(
        ["🎯 Generate Exam (PDF + Qwen)", "📝 Grade Answers", "🔬 Stress Test", "📥 Export & Share"]
    )

    with tab1:
        client = get_client()
        if not client:
            st.error("⚠️ **Groq API Key Missing**")
            st.markdown("""
            To generate exams from your PDFs, you need a Groq API Key.
            
            1. Go to [console.groq.com](https://console.groq.com).
            2. Create a free account (no card needed).
            3. Generate an API Key.
            
            **How to add it to Colab:**
            - In the left sidebar, click the **🔑 Secrets** icon.
            - Add a new secret named: `GROQ_API_KEY`.
            - Paste your key there.
            """)
            return

        st.markdown("### 📄 Upload Source Document (Trusted Source)")
        st.caption("Upload a PDF. The AI will ONLY use this document to create questions.")
        
        up = st.file_uploader("Upload PDF", type=["pdf"])
        
        if up is not None:
            if st.session_state.get("pdf_name") != up.name:
                with st.spinner("⏳ Reading PDF pages..."):
                    try:
                        text, pages = extract_pdf_text(up, max_pages=50)
                    except Exception as e:
                        st.error(f"Error reading PDF: {e}")
                        text, pages = "", 0
                
                st.session_state["pdf_text"] = text
                st.session_state["pdf_name"] = up.name
                st.session_state["pdf_pages"] = pages
                
                st.success(f"✅ Loaded **{up.name}** ({pages} pages).")
                with st.expander("👁️ Preview Extracted Text (First 2000 chars)"):
                    st.text(text[:2000])

        if "pdf_text" not in st.session_state:
            st.info("Please upload a PDF first to enable AI generation.")
        else:
            st.markdown("### ⚙️ Customization")
            c1, c2 = st.columns(2)
            with c1:
                types = st.multiselect(
                    "Question Types",
                    options=VALID_TYPES,
                    default=["mcq", "case_based", "essay"]
                )
            with c2:
                difficulty = st.selectbox(
                    "Difficulty",
                    options=["Easy", "Medium", "Hard"],
                    index=1
                )
            
            total_marks = st.slider("Total Marks", min_value=5, max_value=100, value=20)
            topic_focus = st.text_input("Topic Focus (Optional)", placeholder="e.g., Chapter 3: Photosynthesis")
            
            model_sel = st.selectbox(
                "Model",
                options=GROQ_MODELS,
                format_func=lambda x: x.replace("qwen/", "Qwen ").replace("groq/", "Groq ")
            )
            
            if st.button("🚀 Generate Exam with Qwen 3", type="primary", use_container_width=True):
                if not types:
                    st.warning("Select at least one question type.")
                    return
                
                with st.spinner(f"⚡ Generating exam using **{model_sel}** (this takes ~15-30s)..."):
                    exam, gen_notes = generate_exam_with_qwen(
                        client, 
                        model_sel, 
                        st.session_state["pdf_text"],
                        types, 
                        difficulty, 
                        total_marks, 
                        topic_focus,
                        st.session_state["pdf_name"]
                    )
                    
                    if gen_notes:
                        st.warning("⚠️ Generation Notes:")
                        for note in gen_notes:
                            st.write(f"• {note}")
                    
                    if exam:
                        ok, errs = save_exam_to_shelf(shelf["slug"], exam)
                        if ok:
                            st.success("✅ Exam saved to shelf!")
                            st.info(f"🔍 **Trusted Source Check Passed:** Questions are grounded in the uploaded PDF.")
                            st.json(exam)
                        else:
                            st.error("Validation failed: " + ", ".join(errs))
                    else:
                        st.error("❌ Failed to generate a valid exam. Try simplifying your topic or PDF.")

        st.markdown("---")
        st.markdown("### 📝 View Exams & Grade")
        if not shelf["exams"]:
            st.info("No exams in this shelf yet.")
        else:
            exam_sel = st.selectbox("Select Exam",
                                    ["Exam %d (%d marks)" % (i+1, e["exam_metadata"]["total_marks"])
                                     for i, e in enumerate(shelf["exams"])])
            
            if exam_sel:
                idx = int(exam_sel.split(" (")[0].split("Exam ")[1]) - 1
                exam = shelf["exams"][idx]
                
                for q in exam["questions"]:
                    st.markdown(f"#### {q['id']} | {q['type']} | {q['marks']} marks")
                    if q.get("context_text"):
                        st.markdown("> " + q["context_text"])
                    st.write(q.get("question_text"))
                    st.caption(f"📚 Source: {q.get('source_reference', 'N/A')}")
                    
                    user = st.text_area("Your answer", key="ans_" + q["id"], height=100)
                    
                    if st.button("Grade Answer", key="btn_" + q["id"], type="secondary"):
                        res = grade_answer(q, user)
                        st.metric("Score", f"{res['awarded']} / {res['max']}")
                        st.info(f"**Verdict:** {res['verdict']}")
                        st.write(res["feedback"])
                        
                        col1, col2 = st.columns([1, 1])
                        with col1:
                            st.markdown("#### 🧑‍🎓 Your Answer")
                            st.markdown(user if user else "*No answer provided.*")
                            if res["gap_analysis"]:
                                ga = res["gap_analysis"]
                                st.markdown("#### 🔍 Gap Analysis")
                                st.markdown(f"• Missing Key Points: {', '.join(ga['missingKeyPoints']) or 'None'}")
                                st.markdown(f"• Vocabulary Gaps: {', '.join(ga['vocabularyGaps']) or 'None'}")
                        
                        with col2:
                            st.markdown("#### 🤖 Model Answer (Ideal)")
                            st.markdown(res["model_answer"])
                            st.markdown(f"**Source:** {q.get('source_reference')}")

    with tab2:
        st.subheader("📝 Grade Answers (Same as above)")
        st.info("Go to the **Generate Exam** tab to create an exam, then grade it there.")

    with tab3:
        st.markdown("### 🔬 Stress Test Suite")
        client = get_client()
        if not client:
            st.error("Set GROQ_API_KEY first.")
            return

        tracker = QuotaTracker(st.session_state)
        st.metric("Groq daily quota remaining", tracker.remaining())
        if tracker.remaining() < 50:
            st.warning("Under 50 requests left today. The suite uses 2 per run.")

        if "pdf_text" not in st.session_state:
            st.info("Upload a PDF in the Generate tab first.")
        else:
            exam = shelf["exams"][-1] if shelf["exams"] else None
            if exam is None:
                st.info("Generate at least one exam first - we test real output.")
            else:
                model_sel = st.selectbox("Model for refusal probe", GROQ_MODELS)

                if st.button("🔬 Run full stress test suite", type="primary"):
                    results = run_suite(
                        client, model_sel, st.session_state["pdf_text"],
                        exam, st.session_state.get("pdf_name", "document"),
                        validate_exam)

                    passed = sum(1 for r in results if r["passed"])
                    st.subheader("Report: %d/%d passed" % (passed, len(results)))

                    for r in results:
                        icon = "✅" if r["passed"] else "❌"
                        st.markdown("%s **%s**" % (icon, r["name"]))
                        st.caption(r["detail"])

                    if passed == len(results):
                        st.success("Trusted-source engine verified. Safe to demo.")
                    else:
                        st.error("Failures above are the exact bugs to fix before release.")

                    report = {
                        "run_at": datetime.now(timezone.utc).isoformat(),
                        "document": st.session_state.get("pdf_name"),
                        "model": model_sel,
                        "passed": passed, "total": len(results),
                        "results": results}
                    st.download_button("📥 Download test report (JSON)",
                                       data=json.dumps(report, indent=2),
                                       file_name="stress_report.json",
                                       mime="application/json")

    with tab4:
        st.markdown("### 📥 Export & Share")
        st.markdown("Download your exam as a clean PDF or share the rubric.")
        
        if not shelf["exams"]:
            st.info("Generate an exam first to export.")
        else:
            exam_sel = st.selectbox("Select Exam",
                                    ["Exam %d (%d marks)" % (i+1, e["exam_metadata"]["total_marks"])
                                     for i, e in enumerate(shelf["exams"])])
            
            if exam_sel:
                idx = int(exam_sel.split(" (")[0].split("Exam ")[1]) - 1
                exam = shelf["exams"][idx]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Total Marks", exam["exam_metadata"]["total_marks"])
                    st.caption(f"Source: {exam['exam_metadata']['source_document']}")
                
                with col2:
                    st.metric("Questions", len(exam["questions"]))
                
                st.markdown("### 📄 Download Exam PDF")
                pdf_bytes = generate_exam_pdf(exam)
                st.download_button(
                    label="📥 Download Exam (PDF)",
                    data=pdf_bytes,
                    file_name="exam.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                
                st.markdown("### 📋 Download Rubric PDF")
                rubric_bytes = generate_rubric_pdf(exam)
                st.download_button(
                    label="📥 Download Rubric (PDF)",
                    data=rubric_bytes,
                    file_name="rubric.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                
                st.markdown("### 🔗 Share Link")
                st.info("This app uses `st.session_state`. Links only work within the same session. "
                        "For permanent sharing, use the PDF export above.")

if __name__ == "__main__":
    main()

def render_stress_tab(shelf):
    import tests
    client = get_client()
    if not client:
        st.error("Set GROQ_API_KEY first.")
        return

    tracker = tests.QuotaTracker(st.session_state)
    st.metric("Groq daily quota remaining", tracker.remaining())
    if tracker.low():
        st.warning("Under 50 requests left today. The suite uses 2 per run.")

    if "pdf_text" not in st.session_state:
        st.info("Upload a PDF in the Generate tab first.")
        return

    exam = shelf["exams"][-1] if shelf["exams"] else None
    if exam is None:
        st.info("Generate at least one exam first - we test real output, not samples.")
        return

    model_sel = st.selectbox("Model for refusal probe", GROQ_MODELS)

    if st.button("🔬 Run full stress test suite", type="primary"):
        results = tests.run_suite(
            client, model_sel, st.session_state["pdf_text"],
            exam, st.session_state.get("pdf_name", "document"),
            lambda q, a, r=None: grade_answer(q, a, r))

        passed = sum(1 for r in results if r["passed"])
        st.subheader("Report: %d/%d passed" % (passed, len(results)))

        for r in results:
            icon = "✅" if r["passed"] else "❌"
            st.markdown("%s **%s**" % (icon, r["name"]))
            st.caption(r["detail"])

        if passed == len(results):
            st.success("Trusted-source engine verified. Safe to demo.")
        else:
            st.error("Failures above are the exact bugs to fix before release.")

        report = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "document": st.session_state.get("pdf_name"),
            "model": model_sel,
            "passed": passed, "total": len(results),
            "results": results}
        st.download_button("📥 Download test report (JSON)",
                           data=json.dumps(report, indent=2),
                           file_name="stress_report.json",
                           mime="application/json")


# ==========================================================
# 9. PDF EXPORT (Day 12)
# ==========================================================
class ExamPDF(FPDF):
    def header(self):
        self.set_font("Arial", "B", 12)
        self.cell(0, 10, "AI Exam Engine", 0, 1, "L")
        self.set_font("Arial", "I", 8)
        self.cell(0, 10, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1, "R")
        self.ln(5)

    def footer(self):
        self.set_y(-20)
        self.set_font("Arial", "I", 8)
        self.cell(0, 10, f"Page {{self.page_no()}}/{{nb()}}", 0, 0, "C")

    def add_question(self, q: Dict[str, Any], total_marks: int):
        self.set_font("Arial", "B", 12)
        self.cell(0, 10, f"{q['id']} | {q['type']} | {q['marks']} mark(s)", 0, 1)
        self.set_font("Arial", "", 11)
        
        # Context
        if q.get("context_text"):
            self.set_font("Arial", "I", 10)
            self.multi_cell(0, 6, q["context_text"])
            self.ln(2)
        
        # Question
        qt = q.get("question_text")
        if isinstance(qt, dict):
            self.cell(0, 6, f"Assertion: {qt.get('assertion', '')}", 0, 1)
            self.cell(0, 6, f"Reason: {qt.get('reason', '')}", 0, 1)
        else:
            self.multi_cell(0, 6, str(qt))
        
        # Options
        if q.get("options"):
            self.ln(2)
            self.set_font("Arial", "", 11)
            for i, opt in enumerate(q["options"]):
                self.cell(0, 6, f"{chr(65+i)}. {opt}", 0, 1)
            self.ln(4)
        
        # Source Reference
        self.set_font("Arial", "I", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, f"Source: {q.get('source_reference', '')}", 0, 1)
        self.set_text_color(0, 0, 0)
        self.ln(8)

def generate_exam_pdf(exam: Dict[str, Any]) -> bytes:
    pdf = ExamPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Exam Paper", 0, 1, "C")
    pdf.set_font("Arial", "", 11)
    meta = exam.get("exam_metadata", {})
    pdf.multi_cell(0, 6, f"Source: {meta.get('source_document', '')}")
    pdf.multi_cell(0, 6, f"Total Marks: {meta.get('total_marks', 0)}")
    pdf.ln(10)
    
    for q in exam.get("questions", []):
        pdf.add_question(q, meta.get("total_marks", 0))
    
    return pdf.output(dest="S").encode("latin-1")


def generate_rubric_pdf(exam: Dict[str, Any]) -> bytes:
    pdf = ExamPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Grading Rubric", 0, 1, "C")
    pdf.ln(5)
    meta = exam.get("exam_metadata", {})
    pdf.multi_cell(0, 6, f"For: {meta.get('source_document', '')}")
    pdf.ln(5)
    
    for q in exam.get("questions", []):
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, f"{q['id']}: {q['type']}", 0, 1)
        pdf.set_font("Arial", "", 11)
        pdf.multi_cell(0, 6, f"Model Answer: {q.get('full_model_answer', '')}")
        pdf.ln(2)
        pdf.multi_cell(0, 6, f"Grading Notes: {q.get('grading_notes', '')}")
        if q.get("key_points"):
            pdf.multi_cell(0, 6, f"Key Points: {', '.join(q['key_points'])}")
        if q.get("expected_keywords"):
            pdf.multi_cell(0, 6, f"Expected Keywords: {', '.join(q['expected_keywords'])}")
        pdf.ln(8)
    
    return pdf.output(dest="S").encode("latin-1")


# ==========================================================
# 10. EXPORT & SHARE TAB (Day 12)
# ==========================================================
def render_export_tab(shelf):
    st.markdown("### 📥 Export & Share")
    st.markdown("Download your exam as a clean PDF or share the rubric.")
    
    if not shelf["exams"]:
        st.info("Generate an exam first to export.")
        return
    
    exam_sel = st.selectbox("Select Exam",
                            ["Exam %d (%d marks)" % (i+1, e["exam_metadata"]["total_marks"])
                             for i, e in enumerate(shelf["exams"])])
    
    if exam_sel:
        idx = int(exam_sel.split(" (")[0].split("Exam ")[1]) - 1
        exam = shelf["exams"][idx]
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Marks", exam["exam_metadata"]["total_marks"])
            st.caption(f"Source: {exam['exam_metadata']['source_document']}")
        
        with col2:
            st.metric("Questions", len(exam["questions"]))
        
        st.markdown("### 📄 Download Exam PDF")
        pdf_bytes = generate_exam_pdf(exam)
        st.download_button(
            label="📥 Download Exam (PDF)",
            data=pdf_bytes,
            file_name="exam.pdf",
            mime="application/pdf",
            use_container_width=True
        )
        
        st.markdown("### 📋 Download Rubric PDF")
        rubric_bytes = generate_rubric_pdf(exam)
        st.download_button(
            label="📥 Download Rubric (PDF)",
            data=rubric_bytes,
            file_name="rubric.pdf",
            mime="application/pdf",
            use_container_width=True
        )
        
        st.markdown("### 🔗 Share Link")
        st.info("This app uses `st.session_state`. Links only work within the same session. "
                "For permanent sharing, use the PDF export above.")
