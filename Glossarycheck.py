import streamlit as st
import pandas as pd
import re
import io
from typing import List, Tuple, Optional

# Optional OpenCC for Simplified→Traditional detection
try:
    from opencc import OpenCC
    OPENCC_AVAILABLE = True
    cc_s2t = OpenCC('s2t')
except Exception:
    OPENCC_AVAILABLE = False
    cc_s2t = None

st.set_page_config(page_title="TW MoJ — Target-only Glossary Adherence", layout="wide")
st.title("🇹🇼 TW MoJ — Target-only Glossary Adherence Checker")

with st.expander("What this does (no alignment required)"):
    st.markdown("""
- **Modes**
  - **Target-only**: enforce `zh_tw_term` existence in Target corpus (ignores Source).
  - **Filtered by Source**: only enforce terms whose **EN** form occurs **somewhere** in the Source corpus.
- **Outputs**
  - Per-term coverage with counts and sample contexts from Target
  - Which EN terms were found in Source (if provided)
  - Simplified Chinese flags (heuristic via OpenCC)
  - Mainland→Taiwan term flags
- **Glossary**
  - Columns: `en_term`, `zh_tw_term`, optional `notes`, optional `en_term_regex`, `zh_tw_term_regex`
  - Multiple accepted ZH variants via `zh_tw_term` like `A|B|C`
    """)

# Mainland→Taiwan seed list (extend/override via upload)
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

# --------- Utilities ---------
_FW_TO_HW = str.maketrans({
    "，": ",", "。": ".", "；": ";", "：": ":", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]", "「": '"', "」": '"',
    "『": '"', "』": '"', "、": ",", "　": " ", "－": "-", "～": "~",
    "《": "<", "》": ">"
})
def normalize_zh(s: str) -> str:
    if s is None:
        return ""
    s = str(s).translate(_FW_TO_HW)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def normalize_en(s: str) -> str:
    if s is None:
        return ""
    s = re.sub(r"\s+", " ", str(s))
    return s.strip()

def read_any(file) -> str:
    """Read any TXT/CSV/TSV/XLSX/XLS/DOCX into a single corpus string."""
    name = file.name.lower()
    if name.endswith(".txt"):
        return file.read().decode("utf-8", errors="ignore")
    elif name.endswith(".csv"):
        df = pd.read_csv(file, dtype=str).fillna("")
        return "\n".join([" ".join(map(str, r)) for r in df.values])
    elif name.endswith(".tsv"):
        df = pd.read_csv(file, sep="\t", dtype=str).fillna("")
        return "\n".join([" ".join(map(str, r)) for r in df.values])
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(file, dtype=str).fillna("")
        return "\n".join([" ".join(map(str, r)) for r in df.values])
    elif name.endswith(".docx"):
        try:
            from docx import Document
        except Exception:
            st.error("Add `python-docx` to requirements.txt for DOCX support.")
            raise
        d = Document(file)
        return "\n".join([p.text for p in d.paragraphs])
    else:
        st.error(f"Unsupported file type: {name}")
        return ""

def load_table(file) -> Optional[pd.DataFrame]:
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file, dtype=str).fillna("")
    if name.endswith(".tsv"):
        return pd.read_csv(file, sep="\t", dtype=str).fillna("")
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file, dtype=str).fillna("")
    return None

def load_glossary(gls_file: st.runtime.uploaded_file_manager.UploadedFile) -> pd.DataFrame:
    if gls_file.name.lower().endswith(".csv"):
        df = pd.read_csv(gls_file, dtype=str).fillna("")
    else:
        df = pd.read_excel(gls_file, dtype=str).fillna("")
    # Flexible headers
    low = {c.lower(): c for c in df.columns}
    def pick(*names):
        for n in names:
            if n in low: return low[n]
        return None
    en_col = pick("en_term", "english", "source", "en")
    zh_col = pick("zh_tw_term", "zh_tw", "zh-hant", "traditional_chinese", "target", "tw")
    reg_en = pick("en_term_regex",)
    reg_zh = pick("zh_tw_term_regex",)
    notes = pick("notes",)

    if not en_col or not zh_col:
        raise ValueError("Glossary must have `en_term` and `zh_tw_term` columns (header aliases allowed).")

    df = df.rename(columns={
        en_col: "en_term",
        zh_col: "zh_tw_term",
        **({reg_en: "en_term_regex"} if reg_en else {}),
        **({reg_zh: "zh_tw_term_regex"} if reg_zh else {}),
        **({notes: "notes"} if notes else {}),
    })
    return df

