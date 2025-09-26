import streamlit as st
import pandas as pd
import re
import io
from typing import List, Tuple, Optional

# Optional: OpenCC for simplified/traditional detection
try:
    from opencc import OpenCC
    OPENCC_AVAILABLE = True
    cc_s2t = OpenCC('s2t')
except Exception:
    OPENCC_AVAILABLE = False
    cc_s2t = None

st.set_page_config(page_title="TW MoJ Terminology Checker", layout="wide")

st.title("🇹🇼 Ministry of Justice (Taiwan) Terminology & Script Checker")

# Mainland → Taiwan wordlist (extendable with CSV)
MAINLAND_TO_TW = {
    "软件": "軟體",
    "硬件": "硬體",
    "互联网": "網際網路",
    "网络": "網路",
    "手机": "手機",
    "服务器": "伺服器",
    "用户": "使用者",
    "视频": "影片",
    "打印机": "印表機",
}

# ---- Loaders ----
def load_table(file):
    if file.name.endswith(".csv"):
        return pd.read_csv(file, dtype=str).fillna("")
    elif file.name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file, dtype=str).fillna("")
    return None

# ---- Alignment ----
def build_segments(df, src_col, tgt_col):
    src = df[src_col].astype(str).tolist()
    tgt = df[tgt_col].astype(str).tolist()
    return src, tgt

# ---- Checks ----
def glossary_check(src, tgt, glossary, case_sensitive=False):
    recs = []
    for i, (s, t) in enumerate(zip(src, tgt), start=1):
        for _, row in glossary.iterrows():
            en, zh = row["en_term"], row["zh_tw_term"]
            pattern = re.escape(en)
            flags = 0 if case_sensitive else re.IGNORECASE
            if re.search(pattern, s, flags):
                recs.append({
                    "segment": i,
                    "source": s,
                    "target": t,
                    "en_term": en,
                    "zh_expected": zh,
                    "adhered": zh in t
                })
    return pd.DataFrame(recs)

def simplified_check(tgt):
    hits = []
    if not OPENCC_AVAILABLE:
        return pd.DataFrame()
    for i, t in enumerate(tgt, start=1):
        converted = cc_s2t.convert(t)
        if converted != t:
            hits.append({"segment": i, "text": t})
    return pd.DataFrame(hits)

def mainland_check(tgt):
    hits = []
    for i, t in enumerate(tgt, start=1):
        for ml, tw in MAINLAND_TO_TW.items():
            if ml in t:
                hits.append({"segment": i, "mainland": ml, "suggested_tw": tw, "text": t})
    return pd.DataFrame(hits)

# ---- UI ----
src_file = st.sidebar.file_uploader("Upload bilingual file (CSV/XLSX)", type=["csv","xlsx","xls"])
gls_file = st.sidebar.file_uploader("Upload glossary (CSV/XLSX)", type=["csv","xlsx"])
src_col = st.sidebar.text_input("Source column name (English)")
tgt_col = st.sidebar.text_input("Target column name (Chinese)")

if src_file and gls_file and src_col and tgt_col:
    df = load_table(src_file)
    glossary = load_table(gls_file)
    glossary = glossary.rename(columns=str.lower)

    if not {"en_term","zh_tw_term"}.issubset(glossary.columns):
        st.error("Glossary must have columns en_term and zh_tw_term")
    else:
        src, tgt = build_segments(df, src_col, tgt_col)

        adh = glossary_check(src, tgt, glossary)
        simp = simplified_check(tgt)
        ml = mainland_check(tgt)

        st.subheader("Glossary adherence")
        st.dataframe(adh)

        st.subheader("Simplified characters flagged")
        st.dataframe(simp if not simp.empty else pd.DataFrame([{"info":"None"}]))

        st.subheader("Mainland terms flagged")
        st.dataframe(ml if not ml.empty else pd.DataFrame([{"info":"None"}]))

        # Export Excel
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            adh.to_excel(writer, index=False, sheet_name="glossary")
            simp.to_excel(writer, index=False, sheet_name="simplified")
            ml.to_excel(writer, index=False, sheet_name="mainland")
            pd.DataFrame({"source":src, "target":tgt}).to_excel(writer, index=False, sheet_name="alignment")
        st.download_button("Download report.xlsx", out.getvalue(), "report.xlsx")
else:
    st.info("Upload bilingual file + glossary, and enter source/target column names.")
