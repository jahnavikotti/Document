import streamlit as st
import google.generativeai as genai
import json, re
import pandas as pd
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
from io import BytesIO

# -------------------------------
# Configure Gemini API
# -------------------------------
genai.configure(api_key="AIzaSyAUog3t78n5REhJ2iiREZ5QtQ1tgC3AsPU")
model = genai.GenerativeModel("gemini-1.5-flash")

st.set_page_config(page_title="PDF Document Parser", page_icon="📑")
st.title("📑 Smart PDF Document Parser")
st.write("Upload one or more PDFs (Aadhaar, PAN, Passport, or Study Certificates). "
         "Each page will be OCR-processed, analyzed, and results saved into Excel.")

# -------------------------------
# Helper: extract clean JSON
# -------------------------------
def extract_json_block(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1)
    start = text.find("{")
    if start == -1:
        return None
    cnt = 0; in_str = False; esc = False
    for i, ch in enumerate(text[start:], start=start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                cnt += 1
            elif ch == "}":
                cnt -= 1
                if cnt == 0:
                    return text[start:i+1]
    return None

# -------------------------------
# Prompt for Gemini
# -------------------------------
prompt = """
You are an intelligent document parser.
This page may contain Aadhaar, PAN, Passport, or Study Certificates.

Extract ONLY these fields based on type:
- Aadhaar → Name, DOB, Aadhaar Number
- PAN → Name, DOB, PAN Number
- Passport → Name, Passport Number, Nationality, DOB, Expiry Date
- Study Certificate → Student Name, Course, College/University, Passout Year,CGPA

Return JSON in this exact format:
{
  "document_type": "Study Certificate",
  "extracted_fields": {
    "Student Name": "...",
    "Course": "...",
    "College/University": "...",
    "Passout Year": "...",
    "CGPA": "..."
  }
}
"""

# -------------------------------
# Column mapping per document type
# -------------------------------
doc_columns = {
    "Aadhaar": ["document_type", "Name", "DOB", "Aadhaar Number", "source_file"],
    "PAN": ["document_type", "Name", "DOB", "PAN Number", "source_file"],
    "Passport": ["document_type", "Name", "Passport Number", "Nationality", "DOB", "Expiry Date", "source_file"],
    "Study Certificate": ["document_type", "Student Name", "Course", "College/University", "Passout Year","CGPA","source_file"],
}

# -------------------------------
# File uploader (multiple PDFs)
# -------------------------------
uploaded_pdfs = st.file_uploader("Upload your documents (PDF only)", type=["pdf"], accept_multiple_files=True)

# -------------------------------
# Main processing
# -------------------------------
if uploaded_pdfs and st.button("Extract All Details"):
    results = []

    with st.spinner("Running OCR and analyzing all PDFs..."):
        for pdf in uploaded_pdfs:
            pdf_data = pdf.read()
            doc = fitz.open(stream=pdf_data, filetype="pdf")

            for page_num in range(len(doc)):
                try:
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=300)  # Render page as image
                    img = Image.open(io.BytesIO(pix.tobytes("png")))

                    # OCR with pytesseract
                    page_text = pytesseract.image_to_string(img)

                    if not page_text.strip():
                        continue  # skip empty pages

                    response = model.generate_content([prompt, page_text])
                    raw = (response.text or "").strip()

                    candidate = extract_json_block(raw) or raw
                    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)

                    data = json.loads(candidate)

                    if isinstance(data, dict):
                        flat = {"document_type": data.get("document_type", "")}
                        if isinstance(data.get("extracted_fields"), dict):
                            flat.update(data["extracted_fields"])
                        elif isinstance(data.get("extracted_data"), dict):
                            flat.update(data["extracted_data"])
                        flat["source_file"] = f"{pdf.name} (page {page_num+1})"
                        results.append(flat)

                except Exception as e:
                    results.append({"source_file": f"{pdf.name} (page {page_num+1})", "error": str(e)})

    # Convert to DataFrame
    df = pd.DataFrame(results)
    st.success("✅ All documents parsed")
    st.dataframe(df)

    # -------------------------------
    # Excel download (multi-sheet, no All Documents)
    # -------------------------------
    xbuf = BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as writer:
        wrote_any = False
        if "document_type" in df.columns and not df.empty:
            for doc_type, group in df.groupby("document_type"):
                if group.empty:
                    continue
                safe_sheet_name = str(doc_type)[:31]
                if doc_type in doc_columns:
                    cols = [c for c in doc_columns[doc_type] if c in group.columns]
                    group = group.reindex(columns=cols)
                group.to_excel(writer, index=False, sheet_name=safe_sheet_name)
                wrote_any = True

        if not wrote_any:
            pd.DataFrame([{"message": "No valid data extracted"}]).to_excel(
                writer, index=False, sheet_name="Results"
            )
    xbuf.seek(0)

    st.download_button(
        label="📥 Download Excel (sheets by document type)",
        data=xbuf,
        file_name="parsed_documents_by_type.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