def split_variants(v: str) -> List[str]:
    if not v: return []
    return [x.strip() for x in str(v).split("|") if x.strip()]

def find_all(pattern, text, flags=0):
    return list(re.finditer(pattern, text, flags))

def sample_contexts(text: str, spans: list, window: int = 28) -> List[str]:
    ctx = []
    for m in spans[:5]:
        a, b = m.start(), m.end()
        left = max(0, a - window)
        right = min(len(text), b + window)
        ctx.append(text[left:right].replace("\n", " "))
    return ctx

# --------- Sidebar Uploads & Options ---------
st.sidebar.header("Uploads")
src_file = st.sidebar.file_uploader("Optional Source (English corpus)", type=["txt","csv","tsv","xlsx","xls","docx"])
tgt_file = st.sidebar.file_uploader("Target (ZH-TW corpus) — required", type=["txt","csv","tsv","xlsx","xls","docx"])
gls_file = st.sidebar.file_uploader("Glossary (CSV/XLSX) — required", type=["csv","xlsx","xls"])
override_file = st.sidebar.file_uploader("Optional Mainland→Taiwan overrides (CSV/XLSX)", type=["csv","xlsx","xls"])

st.sidebar.header("Mode")
mode = st.sidebar.radio("Check mode", ["Filtered by Source (recommended)", "Target-only"], index=0)

st.sidebar.header("Matching")
whole_word = st.sidebar.checkbox("EN whole-word match", True)
case_sensitive = st.sidebar.checkbox("EN case-sensitive", False)

# Load overrides
if override_file is not None:
    try:
        df_o = load_table(override_file)
        add_map = {str(r["mainland"]).strip(): str(r["taiwan"]).strip()
                   for _, r in df_o.iterrows() if str(r.get("mainland","")).strip()}
        MAINLAND_TO_TW.update(add_map)
        st.sidebar.success(f"Loaded {len(add_map)} Mainland→Taiwan overrides.")
    except Exception as e:
        st.sidebar.warning(f"Override load failed: {e}")

