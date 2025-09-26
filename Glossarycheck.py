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

st.set_page_config(page_title="TW MoJ Terminology Checker (EN → ZH-TW)", layout="wide")
st.title("🇹🇼 Ministry of Justice (Taiwan) — Separate Source/Target Terminology & Script Checker")

with st.expander("How it works / What changed"):
    st.markdown("""
**NEW (to fix false negatives/positives):**
- **Alignment preview** (first 20 rows) to verify Source↔Target pairing
- **Normalization** for ZH: trims whitespace, normalizes full-width/half-width punctuation, collapses spaces
- **Multiple ZH variants** per glossary row via `zh_tw_term_variants` **or** pipe-separated values in `zh_tw_term`  
  (e.g., `檢察官|檢察官（書記官）|檢察官（含代理）`)
- **Optional regex columns**: `en_term_regex` and/or `zh_tw_term_regex` (Y/Yes/True) for pattern-based matching
- **Whole-word** matching toggle for English (default **ON**)
- **Debug sheet** with normalized strings, match reasons
- Better **Simplified char** sheet (lists differing characters with context)
    """)

# Mainland → Taiwan seed list (extend with your own CSV/XLSX)
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

# ----------------- Normalization helpers -----------------
_FW_TO_HW = str.maketrans({
    "，": ",", "。": ".", "；": ";", "：": ":", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]", "「": '"', "」": '"',
    "『": '"', "』": '"', "、": ",", "　": " ", "－": "-", "～": "~",
    "《": "<", "》": ">"
})
def normalize_zh(s: str) -> str:
    """Normalize Traditional Chinese string for robust contains checks."""
    if s is None:
        return ""
    s = str(s)
    s = s.translate(_FW_TO_HW)       # full-width → half-width punctuation
    s = re.sub(r"\s+", " ", s)       # collapse whitespace
    return s.strip()

