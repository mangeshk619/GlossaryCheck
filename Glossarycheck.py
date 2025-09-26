import streamlit as st
import pandas as pd
import re
import io
from typing import List, Tuple, Optional

# Optional: OpenCC for Simplified↔Traditional checks
try:
    from opencc import OpenCC
    OPENCC_AVAILABLE = True
    cc_s2t = OpenCC('s2t')
except Exception:
    OPENCC_AVAILABLE = False
    cc_s2t = None

st.set_page_config(page_title="TW MoJ Terminology Checker", layout="wide")
st.title("🇹🇼 Ministry of Justice (Taiwan) — Separate Source/Target Terminology & Script Checker")

with st.expander("How it works"):
    st.markdown("""
**Inputs supported**
- **Source (English)**: `.txt`, `.csv`, `.tsv`, `.xlsx`, `.xls`, `.docx`
- **Target (Traditional Chinese)**: `.txt`, `.csv`, `.tsv`, `.xlsx`, `.xls`, `.docx`
- **Glossary**: `.csv` or `.xlsx` with columns:
  - `en_term` (English source term/pattern)
  - `zh_tw_term` (required ZH-TW term)
  - *(optional)* `notes`
- *(Optional)* Mainland→Taiwan overrides CSV/XLSX with columns: `mainland,taiwan`

**Alignment**
- If your files are tabular and have the relevant text in specific columns, set the column names below.
- If files are plain text, alignment is by **line number** (line 1 ↔ line 1, etc.).

**What is checked**
1) Which glossary **EN terms** occur in the Source, and whether the **required ZH-TW terms** appear in the aligned Target segment (adhered / not adhered).  
2) **Simplified Chinese** characters in Target (OpenCC heuristic).  
3) **Mainland** terms in Target and suggested **Taiwan-preferred** equivalents (seed list + optional overrides).
    """)

# --- Mainland→Taiwan seed list (extend with your own CSV/XLSX) ---
MAINLAND_TO_TW = {
    "软件": "軟體",
    "硬件": "硬體",
    "互联网": "網際網路",
    "网络": "網路",
    "手机": "手機",
    "邮箱": "電子郵件",
    "邮件": "郵件",
    "图标": "圖示",
    "应用程序": "應用程式",
    "服务器": "伺服器",
    "高清": "高畫質",
    "视频": "影片",
    "打印机": "印表機",
    "鼠标": "滑鼠",
    "键盘": "鍵盤",
    "用户": "使用者",
    "复印": "影印",
    "登录": "登入",
    "登出": "登出",
}

# ----------------- File loaders -----------------
def load_plain_text(file) -> str:
    name = file.name.lower()
    if name.endswith(".txt"):
        return file.read().decode("utf-8", errors="ignore")
    elif name.endswith((".csv", ".tsv")):
        sep = "," if name.endswith(".csv") else "\t"
        df = pd.read_csv(file, sep=sep, dtype=str).fillna("")
        return "\n".join([" ".join(map(str, row)) for row in df.values])
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(file, dtype=str).fillna("")
        return "\n".join([" ".join(map(str, row)) for row in df.values])
    elif name.endswith(".docx"):
        try:
            from docx import Document
        except Exception:
            st.error("DOCX support requires `python-docx` in requirements.txt")
            raise
        d = Document(file)
        paras = [p.text for p in d.paragraphs]
        return "\n".join(paras)
    else:
        st.error(f"Unsupported text format: {name}")
        return ""

def load_table(file) -> Optional[pd.DataFrame]:
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file, dtype=str).fillna("")
    if name.endswith(".tsv"):
        return pd.read_csv(file, sep="\t", dtype=str).fillna("")
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file, dtype=str).fillna("")
    # For DOCX/TXT, not table-like; return None
    return None

