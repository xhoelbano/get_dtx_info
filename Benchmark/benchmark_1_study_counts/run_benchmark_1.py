#!/usr/bin/env python3
"""Benchmark 1 - Study detection (counts).

Compares the studies discovered automatically by the pipeline (Phase 3 combined
outputs) against the manually curated ground-truth study list, using
deterministic identifier-based record linkage.

Method (see ../methodology.md):
  1. Extract & normalize public identifiers (NCT/DRKS/ISRCTN/EUCTR/PMID/PMC/DOI)
     from each study's `trial_registration_id` and `sources_publications`.
  2. Collapse records that share an identifier into one study cluster
     (connected components) so a protocol + its later publication count once.
  3. Match a GT study to a pipeline study iff their identifier sets intersect.
  4. Report TP (matched), FN (missed), Extras (pipeline studies with no GT
     match, flagged for manual adjudication), plus Recall / Precision / F1 /
     Jaccard, in two scopes (full GT and covered-DTx subset).

Run from the repository root:
    python Benchmark/benchmark_1_study_counts/run_benchmark_1.py
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
RESULTS_DIR = SCRIPT_DIR / "results"
CROSSWALK_PATH = SCRIPT_DIR / "dtx_crosswalk.json"
GT_PATH = REPO_ROOT / "Test_Datasets" / "test_dataset_benchmarking_numbers.json"

MODELS: Dict[str, Path] = {
    "gpt-4o": REPO_ROOT / "Phase_3_Evidence_Analysis/gpt-4o/Website_Search_ON/phase3_combined.json",
    "gemini-3.1-pro-preview": REPO_ROOT / "Phase_3_Evidence_Analysis/gemini-3.1-pro-preview/Website_Search_ON/phase3_combined.json",
    "claude-sonnet-4-6": REPO_ROOT / "Phase_3_Evidence_Analysis/claude-sonnet-4-6/Website_Search_ON/phase3_combined.json",
}

# --------------------------------------------------------------------------
# Identifier extraction & normalization (deterministic regex)
# --------------------------------------------------------------------------
_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("NCT", re.compile(r"NCT\d{8}", re.IGNORECASE)),
    ("DRKS", re.compile(r"DRKS\d{8}", re.IGNORECASE)),
    ("ISRCTN", re.compile(r"ISRCTN\d{8}", re.IGNORECASE)),
    ("EUCTR", re.compile(r"\b\d{4}-\d{6}-\d{2}\b")),
    ("PMC", re.compile(r"PMC\d+", re.IGNORECASE)),
    ("DOI", re.compile(r"10\.\d{4,9}/[^\s,;\)\]\"<>]+")),
]
_PUBMED_URL = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", re.IGNORECASE)


def normalize_ids(trial_registration_id: str, sources_publications: str) -> Set[str]:
    """Return the normalized identifier set for one study record."""
    reg = (trial_registration_id or "").strip()
    text = f"{reg}\n{sources_publications or ''}"
    ids: Set[str] = set()

    for label, pat in _PATTERNS:
        for m in pat.findall(text):
            token = m.strip().rstrip(".,);]'\"")
            if label == "DOI":
                ids.add(f"DOI:{token.lower()}")
            elif label == "EUCTR":
                ids.add(f"EUCTR:{token}")
            elif label == "PMC":
                ids.add(token.upper())
            else:  # NCT / DRKS / ISRCTN
                ids.add(token.upper())

    # PMIDs from PubMed URLs
    for m in _PUBMED_URL.findall(text):
        ids.add(f"PMID:{int(m)}")

    # A bare all-digit trial_registration_id is a PMID
    if reg.isdigit():
        ids.add(f"PMID:{int(reg)}")

    return ids


# --------------------------------------------------------------------------
# Union-find (connected components for study clustering)
# --------------------------------------------------------------------------
class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_records(records: List[dict]) -> List[dict]:
    """Group records sharing >=1 identifier into study clusters.

    Each record must have an 'ids' set. Returns a list of clusters, each a dict
    with the union 'ids' and the member 'records'.
    """
    uf = UnionFind(len(records))
    id_to_first: Dict[str, int] = {}
    for i, rec in enumerate(records):
        for ident in rec["ids"]:
            if ident in id_to_first:
                uf.union(id_to_first[ident], i)
            else:
                id_to_first[ident] = i

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(records)):
        groups[uf.find(i)].append(i)

    clusters: List[dict] = []
    for members in groups.values():
        union_ids: Set[str] = set()
        recs = [records[i] for i in members]
        for r in recs:
            union_ids |= r["ids"]
        clusters.append({"ids": union_ids, "records": recs})
    return clusters


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def load_gt() -> List[dict]:
    """One entry per GT study row, with its identifier set."""
    data = json.loads(GT_PATH.read_text(encoding="utf-8"))
    studies = []
    for row in data["rows"]:
        ids = normalize_ids(row.get("trial_registration_id", ""),
                            row.get("sources_publications", ""))
        studies.append({
            "dtx_name": row.get("dtx_name", ""),
            "evidence_type": (row.get("evidence_type", "") or "").strip(),
            "trial_registration_id": row.get("trial_registration_id", ""),
            "sources_publications": row.get("sources_publications", ""),
            "ids": ids,
        })
    return studies


def load_pipeline(path: Path) -> List[dict]:
    """One record per pipeline row (aligned with its audit entry)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    audit = data.get("audit", [{}] * len(rows))
    records = []
    for row, aud in zip(rows, audit):
        ids = normalize_ids(row.get("trial_registration_id", ""),
                            row.get("sources_publications", ""))
        records.append({
            "country": aud.get("country", ""),
            "slug": aud.get("dtx_slug", ""),
            "dtx_name": row.get("dtx_name", ""),
            "evidence_type": (row.get("evidence_type", "") or "").strip(),
            "trial_registration_id": row.get("trial_registration_id", ""),
            "sources_publications": row.get("sources_publications", ""),
            "ids": ids,
        })
    return records


