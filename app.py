
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
