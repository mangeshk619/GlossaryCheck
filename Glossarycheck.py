import streamlit as st
import pandas as pd
import docx
from docx.enum.text import WD_COLOR_INDEX
from io import BytesIO
import re

# ---------- Helpers ----------
def extract_text_from_docx(docx_file):
    doc = docx.Document(docx_file)
    text = []
    for para in doc.paragraphs:
        text.append(para.text)
    return "\n".join(text)

def highlight_docx_terms(docx_file, terms):
    """Highlight all target terms in a Word file."""
    doc = docx.Document(docx_file)
    for para in doc.paragraphs:
        for term in terms:
            if not isinstance(term, str) or not term.strip():
                continue
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            for run in para.runs:
                if pattern.search(run.text):
                    run.font.highlight_color = WD_COLOR_INDEX.YELLOW
                    run.bold = True
    return doc

def highlight_missing_source_terms(source_file, glossary_df, target_text):
    """
    Highlight only those source terms whose corresponding target terms are missing
    in the target Word file.
    """
    doc = docx.Document(source_file)
    for para in doc.paragraphs:
        para_text = para.text
        if not para_text.strip():
            continue
        new_runs = []
        idx = 0
        while idx < len(para_text):
            highlight_run = None
            for _, row in glossary_df.iterrows():
                source_term = str(row["Source Term"])
                target_term = str(row["Target Term"])
                # Only highlight if target term is missing
                target_present = bool(re.search(re.escape(target_term), target_text, flags=re.IGNORECASE))
                if not target_present:
                    pattern = re.compile(re.escape(source_term), re.IGNORECASE)
                    match = pattern.match(para_text, idx)
                    if match:
                        highlight_run = match
                        break
            if highlight_run:
                run = para.add_run(para_text[highlight_run.start():highlight_run.end()])
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
                run.bold = True
                idx = highlight_run.end()
            else:
                run = para.add_run(para_text[idx])
                idx += 1
            new_runs.append(run)
        # Remove old runs
        for run in para.runs[:]:
            para._element.remove(run._element)
        # Add rebuilt runs
        for run in new_runs:
            para._element.append(run._element)
    return doc

# ---------- Streamlit UI ----------
st.title("Glossary Adherence Checker")

# Upload files
glossary_file = st.file_uploader("Upload Glossary (Excel)", type=["xlsx"])
source_file = st.file_uploader("Upload Source File (Word)", type=["docx"])
target_file = st.file_uploader("Upload Translated File (Word)", type=["docx"])

if glossary_file and source_file and target_file:
    # Load glossary
    glossary_df = pd.read_excel(glossary_file)
    
    if "Source Term" not in glossary_df.columns or "Target Term" not in glossary_df.columns:
        st.error("Glossary must contain 'Source Term' and 'Target Term' columns")
    else:
        # Extract texts
        source_text = extract_text_from_docx(source_file)
        target_text = extract_text_from_docx(target_file)
        
        # Check adherence
        results = []
        present_count = 0
        missing_count = 0

        for _, row in glossary_df.iterrows():
            source = str(row["Source Term"])
            target = str(row["Target Term"])
            
            source_present = bool(re.search(re.escape(source), source_text, flags=re.IGNORECASE))
            target_present = bool(re.search(re.escape(target), target_text, flags=re.IGNORECASE))
            
            if target_present:
                present_count += 1
            else:
                missing_count += 1
            
            results.append({
                "Source Term": source,
                "Target Term": target,
                "Source Present": "Yes" if source_present else "No",
                "Target Present": "Yes" if target_present else "No",
                "Status": "✅ Present" if target_present else "❌ Missing"
            })
        
        results_df = pd.DataFrame(results)

        # Summary
        st.subheader("Summary")
        st.write(f"**Total Terms Checked:** {len(glossary_df)}")
        st.write(f"✅ **Present in Target:** {present_count}")
        st.write(f"❌ **Missing in Target:** {missing_count}")

        # Filter option
        st.subheader("Glossary Adherence Report")
        filter_option = st.selectbox(
            "Filter by Status:",
            ["All", "✅ Present", "❌ Missing"]
        )
        
        if filter_option != "All":
            filtered_df = results_df[results_df["Status"] == filter_option]
        else:
            filtered_df = results_df

        st.dataframe(filtered_df)

        # Downloadable Excel Report
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            results_df.to_excel(writer, index=False, sheet_name="Report")
        st.download_button(
            label="Download Full Report (Excel)",
            data=output.getvalue(),
            file_name="glossary_adherence_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # Highlighted Target File (all target terms)
        st.subheader("Download Highlighted Target File")
        highlighted_target_doc = highlight_docx_terms(target_file, glossary_df["Target Term"].tolist())
        highlighted_target_output = BytesIO()
        highlighted_target_doc.save(highlighted_target_output)
        st.download_button(
            label="Download Highlighted Target File",
            data=highlighted_target_output.getvalue(),
            file_name="highlighted_target.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

        # Highlighted Source File (only missing target terms)
        st.subheader("Download Highlighted Source File (Terms Missing in Target)")
        highlighted_source_doc = highlight_missing_source_terms(source_file, glossary_df, target_text)
        highlighted_source_output = BytesIO()
        highlighted_source_doc.save(highlighted_source_output)
        st.download_button(
            label="Download Highlighted Source File",
            data=highlighted_source_output.getvalue(),
            file_name="highlighted_source_terms.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