# --------- Run ---------
if tgt_file and gls_file:
    # Read corpora
    src_corpus = normalize_en(read_any(src_file)) if src_file else ""
    tgt_corpus_raw = read_any(tgt_file)
    tgt_corpus = normalize_zh(tgt_corpus_raw)

    # Glossary
    try:
        glossary = load_glossary(gls_file)
    except Exception as e:
        st.error(str(e))
        st.stop()

    # Build EN filter if needed
    if mode == "Filtered by Source (recommended)":
        if not src_file:
            st.warning("Filtered-by-Source mode selected, but no Source uploaded. All terms will be enforced. "
                       "Upload a Source file or switch to Target-only mode.")
        flags_en = 0 if case_sensitive else re.IGNORECASE
        enforce_mask = []
        for _, r in glossary.iterrows():
            en = str(r["en_term"])
            is_regex = str(r.get("en_term_regex","")).strip().lower() in ("y","yes","true","1")
            if not src_corpus:
                enforce_mask.append(True)
                continue
            if is_regex:
                try:
                    pat = re.compile(en, flags_en)
                except re.error:
                    enforce_mask.append(False)
                    continue
                enforce_mask.append(bool(pat.search(src_corpus)))
            else:
                pat = re.escape(en)
                if whole_word:
                    pat = r"\b" + pat + r"\b"
                enforce_mask.append(bool(re.search(pat, src_corpus, flags_en)))
        glossary = glossary.loc[enforce_mask].reset_index(drop=True)
        st.info(f"Enforcing {len(glossary)} glossary rows (filtered by Source occurrences).")
    else:
        st.info(f"Target-only mode: enforcing all {len(glossary)} glossary rows against Target corpus.")

    # Target coverage per term
    rows = []
    dbg = []
    for _, r in glossary.iterrows():
        en = str(r["en_term"])
        zh = str(r["zh_tw_term"])
        note = str(r.get("notes",""))

        zh_is_regex = str(r.get("zh_tw_term_regex","")).strip().lower() in ("y","yes","true","1")
        flags_zh = 0  # Chinese case sensitivity usually irrelevant

        # Build variant patterns
        variants = split_variants(zh)
        if not variants:
            variants = [zh]

        matches_total = 0
        contexts = []
        matched_variants = []

        for v in variants:
            if zh_is_regex:
                try:
                    pat = re.compile(v)
                except re.error as e:
                    dbg.append({"en_term": en, "variant": v, "status": f"bad_zh_regex: {e}"})
                    continue
                spans = find_all(pat, tgt_corpus, flags_zh)
            else:
                vn = normalize_zh(v)
                if not vn:
                    continue
                # literal contains — produce faux spans
                spans = []
                start = 0
                while True:
                    pos = tgt_corpus.find(vn, start)
                    if pos == -1: break
                    spans.append(re.Match)  # dummy holder not used; we’ll use indices
                    contexts.append(tgt_corpus[max(0,pos-28):pos+len(vn)+28])
                    matches_total += 1
                    matched_variants.append(v)
                    start = pos + len(vn)
                continue  # we already counted/collected contexts for literal

            if spans:
                matches_total += len(spans)
                contexts += sample_contexts(tgt_corpus, spans)
                matched_variants += [v] * len(spans)

        rows.append({
            "en_term": en,
            "zh_tw_expected": zh,
            "adhered_in_target": matches_total > 0,
            "match_count": matches_total,
            "matched_variant_examples": "|".join(sorted(set(matched_variants))) if matched_variants else "",
            "sample_contexts": " … ".join(contexts[:5]),
            "notes": note
        })

    coverage = pd.DataFrame(rows)

    # Simplified Chinese flags (heuristic)
    if OPENCC_AVAILABLE:
        conv = cc_s2t.convert(tgt_corpus_raw)
        differing_chars = sorted({ch for ch in tgt_corpus_raw if re.match(r"[\u4e00-\u9fff]", ch) and cc_s2t.convert(ch) != ch})
        simp_df = pd.DataFrame([{"differing_chars": "".join(differing_chars)}]) if differing_chars else pd.DataFrame([{"info":"No simplified chars flagged"}])
    else:
        simp_df = pd.DataFrame([{"info":"OpenCC not installed"}])

    # Mainland terms in Target
    ml_hits = []
    for ml, tw in MAINLAND_TO_TW.items():
        if ml in tgt_corpus_raw:
            # collect a few contexts
            contexts = []
            start = 0
            while True:
                pos = tgt_corpus_raw.find(ml, start)
                if pos == -1: break
                contexts.append(tgt_corpus_raw[max(0,pos-28):pos+len(ml)+28].replace("\n"," "))
                if len(contexts) >= 5: break
                start = pos + len(ml)
            ml_hits.append({"mainland_term": ml, "suggested_tw": tw, "sample_contexts": " … ".join(contexts)})
    ml_df = pd.DataFrame(ml_hits) if ml_hits else pd.DataFrame([{"info":"No mainland terms (from current list) detected"}])

    # KPIs
    k1, k2, k3 = st.columns(3)
    enforced = len(coverage)
    adhered = int(coverage["adhered_in_target"].sum()) if not coverage.empty else 0
    k1.metric("Glossary rows enforced", enforced)
    k2.metric("Adhered in Target", adhered)
    k3.metric("Not adhered", enforced - adhered)

    st.subheader("Glossary adherence — corpus level (Target only)")
    st.dataframe(coverage, use_container_width=True)

    st.subheader("Simplified Chinese flags")
    st.dataframe(simp_df, use_container_width=True)

    st.subheader("Mainland terms in Target")
    st.dataframe(ml_df, use_container_width=True)

    # Export report
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
        (coverage if not coverage.empty else pd.DataFrame([{"info":"No coverage data"}])).to_excel(xw, index=False, sheet_name="target_glossary_coverage")
        simp_df.to_excel(xw, index=False, sheet_name="simplified_chars")
        ml_df.to_excel(xw, index=False, sheet_name="mainland_vs_tw")
        if src_file:
            pd.DataFrame([{"source_len_chars": len(src_corpus)}]).to_excel(xw, index=False, sheet_name="source_info")
        pd.DataFrame([{"target_len_chars": len(tgt_corpus_raw)}]).to_excel(xw, index=False, sheet_name="target_info")
    st.download_button("Download target_only_report.xlsx", data=out.getvalue(),
                       file_name="target_only_report.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

else:
    st.info("Upload **Target** and **Glossary** (Source optional if you select Filtered-by-Source).")
