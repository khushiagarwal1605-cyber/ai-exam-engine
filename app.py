import streamlit as st
import json, re, os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from PyPDF2 import PdfReader
from huggingface_hub import InferenceClient

# --- CONFIG ---
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN")
MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"

if not HF_TOKEN:
    st.error("⚠️ Hugging Face Token not found. Please set it in Colab.")
    st.stop()

client = InferenceClient(token=HF_TOKEN)

# --- PDF READER ---
def extract_text_from_pdf(uploaded_file) -> str:
    try:
        reader = PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t: text += t + "\n\n"
        return text.strip()
    except Exception as e:
        st.error(f"PDF Error: {e}")
        return ""

# --- QWEN GENERATOR ---
def generate_exam(prompt: str) -> Optional[Dict]:
    try:
        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.7,
        )
        raw = response.choices[0].message.content
        # Clean JSON
        raw = re.sub(r"```json", "", raw)
        raw = re.sub(r"```", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        st.error(f"Qwen Error: {e}")
        return None

# --- SCHEMA & GRADER (Simplified for Speed) ---
def validate_exam(exam):
    if not isinstance(exam, dict) or "questions" not in exam:
        return ["Invalid JSON structure"]
    return []

def grade_answer(question, user_answer):
    user = (user_answer or "").lower().strip()
    qtype = question.get("type")
    marks = question.get("marks", 1)
    
    if qtype == "mcq":
        correct = question.get("correct_answer", "").lower()
        if correct in user: return {"score": marks, "msg": "Correct!", "model": question.get("full_model_answer")}
        return {"score": 0, "msg": f"Incorrect. Correct: {question.get('correct_answer')}", "model": question.get("full_model_answer")}
    
    # Simple keyword check for essays
    pool = question.get("key_points", [])
    hits = sum(1 for k in pool if k.lower() in user)
    awarded = int((hits / len(pool)) * marks) if pool else 0
    return {"score": awarded, "msg": f"Matched {hits}/{len(pool)} points", "model": question.get("full_model_answer")}

# --- UI ---
st.set_page_config(page_title="Euria AI Exam", layout="wide")
st.title("📚 Euria AI Exam Engine (Day 10)")

# Sidebar
st.sidebar.header("Shelves")
shelf_name = st.sidebar.text_input("Create Shelf", placeholder="Class 10 Physics")
if st.sidebar.button("Create"):
    st.session_state.shelf = shelf_name
    st.success(f"Created: {shelf_name}")

shelf = st.session_state.get("shelf", "Default Shelf")
st.subheader(f"Current Shelf: {shelf}")

# Tabs
tab1, tab2 = st.tabs(["📄 Upload PDF & Generate", "📝 Grade Answers"])

with tab1:
    st.markdown("### 1. Upload Source Document")
    uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])
    
    if uploaded_file:
        st.success("✅ PDF Uploaded")
        source_text = extract_text_from_pdf(uploaded_file)
        st.write(f"Extracted {len(source_text)} characters.")
        
        st.markdown("### 2. Customize Exam")
        q_types = st.multiselect("Question Types", ["MCQ", "Essay", "Case Study"])
        total_marks = st.slider("Total Marks", 10, 100, 50)
        
        if st.button("🚀 Generate with Qwen (Day 10)"):
            if not q_types:
                st.error("Select at least one question type.")
            else:
                prompt = f"""
                Generate a JSON exam from the text below.
                Types: {', '.join(q_types)}
                Total Marks: {total_marks}
                Schema: {{ "exam_metadata": {{ "source": "PDF", "total_marks": {total_marks}, "generated_at": "string" }}, "questions": [ {{ "id": "Q1", "type": "mcq", "marks": 5, "question_text": "...", "options": ["A","B","C","D"], "correct_answer": "A", "key_points": ["point1"], "full_model_answer": "...", "source_reference": "Page 1", "grading_notes": "..." }} ] }}
                Text: {source_text[:10000]}
                """
                with st.spinner("Calling Qwen 2.5-7B..."):
                    exam_data = generate_exam(prompt)
                
                if exam_data:
                    st.success("✅ Exam Generated!")
                    st.json(exam_data)
                    # Save to session (simulating DB)
                    st.session_state.current_exam = exam_data
                else:
                    st.error("Failed to generate. Try a smaller PDF.")

with tab2:
    if "current_exam" in st.session_state:
        exam = st.session_state.current_exam
        st.write(f"Grading: {exam['exam_metadata']['source']}")
        
        for q in exam["questions"]:
            st.markdown(f"#### {q['id']} ({q['type']})")
            st.write(q["question_text"])
            
            user_ans = st.text_area("Your Answer", key=q["id"])
            if st.button("Grade", key=q["id"]):
                res = grade_answer(q, user_ans)
                st.metric("Score", f"{res['score']} / {q['marks']}")
                st.info(res["msg"])
                st.success(f"**Model Answer:** {res['model']}")
    else:
        st.info("Generate an exam first in Tab 1.")