def normalize_en(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def split_variants(val: str) -> List[str]:
    """Support variants via dedicated column OR pipe-separated."""
    if val is None:
        return []
    s = str(val).strip()
    if not s:
        return []
    return [x.strip() for x in s.split("|") if x.strip()]

# ----------------- File loaders -----------------
def load_plain_text(file) -> str:
    name = file.name.lower()
    if name.endswith(".txt"):
        return file.read().decode("utf-8", errors="ignore")
    elif name.endswith(".csv"):
        df = pd.read_csv(file, dtype=str).fillna("")
        return "\n".join([" ".join(map(str, row)) for row in df.values])
    elif name.endswith(".tsv"):
        df = pd.read_csv(file, sep="\t", dtype=str).fillna("")
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
    return None  # txt/docx → not table

def best_effort(file, prefer_table_col: Optional[str]) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Return (df, text). Only one will be non-None."""
    if prefer_table_col:
        df = load_table(file)
        if df is not None:
            return df, None
        file.seek(0)
    text = load_plain_text(file)
    return None, text

def segments_from_text(text: str) -> List[str]:
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

    n = max(len(src), len(tgt))
    if len(src) < n:
        src += [""] * (n - len(src))
    if len(tgt) < n:
        tgt += [""] * (n - len(tgt))
    return src, tgt

# ----------------- Matching helpers -----------------
def contains_en(hay: str, needle: str, case_sensitive: bool, whole_word: bool, is_regex: bool) -> bool:
    if not needle:
        return False
    if is_regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pat = re.compile(needle, flags)
        except re.error:
            return False
        return bool(pat.search(hay))
    # literal
    flags = 0 if case_sensitive else re.IGNORECASE
    pat = re.escape(needle)
    if whole_word:
        pat = r"\b" + pat + r"\b"
    return bool(re.search(pat, hay, flags))

def contains_zh(hay_norm: str, zh_expected: str, zh_regex: bool) -> Tuple[bool, str]:
    """Return (match, reason). `hay_norm` already normalized."""
    if not zh_expected:
        return False, "no_zh_expected"
    if zh_regex:
        try:
            pat = re.compile(zh_expected)
        except re.error as e:
            return False, f"bad_zh_regex: {e}"
        return (bool(pat.search(hay_norm)), "regex") if pat.search(hay_norm) else (False, "regex_no_match")
    # literal; allow pipe-separated variants
    variants = split_variants(zh_expected)
    if not variants:
        variants = [zh_expected]
    for v in variants:
        if normalize_zh(v) and normalize_zh(v) in hay_norm:
            return True, f"literal_contains:{v}"
    return False, "literal_no_variant_match"

# ----------------- Glossary / detection -----------------
def glossary_check(src: List[str], tgt: List[str], glossary: pd.DataFrame,
                   case_sensitive: bool, whole_word: bool) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    debug = []
    for i, (s_raw, t_raw) in enumerate(zip(src, tgt), start=1):
        s = normalize_en(s_raw)
        t = normalize_zh(t_raw)
        for _, row in glossary.iterrows():
            en = str(row["en_term"]).strip()
            zh_req = str(row["zh_tw_term"]).strip()
            notes = str(row.get("notes", ""))

            en_regex = str(row.get("en_term_regex", "")).strip().lower() in ("y","yes","true","1")
            zh_regex = str(row.get("zh_tw_term_regex", "")).strip().lower() in ("y","yes","true","1")

            en_found = contains_en(s, en, case_sensitive, whole_word, en_regex)
            if not en_found:
                continue

            zh_match, reason = contains_zh(t, zh_req, zh_regex)

            rows.append({
                "segment": i,
                "source_excerpt": s_raw[:200],
                "target_excerpt": t_raw[:200],
                "en_term": en,
                "zh_tw_expected": zh_req,
                "adhered": zh_match,
                "reason": reason,
                "notes": notes
            })
            debug.append({
                "segment": i,
                "src_norm": s,
                "tgt_norm": t,
                "en_term": en,
                "en_regex": en_regex,
                "whole_word": whole_word,
                "case_sensitive": case_sensitive,
                "zh_expected": zh_req,
                "zh_regex": zh_regex,
                "adhered": zh_match,
                "reason": reason
            })
    return pd.DataFrame(rows), pd.DataFrame(debug)

def simplified_check(tgt: List[str]) -> pd.DataFrame:
    if not OPENCC_AVAILABLE:
        return pd.DataFrame()
    hits = []
    for i, t in enumerate(tgt, start=1):
        conv = cc_s2t.convert(t)
        if conv != t:
            # collect differing CJK chars only
            differing = sorted({ch for ch in t if re.match(r"[\u4e00-\u9fff]", ch) and cc_s2t.convert(ch) != ch})
            if differing:
                hits.append({"segment": i, "differing_chars": "".join(differing), "target_excerpt": t[:200]})
    return pd.DataFrame(hits)

def mainland_check(tgt: List[str], map_ml2tw: dict) -> pd.DataFrame:
    hits = []
    for i, t_raw in enumerate(tgt, start=1):
        t = normalize_zh(t_raw)
        for ml, tw in map_ml2tw.items():
            if ml in t_raw:  # keep original context check
                hits.append({"segment": i, "mainland_term": ml, "suggested_tw": tw, "context": t_raw[:200]})
    return pd.DataFrame(hits).drop_duplicates()

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

# ----------------- Run -----------------
if src_file and tgt_file and gls_file:
    src_df, src_text = best_effort(src_file, prefer_table_col=src_col)
    tgt_df, tgt_text = best_effort(tgt_file, prefer_table_col=tgt_col)

    src_segments, tgt_segments = build_aligned_segments(
        src_df, src_text, src_col, tgt_df, tgt_text, tgt_col
    )
    st.info(f"Aligned {len(src_segments)} segment(s) (row/line-wise).")

    # Alignment preview
    st.subheader("Quick alignment preview (first 20)")
    prev_df = pd.DataFrame({
        "segment": list(range(1, min(20, len(src_segments)) + 1)),
        "source": src_segments[:20],
        "target": tgt_segments[:20]
    })
    st.dataframe(prev_df, use_container_width=True)

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
    zh_var_col = pick("zh_tw_term_variants", "zh_variants", "variants")
    en_regex_col = pick("en_term_regex",)
    zh_regex_col = pick("zh_tw_term_regex",)

    if not en_col or not zh_col:
        st.error("Glossary must include `en_term` and `zh_tw_term` (headers can be flexibly named).")
        st.stop()

    glossary = glossary.rename(columns={
        en_col: "en_term",
        zh_col: "zh_tw_term",
        **({zh_var_col: "zh_tw_term_variants"} if zh_var_col else {}),
        **({en_regex_col: "en_term_regex"} if en_regex_col else {}),
        **({zh_regex_col: "zh_tw_term_regex"} if zh_regex_col else {}),
    })

    # Merge variants column (if present) into zh_tw_term as pipes so both paths work
    if "zh_tw_term_variants" in glossary.columns:
        merged = []
        for _, r in glossary.iterrows():
            base = str(r.get("zh_tw_term","")).strip()
            vars_ = split_variants(r.get("zh_tw_term_variants",""))
            merged.append("|".join([x for x in ([base] + vars_) if x]))
        glossary["zh_tw_term"] = merged

    # Checks
    adh, dbg = glossary_check(src_segments, tgt_segments, glossary, case_sensitive, whole_word)
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
    st.dataframe(adh if not adh.empty else pd.DataFrame([{"info":"No glossary matches"}]), use_container_width=True)

    st.subheader("Simplified Chinese characters flagged")
    if OPENCC_AVAILABLE:
        st.dataframe(simp if not simp.empty else pd.DataFrame([{"info":"None detected"}]), use_container_width=True)
    else:
        st.warning("OpenCC not installed. Add `opencc-python-reimplemented` to requirements.txt to enable this check.")

    st.subheader("Mainland terms vs Taiwan-preferred equivalents")
    st.dataframe(ml if not ml.empty else pd.DataFrame([{"info":"None detected in current list"}]), use_container_width=True)

    # Export: Excel with multiple sheets + debug
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        (adh if not adh.empty else pd.DataFrame([{"info":"No glossary matches"}])).to_excel(writer, index=False, sheet_name="glossary_adherence")
        (simp if not simp.empty else pd.DataFrame([{"info":"No simplified chars flagged"}])).to_excel(writer, index=False, sheet_name="simplified_chars")
        (ml if not ml.empty else pd.DataFrame([{"info":"No mainland terms flagged"}])).to_excel(writer, index=False, sheet_name="mainland_vs_tw")
        pd.DataFrame({"segment": list(range(1, len(src_segments)+1)), "source": src_segments, "target": tgt_segments}).to_excel(writer, index=False, sheet_name="alignment_dump")
        (dbg if not dbg.empty else pd.DataFrame([{"info":"no debug"}])).to_excel(writer, index=False, sheet_name="debug_internal")
    st.download_button("Download tw_moj_check_report.xlsx", data=out.getvalue(),
                       file_name="tw_moj_check_report.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("Upload **Source (EN)**, **Target (ZH-TW)**, and **Glossary** to run checks.")
