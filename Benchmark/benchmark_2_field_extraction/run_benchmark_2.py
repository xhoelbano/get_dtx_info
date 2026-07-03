#!/usr/bin/env python3
"""Benchmark 2 - Field-level extraction quality.

Scores each model's Phase 3 analysis against the manual ground-truth analysis,
cell-by-cell, using a data-type-appropriate metric per column (see
../methodology.md and column_types.json).

Pipeline:
  1. Reuse Benchmark 1 identifier linkage + union-find clustering to collapse
     protocol/publication records of the same study, then merge each cluster into
     one record per field (informative non-empty value).
  2. Build the evaluable set = GT studies that are (a) actually filled and
     (b) matched to a pipeline study. Dropped studies are logged with reasons.
  3. Score every scored column with its configured metric, classify blanks
     (both-empty / omission / unverified-addition), and aggregate per column and
     per model (macro-average headline). GT-blank cells the model filled are
     "unverified additions": counted, never scored as errors, because the manual
     GT is a small, incomplete subset (see methodology.md, blank handling).

Run from the repository root:
    python Benchmark/benchmark_2_field_extraction/run_benchmark_2.py
    python Benchmark/benchmark_2_field_extraction/run_benchmark_2.py --no-bertscore
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# --------------------------------------------------------------------------
# Paths and Benchmark 1 reuse
# --------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
RESULTS_DIR = SCRIPT_DIR / "results"
CONFIG_PATH = SCRIPT_DIR / "column_types.json"
GT_PATH = REPO_ROOT / "Test_Datasets" / "test_dataset_benchmarking_analysis.json"

B1_DIR = REPO_ROOT / "Benchmark" / "benchmark_1_study_counts"
sys.path.insert(0, str(B1_DIR))
import run_benchmark_1 as b1  # noqa: E402  (reuse normalize_ids / cluster_records)

MODELS: Dict[str, Path] = {
    "gpt-4o": REPO_ROOT / "Phase_3_Evidence_Analysis/gpt-4o/Website_Search_ON/phase3_combined.json",
    "gemini-3.1-pro-preview": REPO_ROOT / "Phase_3_Evidence_Analysis/gemini-3.1-pro-preview/Website_Search_ON/phase3_combined.json",
    "claude-sonnet-4-6": REPO_ROOT / "Phase_3_Evidence_Analysis/claude-sonnet-4-6/Website_Search_ON/phase3_combined.json",
}
REFERENCE_MODEL = "gpt-4o"  # used to decide study membership (identical across models)

# Minimum number of GT-blank cells before an addition_rate percentage is reported.
# Below this the rate is suppressed (reported as null) because a "100%" over 1-2
# cells is noise, not a finding. Additions are always reported as raw counts.
MIN_RATE_DENOM = 10

# Optional fuzzy lib
try:
    from rapidfuzz import fuzz as _fuzz

    def fuzzy_ratio(a: str, b: str) -> float:
        return _fuzz.ratio(a, b) / 100.0
except Exception:  # pragma: no cover
    import difflib

    def fuzzy_ratio(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------
# Basic normalization helpers
# --------------------------------------------------------------------------
EMPTY_TOKENS = {"", "-", "n/a", "na", "none", "null"}
_LEGAL_SUFFIXES = {
    "gmbh", "ag", "inc", "inc.", "ltd", "ltd.", "llc", "co", "co.", "kg", "mbh",
    "se", "gbr", "bv", "b.v.", "plc", "corp", "corp.", "company", "limited",
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def is_empty(s: Optional[str]) -> bool:
    return s is None or str(s).strip().lower() in EMPTY_TOKENS


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def norm_name(s: str) -> str:
    t = re.sub(r"[^\w\s]", " ", str(s).lower())
    toks = [w for w in t.split() if w not in _LEGAL_SUFFIXES]
    return re.sub(r"\s+", " ", " ".join(toks)).strip()


def parse_int(s: str) -> Optional[int]:
    m = re.search(r"-?\d[\d,]*", str(s))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_float(s: str) -> Optional[float]:
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def parse_date(s: str) -> Optional[Tuple[int, Optional[int], Optional[int]]]:
    """Return (year, month, day) with None for absent components, or None."""
    t = str(s).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        return int(m[1]), int(m[2]), int(m[3])
    m = re.match(r"^(\d{4})-(\d{2})$", t)
    if m:
        return int(m[1]), int(m[2]), None
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", t)
    if m:
        return int(m[3]), int(m[2]), int(m[1])
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})$", t)
    if m and m[2].lower() in _MONTHS:
        return int(m[3]), _MONTHS[m[2].lower()], int(m[1])
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{4})$", t)
    if m and m[1].lower() in _MONTHS:
        return int(m[2]), _MONTHS[m[1].lower()], None
    m = re.match(r"^(\d{4})$", t)
    if m:
        return int(m[1]), None, None
    return None


_ICD10 = re.compile(r"[A-TV-Z][0-9]{2}(?:\.[0-9A-Za-z]{1,4})?")
_RISK = re.compile(r"\b(IV|III|II|I)(a|b)?\b")


def risk_token(s: str) -> str:
    m = _RISK.search(str(s))
    if not m:
        return norm_text(s)
    return (m.group(1) + (m.group(2) or "")).lower()


def token_f1(gold: str, pred: str) -> float:
    g = re.findall(r"\w+", norm_text(gold))
    p = re.findall(r"\w+", norm_text(pred))
    if not g or not p:
        return 0.0
    gc, pc = defaultdict(int), defaultdict(int)
    for w in g:
        gc[w] += 1
    for w in p:
        pc[w] += 1
    common = sum(min(gc[w], pc[w]) for w in gc)
    if common == 0:
        return 0.0
    prec = common / len(p)
    rec = common / len(g)
    return 2 * prec * rec / (prec + rec)


def concept_tokens(s: str, synonyms: Dict[str, str]) -> Set[str]:
    raw = re.split(r"[;/,]", str(s))
    out = set()
    for tok in raw:
        n = norm_text(tok)
        if not n:
            continue
        out.add(synonyms.get(n, n))
    return out


def set_prf(gold: Set[str], pred: Set[str]) -> Dict[str, float]:
    if not gold and not pred:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "jaccard": 1.0}
    inter = gold & pred
    prec = len(inter) / len(pred) if pred else 0.0
    rec = len(inter) / len(gold) if gold else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    union = gold | pred
    jac = len(inter) / len(union) if union else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "jaccard": jac}


def cohen_kappa(gold: List[str], pred: List[str]) -> Optional[float]:
    n = len(gold)
    if n == 0:
        return None
    labels = sorted(set(gold) | set(pred))
    po = sum(1 for g, p in zip(gold, pred) if g == p) / n
    gc = {l: gold.count(l) / n for l in labels}
    pc = {l: pred.count(l) / n for l in labels}
    pe = sum(gc[l] * pc[l] for l in labels)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


# --------------------------------------------------------------------------
# BERTScore (batched, optional)
# --------------------------------------------------------------------------
def compute_bertscore(pairs: List[Tuple[str, str]], enabled: bool) -> Dict[Tuple[str, str], float]:
    """Map (candidate, reference) -> BERTScore-F1 for all unique pairs."""
    out: Dict[Tuple[str, str], float] = {}
    if not enabled or not pairs:
        return out
    try:
        from bert_score import score as bert_score_fn
    except Exception as exc:  # pragma: no cover
        print(f"  WARN: bert_score import failed ({exc}); using token-F1 only.")
        return out
    cands = [c for c, _ in pairs]
    refs = [r for _, r in pairs]
    try:
        _, _, f1 = bert_score_fn(cands, refs, lang="en", model_type="roberta-large",
                                 rescale_with_baseline=False, verbose=False)
    except Exception as exc:  # pragma: no cover
        print(f"  WARN: bert_score scoring failed ({exc}); using token-F1 only.")
        return out
    for (c, r), v in zip(pairs, f1.tolist()):
        out[(c, r)] = max(0.0, min(1.0, float(v)))
    return out


# --------------------------------------------------------------------------
# Loading, clustering, merging
# --------------------------------------------------------------------------
def add_ids(row: dict) -> Set[str]:
    return b1.normalize_ids(row.get("trial_registration_id", ""),
                            row.get("sources_publications", ""))


def merge_cluster(records: List[dict], columns: List[str]) -> dict:
    """Collapse cluster members into one value per column (informative non-empty)."""
    merged = {}
    for col in columns:
        vals = [str(r.get(col, "")) for r in records]
        non_empty = [v for v in vals if not is_empty(v)]
        merged[col] = max(non_empty, key=len) if non_empty else ""
    return merged


def build_studies(rows: List[dict], columns: List[str]) -> List[dict]:
    recs = []
    for row in rows:
        recs.append({**row, "ids": add_ids(row)})
    clusters = b1.cluster_records(recs)
    studies = []
    for cl in clusters:
        merged = merge_cluster(cl["records"], columns)
        studies.append({"ids": cl["ids"], "merged": merged,
                        "members": cl["records"]})
    return studies


def load_gt() -> Tuple[List[dict], List[str]]:
    data = json.loads(GT_PATH.read_text(encoding="utf-8"))
    cols = data["metadata"]["columns"]
    return build_studies(data["rows"], cols), cols


def load_model(path: Path, columns: List[str]) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return build_studies(data["rows"], columns)


# --------------------------------------------------------------------------
# Per-cell scoring
# --------------------------------------------------------------------------
def score_cell(col: str, cfg: dict, gold: str, pred: str,
               synonyms: Dict[str, str], bert: Dict[Tuple[str, str], float]) -> dict:
    """Score a both-present cell. Returns {'score': float, ...extra}."""
    metric = cfg["metric"]
    if metric == "exact_norm":
        return {"score": 1.0 if norm_text(gold) == norm_text(pred) else 0.0}
    if metric == "risk_class_norm":
        return {"score": 1.0 if risk_token(gold) == risk_token(pred) else 0.0}
    if metric == "name_norm_fuzzy":
        if norm_name(gold) == norm_name(pred):
            return {"score": 1.0, "fuzzy": 1.0, "exact": True}
        ratio = fuzzy_ratio(norm_name(gold), norm_name(pred))
        return {"score": ratio, "fuzzy": ratio, "exact": False}
    if metric == "int_exact":
        gi, pi = parse_int(gold), parse_int(pred)
        return {"score": 1.0 if (gi is not None and gi == pi) else 0.0,
                "gold": gi, "pred": pi}
    if metric == "float_tolerance":
        tol = cfg.get("tolerance", 0.05)
        gf, pf = parse_float(gold), parse_float(pred)
        if gf is None or pf is None:
            return {"score": 0.0, "gold": gf, "pred": pf}
        denom = abs(gf) if gf != 0 else 1.0
        rel = abs(gf - pf) / denom
        return {"score": 1.0 if rel <= tol else 0.0, "rel_error": rel,
                "gold": gf, "pred": pf}
    if metric == "iso_date":
        gd, pd = parse_date(gold), parse_date(pred)
        if gd is None:
            return {"score": 1.0 if norm_text(gold) == norm_text(pred) else 0.0,
                    "note": "gold_unparsed"}
        if pd is None:
            return {"score": 0.0, "note": "pred_unparsed"}
        ok = gd[0] == pd[0]
        if ok and gd[1] is not None:
            ok = gd[1] == pd[1]
        if ok and gd[2] is not None:
            ok = gd[2] == pd[2]
        return {"score": 1.0 if ok else 0.0, "gold": gd, "pred": pd}
    if metric == "icd10_set":
        g_strict = {c.upper() for c in _ICD10.findall(gold)}
        p_strict = {c.upper() for c in _ICD10.findall(pred)}
        g_rel = {c.split(".")[0] for c in g_strict}
        p_rel = {c.split(".")[0] for c in p_strict}
        strict = set_prf(g_strict, p_strict)
        relaxed = set_prf(g_rel, p_rel)
        return {"score": strict["f1"], "strict": strict, "relaxed": relaxed}
    if metric == "concept_set":
        g = concept_tokens(gold, synonyms)
        p = concept_tokens(pred, synonyms)
        res = set_prf(g, p)
        return {"score": res["f1"], "strict_exact":
                1.0 if norm_text(gold) == norm_text(pred) else 0.0,
                "gold_concepts": sorted(g), "pred_concepts": sorted(p)}
    if metric == "short_norm_tokenf1":
        if norm_text(gold) == norm_text(pred):
            return {"score": 1.0, "token_f1": 1.0}
        tf = token_f1(gold, pred)
        return {"score": tf, "token_f1": tf}
    if metric == "bertscore_tokenf1":
        tf = token_f1(gold, pred)
        bs = bert.get((pred, gold))
        return {"score": bs if bs is not None else tf,
                "bertscore_f1": bs, "token_f1": tf,
                "metric_used": "bertscore" if bs is not None else "token_f1"}
    return {"score": 0.0, "note": f"unknown_metric:{metric}"}


# --------------------------------------------------------------------------
# Main evaluation
# --------------------------------------------------------------------------
def _load_from_cache(col_cfg: dict) -> None:
    """Regenerate all aggregate reports from the per-cell scores already saved in
    results/*_field_scores.json, WITHOUT re-running any model or BERTScore.

    The per-cell scores in those files were computed once with BERTScore
    (roberta-large); reloading them preserves those exact free-text scores while
    letting us apply the corrected blank-cell framing. Only the empty-cell
    labeling/counting changed, none of which depends on BERTScore.
    """
    model_names = [m for m in MODELS if (RESULTS_DIR / f"{m}_field_scores.json").exists()]
    if not model_names:
        raise SystemExit("--from-cache: no results/*_field_scores.json found; "
                         "run once normally (with bert_score installed) first.")
    fd_raw = {m: json.loads((RESULTS_DIR / f"{m}_field_scores.json").read_text(encoding="utf-8"))
              for m in model_names}
    ref = model_names[0]
    n_studies = len(fd_raw[ref])
    # column order as stored in the cached detail
    scored_cols = list(fd_raw[ref][0]["fields"].keys()) if n_studies else []

    def _cls(old: str) -> str:
        # legacy artifacts labeled GT-blank/model-filled cells "hallucination"
        return "addition" if old == "hallucination" else old

    evaluable: List[dict] = []
    results: Dict[str, Dict[str, List[dict]]] = {m: {c: [] for c in scored_cols}
                                                 for m in model_names}
    field_detail: Dict[str, List[dict]] = {m: [] for m in model_names}
    for i in range(n_studies):
        label = fd_raw[ref][i]["study"]
        gt = {c: fd_raw[ref][i]["fields"][c]["gold"] for c in scored_cols}
        models_merged = {m: {c: fd_raw[m][i]["fields"][c]["pred"] for c in scored_cols}
                         for m in model_names}
        evaluable.append({"gt": gt, "ids": set(), "label": label,
                          "models": models_merged})
        for m in model_names:
            row = {"study": label, "fields": {}}
            for c in scored_cols:
                f = fd_raw[m][i]["fields"][c]
                cls = _cls(f["cls"])
                # score is only meaningful on gt_present cells; leave others None
                score = f.get("score") if cls in ("scored", "omission") else None
                if cls == "omission":
                    score = 0.0
                results[m][c].append({"cls": cls, "score": score})
                row["fields"][c] = {"gold": f["gold"], "pred": f["pred"],
                                    "cls": cls, "score": score}
            field_detail[m].append(row)

    dropped_path = RESULTS_DIR / "dropped_studies.json"
    dropped = json.loads(dropped_path.read_text(encoding="utf-8")) if dropped_path.exists() else []
    prev_path = RESULTS_DIR / "benchmark_2_results.json"
    prev = json.loads(prev_path.read_text(encoding="utf-8")) if prev_path.exists() else {}
    n_gt = prev.get("counts", {}).get("gt_studies", len(evaluable) + len(dropped))
    bert_active = prev.get("bertscore_active", True)
    gt_studies = [None] * n_gt  # only len() is used downstream

    _finalize(model_names, scored_cols, col_cfg, evaluable, results, field_detail,
              dropped, gt_studies, bert_active, use_bert=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-bertscore", action="store_true",
                    help="Skip BERTScore; use token-level F1 for free text.")
    ap.add_argument("--from-cache", action="store_true",
                    help="Rebuild all reports from results/*_field_scores.json "
                         "without re-running models or BERTScore (preserves the "
                         "original free-text scores; only re-aggregates).")
    args = ap.parse_args()
    use_bert = not args.no_bertscore

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    col_cfg = cfg["columns"]
    synonyms = {k: v for k, v in cfg["category_synonyms"].items() if k != "_comment"}
    study_level = cfg["study_level_fields"]

    if args.from_cache:
        _load_from_cache(col_cfg)
        return

    gt_studies, columns = load_gt()
    scored_cols = [c for c in columns if col_cfg.get(c, {}).get("metric") != "skip"
                   and c in col_cfg]

    model_studies = {m: load_model(p, columns) for m, p in MODELS.items()
                     if p.exists()}

    # index each model's studies by identifier
    model_index: Dict[str, Dict[str, List[int]]] = {}
    for m, studies in model_studies.items():
        idx: Dict[str, List[int]] = defaultdict(list)
        for i, st in enumerate(studies):
            for ident in st["ids"]:
                idx[ident].append(i)
        model_index[m] = idx

    def match_model(model: str, gt_ids: Set[str]) -> Optional[dict]:
        hits: Set[int] = set()
        for ident in gt_ids:
            hits |= set(model_index[model].get(ident, []))
        if not hits:
            return None
        members = [r for i in hits for r in model_studies[model][i]["members"]]
        return merge_cluster(members, columns)

    # ---- build evaluable set, with drop reasons ----
    evaluable: List[dict] = []  # {gt_merged, ids, models:{model:merged}}
    dropped: List[dict] = []
    for st in gt_studies:
        gt = st["merged"]
        ids = st["ids"]
        filled = any(not is_empty(gt.get(c, "")) for c in study_level)
        ref_match = match_model(REFERENCE_MODEL, ids) if REFERENCE_MODEL in model_studies else None
        label = f"{gt.get('dtx_name', '')} [{b1.short_ids(ids)}]"
        if not filled:
            dropped.append({"study": label, "dtx_name": gt.get("dtx_name", ""),
                            "ids": sorted(ids), "reason": "unfilled_gt"})
            continue
        if ref_match is None:
            dropped.append({"study": label, "dtx_name": gt.get("dtx_name", ""),
                            "ids": sorted(ids), "reason": "not_found_by_pipeline"})
            continue
        models_merged = {m: match_model(m, ids) or {} for m in model_studies}
        evaluable.append({"gt": gt, "ids": ids, "label": label,
                          "models": models_merged})

    # ---- collect free-text pairs for one batched BERTScore call ----
    pairs: Set[Tuple[str, str]] = set()
    for ev in evaluable:
        for col in scored_cols:
            if col_cfg[col]["metric"] != "bertscore_tokenf1":
                continue
            g = ev["gt"].get(col, "")
            if is_empty(g):
                continue
            for m in model_studies:
                p = ev["models"][m].get(col, "")
                if not is_empty(p):
                    pairs.add((p, g))
    bert = compute_bertscore(sorted(pairs), use_bert)
    bert_active = bool(bert)

    # ---- score every cell ----
    # results[model][col] = list of cell dicts
    results: Dict[str, Dict[str, List[dict]]] = {
        m: {c: [] for c in scored_cols} for m in model_studies}
    field_detail: Dict[str, List[dict]] = {m: [] for m in model_studies}

    for ev in evaluable:
        for m in model_studies:
            mrec = ev["models"][m]
            row_detail = {"study": ev["label"], "fields": {}}
            for col in scored_cols:
                g = ev["gt"].get(col, "")
                p = mrec.get(col, "")
                ge, pe = is_empty(g), is_empty(p)
                if ge and pe:
                    # both blank: the model correctly agreed there was nothing
                    cell = {"cls": "both_empty", "score": None}
                elif ge and not pe:
                    # GT blank, model filled it. The GT is a deliberately small
                    # manual subset and is known to be incomplete, so this is an
                    # *unverified addition* (a candidate value that needs a source
                    # check), NOT a scored error. Consistent with how Benchmark 1
                    # treats unmatched pipeline studies ("Extras", not false
                    # positives). It is counted, never scored as 0.
                    cell = {"cls": "addition", "score": None}
                elif not ge and pe:
                    # GT has a value, model left it blank: a genuine omission.
                    cell = {"cls": "omission", "score": 0.0}
                else:
                    sc = score_cell(col, col_cfg[col], g, p, synonyms, bert)
                    cell = {"cls": "scored", **sc}
                results[m][col].append(cell)
                row_detail["fields"][col] = {
                    "gold": g, "pred": p, "cls": cell["cls"],
                    "score": (None if cell["score"] is None
                              else round(cell["score"], 4))}
            field_detail[m].append(row_detail)

    # ---- aggregate + write ----
    _finalize(list(model_studies.keys()), scored_cols, col_cfg, evaluable,
              results, field_detail, dropped, gt_studies, bert_active, use_bert)


def _agg_model(model: str, results: Dict[str, Dict[str, List[dict]]],
               scored_cols: List[str], col_cfg: dict,
               evaluable: List[dict]) -> dict:
    per_col = {}
    for col in scored_cols:
        cells = results[model][col]
        n = len(cells)
        # cells where the GT actually has a value = the population we score
        gt_present = [c for c in cells if c["cls"] in ("scored", "omission")]
        scored = [c for c in cells if c["cls"] == "scored"]
        n_gt_present = len(gt_present)  # omission + scored
        n_gt_empty = sum(1 for c in cells if c["cls"] in ("both_empty", "addition"))
        omissions = sum(1 for c in cells if c["cls"] == "omission")
        additions = sum(1 for c in cells if c["cls"] == "addition")
        both_empty = sum(1 for c in cells if c["cls"] == "both_empty")
        # score is defined only on gt_present cells (omission = 0, scored = metric)
        score_gt_present = (sum(c["score"] for c in gt_present) / n_gt_present
                            if n_gt_present else None)
        score_scored = (sum(c["score"] for c in scored) / len(scored)
                        if scored else None)
        model_filled = len(scored) + additions  # descriptive fill count
        entry = {
            "n_cells": n,
            "n_scored": len(scored),
            "n_gt_present": n_gt_present,
            "n_gt_empty": n_gt_empty,
            "both_empty": both_empty,
            "omissions": omissions,
            # GT blank + model filled: candidate additions, reported as counts,
            # never scored as errors (see methodology.md, blank handling).
            "unverified_additions": additions,
            "omission_rate": omissions / n_gt_present if n_gt_present else None,
            # rate over GT-blank cells only when there are enough of them,
            # to avoid misleading percentages over n=1..few cells.
            "addition_rate": (additions / n_gt_empty
                              if n_gt_empty >= MIN_RATE_DENOM else None),
            "model_fill_rate": model_filled / n if n else None,
            "score_gt_present": score_gt_present,
            "score_scored_only": score_scored,
            "metric": col_cfg[col]["metric"],
        }
        # chance-corrected agreement for single-label categorical columns
        if col_cfg[col]["metric"] in ("exact_norm", "risk_class_norm"):
            entry["cohen_kappa"] = _kappa_for_col(model, col, col_cfg[col],
                                                  evaluable)
        per_col[col] = entry

    present_scores = [v["score_gt_present"] for v in per_col.values()
                      if v["score_gt_present"] is not None]
    macro = sum(present_scores) / len(present_scores) if present_scores else 0.0
    # micro: weight by n_gt_present
    num = sum((v["score_gt_present"] or 0) * v["n_gt_present"]
              for v in per_col.values())
    den = sum(v["n_gt_present"] for v in per_col.values())
    micro = num / den if den else 0.0
    return {"macro_score_gt_present": macro, "micro_score_gt_present": micro,
            "per_column": per_col}


def _finalize(model_names: List[str], scored_cols: List[str], col_cfg: dict,
              evaluable: List[dict], results: Dict[str, Dict[str, List[dict]]],
              field_detail: Dict[str, List[dict]], dropped: List[dict],
              gt_studies: List[dict], bert_active: bool, use_bert: bool) -> None:
    summary = {m: _agg_model(m, results, scored_cols, col_cfg, evaluable)
               for m in model_names}
    model_studies = {m: None for m in model_names}  # only keys are used downstream
    _write_outputs(summary, results, field_detail, evaluable, dropped, gt_studies,
                   scored_cols, col_cfg, model_studies, bert_active, use_bert)
    print(f"GT studies: {len(gt_studies)} | evaluable: {len(evaluable)} | "
          f"dropped: {len(dropped)} "
          f"(unfilled={sum(1 for d in dropped if d['reason']=='unfilled_gt')}, "
          f"not_found={sum(1 for d in dropped if d['reason']=='not_found_by_pipeline')})")
    print(f"BERTScore active: {bert_active}")
    for m, s in summary.items():
        print(f"  {m}: macro(GT-present)={s['macro_score_gt_present']*100:.1f}%  "
              f"micro={s['micro_score_gt_present']*100:.1f}%")
    print(f"Results written to {RESULTS_DIR}")


def _kappa_for_col(model: str, col: str, cfg: dict, evaluable: List[dict]) -> Optional[float]:
    gold_lab, pred_lab = [], []
    for ev in evaluable:
        g = ev["gt"].get(col, "")
        p = ev["models"][model].get(col, "")
        gl = "<EMPTY>" if is_empty(g) else (risk_token(g) if cfg["metric"] == "risk_class_norm" else norm_text(g))
        pl = "<EMPTY>" if is_empty(p) else (risk_token(p) if cfg["metric"] == "risk_class_norm" else norm_text(p))
        gold_lab.append(gl)
        pred_lab.append(pl)
    return cohen_kappa(gold_lab, pred_lab)


# --------------------------------------------------------------------------
# Output rendering
# --------------------------------------------------------------------------
def pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def _write_outputs(summary, results, field_detail, evaluable, dropped, gt_studies,
                   scored_cols, col_cfg, model_studies, bert_active, use_bert):
    # machine-readable
    (RESULTS_DIR / "benchmark_2_results.json").write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bertscore_active": bert_active,
            "counts": {
                "gt_studies": len(gt_studies),
                "evaluable": len(evaluable),
                "dropped_unfilled": sum(1 for d in dropped if d["reason"] == "unfilled_gt"),
                "dropped_not_found": sum(1 for d in dropped if d["reason"] == "not_found_by_pipeline"),
            },
            "models": summary,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    # dropped studies
    (RESULTS_DIR / "dropped_studies.json").write_text(
        json.dumps(dropped, indent=2, ensure_ascii=False), encoding="utf-8")
    dl = ["# Dropped ground-truth studies", "",
          "Studies excluded from Benchmark 2, with reason. `unfilled_gt` = the GT "
          "row had no analysed study-level values; `not_found_by_pipeline` = no "
          "matching pipeline study (cannot score analysis without a model output).",
          ""]
    for reason in ("unfilled_gt", "not_found_by_pipeline"):
        items = [d for d in dropped if d["reason"] == reason]
        dl.append(f"## {reason} ({len(items)})")
        dl.append("")
        dl.append("| DTx | Identifiers |")
        dl.append("|---|---|")
        for d in items:
            dl.append(f"| {d['dtx_name']} | {', '.join(d['ids']) or '(none)'} |")
        dl.append("")
    (RESULTS_DIR / "dropped_studies.md").write_text("\n".join(dl), encoding="utf-8")

    # per-model field detail
    for m in model_studies:
        (RESULTS_DIR / f"{m}_field_scores.json").write_text(
            json.dumps(field_detail[m], indent=2, ensure_ascii=False), encoding="utf-8")

    # outcomes spot-check (~ all evaluable rows for key_outcomes_findings)
    spot_path = RESULTS_DIR / "outcomes_spotcheck.csv"
    with spot_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        header = ["study", "gt_key_outcomes_findings"] + \
                 [f"{m}__key_outcomes_findings" for m in model_studies] + \
                 [f"{m}__score" for m in model_studies]
        w.writerow(header)
        for i, ev in enumerate(evaluable):
            g = ev["gt"].get("key_outcomes_findings", "")
            if is_empty(g):
                continue
            row = [ev["label"], g]
            for m in model_studies:
                row.append(ev["models"][m].get("key_outcomes_findings", ""))
            for m in model_studies:
                cell = results[m]["key_outcomes_findings"][i]
                row.append(round(cell["score"], 3))
            w.writerow(row)

    # markdown report
    _write_markdown(summary, evaluable, dropped, gt_studies, scored_cols,
                    col_cfg, model_studies, bert_active, use_bert)


def _write_markdown(summary, evaluable, dropped, gt_studies, scored_cols,
                    col_cfg, model_studies, bert_active, use_bert):
    models = list(model_studies.keys())
    L: List[str] = []
    L.append("# Benchmark 2 - Field-level extraction quality (results)")
    L.append("")
    L.append(f"_Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    L.append("")
    L.append("Per-field accuracy of each model's Phase 3 analysis vs. the manual "
             "ground truth, scored with data-type-appropriate metrics "
             "(see [`../methodology.md`](../methodology.md)).")
    L.append("")
    n_unf = sum(1 for d in dropped if d["reason"] == "unfilled_gt")
    n_nf = sum(1 for d in dropped if d["reason"] == "not_found_by_pipeline")
    L.append("## Dataset")
    L.append("")
    L.append(f"- GT studies (after clustering): **{len(gt_studies)}**.")
    L.append(f"- Evaluable (filled GT and found by pipeline): **{len(evaluable)}**.")
    L.append(f"- Dropped: **{len(dropped)}** - {n_unf} `unfilled_gt`, "
             f"{n_nf} `not_found_by_pipeline` "
             f"(see [`dropped_studies.md`](dropped_studies.md)).")
    L.append(f"- BERTScore active: **{bert_active}** "
             f"({'roberta-large' if bert_active else 'fallback: token-level F1'}).")
    L.append("")
    L.append("## Headline (per model)")
    L.append("")
    L.append("Macro-average over columns of the per-column score on cells where the "
             "GT has a value (omissions count as 0; both-empty cells and "
             "GT-blank/model-filled additions are excluded, not scored). "
             "Micro-average weights by number of GT-present cells.")
    L.append("")
    L.append("| Model | Macro (GT-present) | Micro (GT-present) |")
    L.append("|---|--:|--:|")
    for m in models:
        s = summary[m]
        L.append(f"| {m} | {pct(s['macro_score_gt_present'])} | "
                 f"{pct(s['micro_score_gt_present'])} |")
    L.append("")
    # best per metric note
    L.append("## Per-column score (GT-present) by model")
    L.append("")
    L.append("| Column | Metric | " + " | ".join(models) + " |")
    L.append("|---|---|" + "|".join(["--:"] * len(models)) + "|")
    for col in scored_cols:
        metric = col_cfg[col]["metric"]
        cells = " | ".join(pct(summary[m]["per_column"][col]["score_gt_present"])
                           for m in models)
        L.append(f"| {col} | {metric} | {cells} |")
    L.append("")
    L.append("## Omissions and unverified additions by model")
    L.append("")
    L.append("**Omission** = GT has a value, model left it blank (a genuine miss; "
             "scored as 0 in the GT-present score). **Unverified addition** = GT "
             "blank, model filled it. Because the analysis GT is a deliberately "
             "small, incomplete manual subset, an addition is a *candidate* value "
             "that needs a source check, not an automatic error - so it is reported "
             "as a count and is never scored (mirrors Benchmark 1 'Extras'). The "
             "addition rate is shown only where at least "
             f"{MIN_RATE_DENOM} GT-blank cells exist (`-` otherwise), because a "
             "percentage over one or two cells is noise.")
    L.append("")
    for m in models:
        L.append(f"### {m}")
        L.append("")
        L.append("| Column | Omissions (of GT-present) | Omission rate | "
                 "Unverified additions (of GT-blank) | Addition rate |")
        L.append("|---|--:|--:|--:|--:|")
        for col in scored_cols:
            e = summary[m]["per_column"][col]
            L.append(f"| {col} | {e['omissions']}/{e['n_gt_present']} | "
                     f"{pct(e['omission_rate'])} | "
                     f"{e['unverified_additions']}/{e['n_gt_empty']} | "
                     f"{pct(e['addition_rate'])} |")
        L.append("")
    # categorical kappa
    L.append("## Categorical agreement (Cohen's kappa)")
    L.append("")
    L.append("| Column | " + " | ".join(models) + " |")
    L.append("|---|" + "|".join(["--:"] * len(models)) + "|")
    for col in scored_cols:
        if "cohen_kappa" not in summary[models[0]]["per_column"][col]:
            continue
        vals = " | ".join(
            (lambda k: "-" if k is None else f"{k:.2f}")(
                summary[m]["per_column"][col].get("cohen_kappa"))
            for m in models)
        L.append(f"| {col} | {vals} |")
    L.append("")
    L.append("## Notes")
    L.append("")
    L.append("- Free-text columns use BERTScore-F1 (primary) with token-level F1 "
             "as cross-check; a sample is exported to "
             "[`outcomes_spotcheck.csv`](outcomes_spotcheck.csv) for the manual "
             "negation/number review BERTScore can miss.")
    L.append("- `category` uses canonical-concept set overlap (vocabularies "
             "differ between GT and models); see methodology limitations.")
    L.append("- Per-study per-field detail is in `<model>_field_scores.json`.")
    L.append("")
    (RESULTS_DIR / "benchmark_2_results.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