def best_effort(file, prefer_table_col: Optional[str]) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Try to read as table if a column name is provided; otherwise fall back to text.
    Returns (df, text). Only one is non-None.
    """
    if prefer_table_col:
        df = load_table(file)
        if df is not None:
            return df, None
        file.seek(0)
    # fallback to plain text
    text = load_plain_text(file)
    return None, text

def segments_from_text(text: str) -> List[str]:
    # Line-wise segmentation
    return [ln.strip() for ln in text.splitlines()]

def build_aligned_segments(
    src_df: Optional[pd.DataFrame], src_text: Optional[str], src_col: Optional[str],
    tgt_df: Optional[pd.DataFrame], tgt_text: Optional[str], tgt_col: Optional[str],
) -> Tuple[List[str], List[str]]:
    if src_df is not None and src_col:
        src = src_df[src_col].astype(str).tolist()
    elif src_text is not None:
        src = segments_from_text(src_text)
    else:
        src = []

    if tgt_df is not None and tgt_col:
        tgt = tgt_df[tgt_col].astype(str).tolist()
    elif tgt_text is not None:
        tgt = segments_from_text(tgt_text)
    else:
        tgt = []

    # pad to equal length
    n = max(len(src), len(tgt))
    if len(src) < n:
        src += [""] * (n - len(src))
    if len(tgt) < n:
        tgt += [""] * (n - len(tgt))
    return src, tgt

# ----------------- Glossary / detection helpers -----------------
def glossary_check(src: List[str], tgt: List[str], glossary: pd.DataFrame,
                   case_sensitive: bool, whole_word: bool) -> pd.DataFrame:
    recs = []
    flags = 0 if case_sensitive else re.IGNORECASE
    for i, (s, t) in enumerate(zip(src, tgt), start=1):
        for _, row in glossary.iterrows():
            en = str(row["en_term"])
            zh_req = str(row["zh_tw_term"])
            notes = str(row.get("notes", ""))

            pat = re.escape(en)
            if whole_word:
                pat = r"\b" + pat + r"\b"

            if re.search(pat, s, flags):
                adhered = zh_req in t
                recs.append({
                    "segment": i,
                    "source_excerpt": s[:200],
                    "target_excerpt": t[:200],
                    "en_term": en,
                    "zh_tw_expected": zh_req,
                    "adhered": adhered,
                    "notes": notes
                })
    return pd.DataFrame(recs)

def simplified_check(tgt: List[str]) -> pd.DataFrame:
    if not OPENCC_AVAILABLE:
        return pd.DataFrame()
    hits = []
    for i, t in enumerate(tgt, start=1):
        conv = cc_s2t.convert(t)
        if conv != t:
            hits.append({"segment": i, "text_excerpt": t[:200]})
    return pd.DataFrame(hits)

def mainland_check(tgt: List[str], map_ml2tw: dict) -> pd.DataFrame:
    hits = []
    for i, t in enumerate(tgt, start=1):
        if not t.strip():
            continue
        for ml, tw in map_ml2tw.items():
            if ml in t:
                hits.append({"segment": i, "mainland_term": ml, "suggested_tw": tw, "context": t[:200]})
    return pd.DataFrame(hits)

# ----------------- Sidebar: uploads & options -----------------
st.sidebar.header("Upload files (separate Source & Target)")
src_file = st.sidebar.file_uploader("Source (English)", type=["txt","csv","tsv","xlsx","xls","docx"])
tgt_file = st.sidebar.file_uploader("Target (ZH-TW)", type=["txt","csv","tsv","xlsx","xls","docx"])
gls_file = st.sidebar.file_uploader("Glossary (CSV/XLSX)", type=["csv","xlsx","xls"])
override_file = st.sidebar.file_uploader("Optional Mainland→Taiwan overrides (CSV/XLSX)", type=["csv","xlsx","xls"])

st.sidebar.subheader("Column names (ONLY if your files are tabular)")
src_col = st.sidebar.text_input("Source column name (leave empty for TXT/DOCX)")
tgt_col = st.sidebar.text_input("Target column name (leave empty for TXT/DOCX)")

st.sidebar.subheader("Matching options")
whole_word = st.sidebar.checkbox("Match EN terms as whole words", True)
case_sensitive = st.sidebar.checkbox("Case-sensitive EN match", False)

# Load overrides
if override_file is not None:
    try:
        if override_file.name.lower().endswith(".csv"):
            df_override = pd.read_csv(override_file, dtype=str).fillna("")
        else:
            df_override = pd.read_excel(override_file, dtype=str).fillna("")
        add_map = {str(r["mainland"]).strip(): str(r["taiwan"]).strip()
                   for _, r in df_override.iterrows() if str(r.get("mainland","")).strip()}
        MAINLAND_TO_TW.update(add_map)
        st.sidebar.success(f"Loaded {len(add_map)} Mainland→Taiwan overrides.")
    except Exception as e:
        st.sidebar.warning(f"Could not read overrides: {e}")

# ----------------- Run checks -----------------
if src_file and tgt_file and gls_file:
    # Read source/target with table preference if a column is given
    src_df, src_text = best_effort(src_file, prefer_table_col=src_col)
    tgt_df, tgt_text = best_effort(tgt_file, prefer_table_col=tgt_col)

    # Build aligned segments
    src_segments, tgt_segments = build_aligned_segments(
        src_df, src_text, src_col, tgt_df, tgt_text, tgt_col
    )
    st.info(f"Aligned {len(src_segments)} segment(s) (row/line-wise).")

    # Read glossary
    try:
        if gls_file.name.lower().endswith(".csv"):
            glossary = pd.read_csv(gls_file, dtype=str).fillna("")
        else:
            glossary = pd.read_excel(gls_file, dtype=str).fillna("")
    except Exception as e:
        st.error(f"Failed to read glossary: {e}")
        st.stop()

    # Flexible header mapping
    low = {c.lower(): c for c in glossary.columns}
    def pick(*names):
        for n in names:
            if n in low: return low[n]
        return None

    en_col = pick("en_term", "english", "source", "en")
    zh_col = pick("zh_tw_term", "zh_tw", "zh-hant", "traditional_chinese", "target", "tw")

    if not en_col or not zh_col:
        st.error("Glossary must have columns `en_term` and `zh_tw_term` (headers can be flexibly named).")
        st.stop()

    glossary = glossary.rename(columns={en_col: "en_term", zh_col: "zh_tw_term"})

    # Checks
    adh = glossary_check(src_segments, tgt_segments, glossary, case_sensitive, whole_word)
    simp = simplified_check(tgt_segments)
    ml = mainland_check(tgt_segments, MAINLAND_TO_TW)

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    total_triggers = len(adh)
    adhered = int(adh["adhered"].sum()) if not adh.empty else 0
    k1.metric("Glossary triggers (EN found in Source)", total_triggers)
    k2.metric("Adhered (required ZH-TW present)", adhered)
    k3.metric("Non-adhered", total_triggers - adhered)
    k4.metric("Simplified char flags", len(simp))

    st.subheader("Glossary adherence (segment-level)")
    if adh.empty:
        st.info("No glossary terms found in the Source.")
    else:
        st.dataframe(adh, use_container_width=True)

    st.subheader("Simplified Chinese characters flagged")
    if OPENCC_AVAILABLE:
        st.dataframe(simp if not simp.empty else pd.DataFrame([{"info":"None detected"}]), use_container_width=True)
    else:
        st.warning("OpenCC not installed. Add `opencc-python-reimplemented` to requirements.txt to enable this check.")

    st.subheader("Mainland terms vs Taiwan-preferred equivalents")
    st.dataframe(ml if not ml.empty else pd.DataFrame([{"info":"None detected in current list"}]), use_container_width=True)

    # Export: Excel with multiple sheets
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        (adh if not adh.empty else pd.DataFrame([{"info":"No glossary matches"}])).to_excel(writer, index=False, sheet_name="glossary_adherence")
        (simp if not simp.empty else pd.DataFrame([{"info":"No simplified chars flagged"}])).to_excel(writer, index=False, sheet_name="simplified_chars")
        (ml if not ml.empty else pd.DataFrame([{"info":"No mainland terms flagged"}])).to_excel(writer, index=False, sheet_name="mainland_vs_tw")
        pd.DataFrame({"segment": list(range(1, len(src_segments)+1)),
                      "source": src_segments,
                      "target": tgt_segments}).to_excel(writer, index=False, sheet_name="alignment_dump")
    st.download_button("Download report.xlsx", data=out.getvalue(),
                       file_name="tw_moj_check_report.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

else:
    st.info("Upload **Source (EN)**, **Target (ZH-TW)**, and **Glossary** to run checks.")