def load_crosswalk() -> dict:
    return json.loads(CROSSWALK_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Core evaluation for one model
# --------------------------------------------------------------------------
def short_ids(ids: Set[str]) -> str:
    """Compact, stable, registry-first label for an identifier set."""
    order = {"NCT": 0, "DRKS": 1, "ISRCTN": 2, "EUCTR": 3, "PMID": 4, "PMC": 5, "DOI": 6}
    def key(i: str) -> Tuple[int, str]:
        pref = i.split(":")[0] if ":" in i else re.match(r"[A-Z]+", i).group(0)
        return (order.get(pref, 9), i)
    return ", ".join(sorted(ids, key=key)) if ids else "(no identifier)"


def evaluate(gt_studies: List[dict], pipeline_records: List[dict],
             crosswalk: dict) -> dict:
    covered_gt_names = set(crosswalk["covered"].keys())
    # reverse map (country, slug) -> GT name, for attributing pipeline clusters
    rev: Dict[Tuple[str, str], str] = {}
    for gt_name, groups in crosswalk["covered"].items():
        for g in groups:
            rev[(g["country"], g["slug"])] = gt_name

    pipe_clusters = cluster_records(pipeline_records)

    # index pipeline clusters by identifier for O(1) matching
    id_to_clusters: Dict[str, Set[int]] = defaultdict(set)
    for ci, cl in enumerate(pipe_clusters):
        for ident in cl["ids"]:
            id_to_clusters[ident].add(ci)

    matched_cluster_idx: Set[int] = set()
    gt_results: List[dict] = []
    for g in gt_studies:
        hits: Set[int] = set()
        for ident in g["ids"]:
            hits |= id_to_clusters.get(ident, set())
        matched = bool(hits)
        if matched:
            matched_cluster_idx |= hits
        # pipeline evidence types for matched clusters
        pipe_types = sorted({r["evidence_type"] for ci in hits
                             for r in pipe_clusters[ci]["records"]})
        gt_results.append({
            "dtx_name": g["dtx_name"],
            "evidence_type": g["evidence_type"],
            "ids": g["ids"],
            "matched": matched,
            "pipeline_types": pipe_types,
        })

    # extras = pipeline clusters matching no GT study
    extras = []
    for ci, cl in enumerate(pipe_clusters):
        if ci in matched_cluster_idx:
            continue
        rec0 = cl["records"][0]
        dtx_names = sorted({r["dtx_name"] for r in cl["records"]})
        gt_attr = sorted({rev.get((r["country"], r["slug"]), "")
                         for r in cl["records"]} - {""})
        extras.append({
            "ids": cl["ids"],
            "pipeline_dtx": dtx_names,
            "gt_dtx": gt_attr,
            "evidence_types": sorted({r["evidence_type"] for r in cl["records"]}),
            "sources": sorted({r["sources_publications"] for r in cl["records"]}),
        })

    def metrics(tp: int, fn: int, ex: int) -> dict:
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        precision = tp / (tp + ex) if (tp + ex) else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        return {"tp": tp, "fn": fn, "extras": ex,
                "recall": recall, "precision": precision, "f1": f1}

    # full scope (all GT studies)
    tp_full = sum(1 for r in gt_results if r["matched"])
    fn_full = sum(1 for r in gt_results if not r["matched"])
    # covered subset (GT studies whose DTx was attempted by the pipeline)
    cov = [r for r in gt_results if r["dtx_name"] in covered_gt_names]
    tp_cov = sum(1 for r in cov if r["matched"])
    fn_cov = sum(1 for r in cov if not r["matched"])

    ex = len(extras)
    return {
        "n_pipeline_clusters": len(pipe_clusters),
        "n_pipeline_rows": len(pipeline_records),
        "full": metrics(tp_full, fn_full, ex),
        "covered": metrics(tp_cov, fn_cov, ex),
        "gt_results": gt_results,
        "extras": extras,
        "pipe_clusters": pipe_clusters,
    }


# --------------------------------------------------------------------------
# Per-DTx table
# --------------------------------------------------------------------------
def per_dtx_table(gt_studies: List[dict], ev: dict, crosswalk: dict) -> List[dict]:
    covered = crosswalk["covered"]
    rev: Dict[Tuple[str, str], str] = {}
    for gt_name, groups in covered.items():
        for g in groups:
            rev[(g["country"], g["slug"])] = gt_name

    # GT studies grouped by DTx (preserve first-seen order)
    gt_by_dtx: Dict[str, List[dict]] = defaultdict(list)
    order: List[str] = []
    for r in ev["gt_results"]:
        if r["dtx_name"] not in gt_by_dtx:
            order.append(r["dtx_name"])
        gt_by_dtx[r["dtx_name"]].append(r)

    # pipeline clusters attributed per GT DTx
    pipe_by_dtx: Dict[str, list] = defaultdict(list)
    for cl in ev["pipe_clusters"]:
        names = {rev.get((r["country"], r["slug"]), "") for r in cl["records"]}
        for nm in names - {""}:
            pipe_by_dtx[nm].append(cl)

    rows = []
    for name in order:
        gts = gt_by_dtx[name]
        n_gt = len(gts)
        matched = sum(1 for r in gts if r["matched"])
        missed = [short_ids(r["ids"]) for r in gts if not r["matched"]]
        n_pipe = len(pipe_by_dtx.get(name, []))
        covered_flag = name in covered
        # Jaccard on study sets: |intersection| / |union|
        union = n_gt + n_pipe - matched
        jacc = matched / union if union else 0.0
        rows.append({
            "dtx_name": name,
            "covered": covered_flag,
            "gt_studies": n_gt,
            "pipeline_studies": n_pipe,
            "matched": matched,
            "missed_ids": missed,
            "jaccard": jacc,
        })
    return rows


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def build_markdown(all_results: Dict[str, dict], per_dtx: Dict[str, List[dict]],
                   gt_studies: List[dict], crosswalk: dict) -> str:
    n_gt = len(gt_studies)
    n_gt_dtx = len({g["dtx_name"] for g in gt_studies})
    n_cov = len(crosswalk["covered"])
    n_unc = len(crosswalk["uncovered"])
    gt_cov_studies = sum(1 for g in gt_studies
                        if g["dtx_name"] in crosswalk["covered"])

    L: List[str] = []
    L.append("# Benchmark 1 - Study detection (counts)")
    L.append("")
    L.append(f"_Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    L.append("")
    L.append("Automated pipeline study detection vs. the manually curated "
             "ground truth, using deterministic identifier-based record linkage "
             "(see [`../../methodology.md`](../../methodology.md)).")
    L.append("")
    L.append("## Dataset overview")
    L.append("")
    L.append(f"- Ground-truth studies: **{n_gt}** across **{n_gt_dtx}** DTx.")
    L.append(f"- DTx for which the pipeline returned at least one verified study "
             f"(evaluable / 'covered'): **{n_cov}** "
             f"({gt_cov_studies} GT studies).")
    L.append(f"- DTx for which the pipeline found **zero** verified studies "
             f"(the evidence search ran but no candidate passed verification, so "
             f"there was nothing to evaluate): **{n_unc}** "
             f"({n_gt - gt_cov_studies} GT studies) - "
             f"{', '.join(crosswalk['uncovered'])}.")
    L.append("")
    L.append("All three models analyze the **same** Phase 2 evidence set "
             "(extracted with gpt-4o, Website Search/Browser Use ON), so the "
             "study set is identical across models; running all three is a "
             "determinism check.")
    L.append("")

    # Headline metrics
    L.append("## Headline results")
    L.append("")
    L.append("**Full GT scope** (all DTx; DTx with zero verified pipeline "
             "studies contribute only misses):")
    L.append("")
    L.append("| Model | Pipeline studies | TP (matched) | FN (missed) | Extras | Recall | Precision* | F1 |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for model, ev in all_results.items():
        f = ev["full"]
        L.append(f"| {model} | {ev['n_pipeline_clusters']} | {f['tp']} | "
                 f"{f['fn']} | {f['extras']} | {pct(f['recall'])} | "
                 f"{pct(f['precision'])} | {pct(f['f1'])} |")
    L.append("")
    L.append("**Covered-subset scope** (only the DTx for which the pipeline "
             "returned at least one verified study):")
    L.append("")
    L.append("| Model | TP (matched) | FN (missed) | Extras | Recall | Precision* | F1 |")
    L.append("|---|--:|--:|--:|--:|--:|--:|")
    for model, ev in all_results.items():
        c = ev["covered"]
        L.append(f"| {model} | {c['tp']} | {c['fn']} | {c['extras']} | "
                 f"{pct(c['recall'])} | {pct(c['precision'])} | {pct(c['f1'])} |")
    L.append("")
    L.append("\\* Precision is a **corroboration rate** (share of pipeline "
             "studies confirmed by the GT) and a lower bound until the Extras "
             "are adjudicated; see the Extras section.")
    L.append("")

    # Per-DTx (use first model as representative; note identity)
    rep_model = next(iter(all_results))
    L.append(f"## Per-DTx breakdown")
    L.append("")
    L.append(f"Identifier-based matching is identical across the three models "
             f"(same evidence set); the table below is representative "
             f"(`{rep_model}`). `Jaccard` = |GT ∩ Pipeline| / |GT ∪ Pipeline| "
             f"on study sets.")
    L.append("")
    L.append("| DTx | Pipeline evidence | GT | Pipeline | Matched | Missed | Jaccard |")
    L.append("|---|:--:|--:|--:|--:|--:|--:|")
    for r in per_dtx[rep_model]:
        cov = "yes" if r["covered"] else "none found"
        L.append(f"| {r['dtx_name']} | {cov} | {r['gt_studies']} | "
                 f"{r['pipeline_studies']} | {r['matched']} | "
                 f"{len(r['missed_ids'])} | {pct(r['jaccard'])} |")
    L.append("")

    # Missed studies list
    L.append("## Missed ground-truth studies (false negatives)")
    L.append("")
    L.append("Studies present in the GT but not recovered by the pipeline. "
             "Where 'Pipeline evidence' is 'none found', the pipeline processed "
             "the DTx but verified zero studies, so every GT study for it is "
             "necessarily a miss (there was nothing to evaluate); where it is "
             "'yes', the miss is a genuine extraction/matching failure within a "
             "DTx that did yield verified evidence.")
    L.append("")
    L.append("| DTx | Pipeline evidence | GT identifiers | Evidence type |")
    L.append("|---|:--:|---|:--:|")
    for r in all_results[rep_model]["gt_results"]:
        if r["matched"]:
            continue
        cov = "yes" if r["dtx_name"] in crosswalk["covered"] else "none found"
        L.append(f"| {r['dtx_name']} | {cov} | {short_ids(r['ids'])} | "
                 f"{r['evidence_type'] or '-'} |")
    L.append("")

    # Extras / adjudication
    L.append("## Extras requiring adjudication (pipeline studies with no GT match)")
    L.append("")
    L.append("These pipeline studies did not match any GT study. They are **not** "
             "automatically wrong: the manual GT may be incomplete. Review each "
             "to decide whether it is a genuine extra study (GT gap) or a "
             "pipeline over-extraction / wrong hit.")
    L.append("")
    L.append("| DTx (pipeline) | GT DTx | Identifiers | Evidence type | Source |")
    L.append("|---|---|---|:--:|---|")
    for x in all_results[rep_model]["extras"]:
        dtx = ", ".join(x["pipeline_dtx"]) or "-"
        gtd = ", ".join(x["gt_dtx"]) or "-"
        etypes = ", ".join(x["evidence_types"]) or "-"
        src = " ; ".join(x["sources"][:2]) or "-"
        L.append(f"| {dtx} | {gtd} | {short_ids(x['ids'])} | {etypes} | {src} |")
    L.append("")

    # Cross-model note
    L.append("## Cross-model comparison")
    L.append("")
    identical = len({(ev["full"]["tp"], ev["full"]["fn"], ev["full"]["extras"])
                     for ev in all_results.values()}) == 1
    if identical:
        L.append("All three models produce **identical** detection counts, as "
                 "expected: Benchmark 1 measures the shared Phase 2 evidence "
                 "extraction, not the per-model Phase 3 analysis. The numbers "
                 "above therefore characterize the extraction pipeline itself.")
    else:
        L.append("The models differ in detection counts (see tables above), "
                 "which is unexpected given the shared evidence set and warrants "
                 "inspection of the differing rows.")
    L.append("")
    L.append("## Interpretation")
    L.append("")
    f = all_results[rep_model]["full"]
    c = all_results[rep_model]["covered"]
    L.append(f"- **End-to-end recall** (full GT) is {pct(f['recall'])}: the "
             f"pipeline recovered {f['tp']} of {f['tp'] + f['fn']} curated "
             f"studies. The gap is dominated by the {n_unc} DTx for which the "
             f"pipeline found zero verified studies (no evidence to evaluate).")
    L.append(f"- **Recall on DTx that yielded evidence** rises to "
             f"{pct(c['recall'])} ({c['tp']}/{c['tp'] + c['fn']}), isolating "
             f"extraction/matching quality from the zero-evidence DTx.")
    L.append(f"- **Extras**: {f['extras']} pipeline studies have no GT match and "
             f"need adjudication; precision is reported as a lower bound until "
             f"then.")
    L.append("")
    return "\n".join(L)


def to_jsonable(ev: dict) -> dict:
    """Strip non-serializable bits (sets, nested records) for results.json."""
    def clean_gt(r):
        return {k: (sorted(v) if isinstance(v, set) else v)
                for k, v in r.items() if k != "pipe_clusters"}
    return {
        "n_pipeline_rows": ev["n_pipeline_rows"],
        "n_pipeline_clusters": ev["n_pipeline_clusters"],
        "full": ev["full"],
        "covered": ev["covered"],
    }


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    crosswalk = load_crosswalk()
    gt_studies = load_gt()

    # validate crosswalk slugs against an actual model file
    sample = load_pipeline(next(iter(MODELS.values())))
    actual_groups = {(r["country"], r["slug"]) for r in sample}
    for gt_name, groups in crosswalk["covered"].items():
        for g in groups:
            if (g["country"], g["slug"]) not in actual_groups:
                raise SystemExit(
                    f"Crosswalk error: {gt_name} -> {g} not found in pipeline output")

    all_results: Dict[str, dict] = {}
    per_dtx: Dict[str, List[dict]] = {}
    for model, path in MODELS.items():
        if not path.exists():
            print(f"  WARN: missing {path}, skipping {model}")
            continue
        records = load_pipeline(path)
        ev = evaluate(gt_studies, records, crosswalk)
        all_results[model] = ev
        per_dtx[model] = per_dtx_table(gt_studies, ev, crosswalk)

        # per-model match detail (matched / missed / extras)
        detail = {
            "model": model,
            "full": ev["full"],
            "covered": ev["covered"],
            "matched": [
                {"dtx": r["dtx_name"], "ids": sorted(r["ids"]),
                 "gt_evidence_type": r["evidence_type"],
                 "pipeline_evidence_types": r["pipeline_types"]}
                for r in ev["gt_results"] if r["matched"]
            ],
            "missed": [
                {"dtx": r["dtx_name"], "ids": sorted(r["ids"]),
                 "evidence_type": r["evidence_type"],
                 "covered": r["dtx_name"] in crosswalk["covered"]}
                for r in ev["gt_results"] if not r["matched"]
            ],
            "extras": [
                {"pipeline_dtx": x["pipeline_dtx"], "gt_dtx": x["gt_dtx"],
                 "ids": sorted(x["ids"]), "evidence_types": x["evidence_types"],
                 "sources": x["sources"]}
                for x in ev["extras"]
            ],
        }
        (RESULTS_DIR / f"{model}_matches.json").write_text(
            json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8")

    # combined machine-readable summary
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ground_truth": {
            "studies": len(gt_studies),
            "dtx": len({g["dtx_name"] for g in gt_studies}),
            "covered_dtx": len(crosswalk["covered"]),
            "uncovered_dtx": crosswalk["uncovered"],
        },
        "models": {m: to_jsonable(ev) for m, ev in all_results.items()},
    }
    (RESULTS_DIR / "benchmark_1_results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    md = build_markdown(all_results, per_dtx, gt_studies, crosswalk)
    (RESULTS_DIR / "benchmark_1_results.md").write_text(md, encoding="utf-8")

    # console summary
    print(f"GT: {len(gt_studies)} studies / "
          f"{len({g['dtx_name'] for g in gt_studies})} DTx")
    for model, ev in all_results.items():
        f, c = ev["full"], ev["covered"]
        print(f"  {model}: clusters={ev['n_pipeline_clusters']} "
              f"TP={f['tp']} FN={f['fn']} Extras={f['extras']} | "
              f"full R={pct(f['recall'])} P={pct(f['precision'])} "
              f"F1={pct(f['f1'])} | covered R={pct(c['recall'])}")
    print(f"Results written to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
