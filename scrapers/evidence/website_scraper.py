"""Website-based evidence discovery using browser-use (Phase-2 extension).

Visits the DTx company website to find research/publications links when registry
sources return no verified evidence. The browser-use Agent's LLM is configurable:
it follows BROWSER_USE_PROVIDER / BROWSER_USE_MODEL, which default to LLM_PROVIDER
(and that provider's model), so by default it uses the same provider as the rest
of the pipeline and reuses the existing per-provider API keys.

Folder layout matches other sources: candidates/website/, verified/{RCT|RWE}/website/,
rejected/website/.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from slugify import slugify

from utils.llm_provider import LLMProvider

load_dotenv(override=True)

# Registry sources from the main Phase-2 pipeline (exclude "website" when counting)
REGISTRY_SOURCES = frozenset({"pubmed", "clinicaltrials", "drks", "isrctn"})
SOURCE_NAME = "website"

# Single source of truth for the per-candidate evidence structure. Edit the JSON
# file to add/rename fields; the agent prompt and persistence follow it (no
# hardcoded structure here).
EVIDENCE_CANDIDATE_SCHEMA_PATH = Path("data-format/evidence_candidate.json")


def _load_evidence_candidate_schema() -> Dict[str, Any]:
    """Load the evidence-candidate schema (field -> description)."""
    if not EVIDENCE_CANDIDATE_SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema file not found: {EVIDENCE_CANDIDATE_SCHEMA_PATH}. "
            "Create it at data-format/evidence_candidate.json."
        )
    with open(EVIDENCE_CANDIDATE_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _dtx_slug(dtx_name: str) -> str:
    return slugify(dtx_name, max_length=50, lowercase=True)


def count_registry_verified_studies(
    evidence_dir: Path | str, country: str, dtx_name: str
) -> Tuple[int, int]:
    """Count verified RCT and RWE studies from registry sources only (not website).

    Args:
        evidence_dir: Root evidence folder (e.g. "evidence").
        country: "Germany" or "USA".
        dtx_name: DTx product name (same as in dtx_data).

    Returns:
        (rct_total, rwe_total) summed across pubmed, clinicaltrials, drks, isrctn.
    """
    base = Path(evidence_dir) / country / _dtx_slug(dtx_name) / "verified"
    rct_total = 0
    rwe_total = 0
    for evidence_type in ("RCT", "RWE"):
        et_path = base / evidence_type
        if not et_path.is_dir():
            continue
        for src_dir in et_path.iterdir():
            if not src_dir.is_dir() or src_dir.name not in REGISTRY_SOURCES:
                continue
            studies_file = src_dir / "studies.json"
            if not studies_file.is_file():
                continue
            try:
                with open(studies_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                n = int(data.get("count", len(data.get("studies", []))))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            if evidence_type == "RCT":
                rct_total += n
            else:
                rwe_total += n
    return rct_total, rwe_total


def extract_json_array_from_text(text: str) -> Optional[List[Dict[str, Any]]]:
    """Parse a JSON array from agent output (handles markdown fences)."""
    if not text or not text.strip():
        return None
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(s[start : end + 1])
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def _stable_study_id(url: str, title: str) -> str:
    h = hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest()
    return f"web_{h[:16]}"


class WebsiteEvidenceScraper:
    """Run browser-use Agent on company_website; verify + classify; save under website/."""

    def __init__(self, evidence_dir: str = "evidence", metrics_sink: Optional[List[Dict]] = None):
        self.evidence_dir = Path(evidence_dir)
        # Shared sink for Phase 2 LLM metrics (browser-use agent + verify/classify).
        self.metrics: List[Dict] = metrics_sink if metrics_sink is not None else []
        # Schema-driven evidence-candidate structure (no hardcoded fields).
        self.evidence_schema = _load_evidence_candidate_schema()

    def registry_evidence_total(self, country: str, dtx_name: str) -> int:
        r, w = count_registry_verified_studies(self.evidence_dir, country, dtx_name)
        return r + w

    def _record_agent_metrics(
        self,
        history: Any,
        provider: str,
        model: str,
        latency_ms: float,
        dtx_name: str,
    ) -> None:
        """Append a best-effort metrics row for the browser-use agent run.

        browser-use exposes aggregate token/cost usage on history.usage when the
        Agent is created with calculate_cost=True. Reliability varies by provider,
        so all fields are extracted defensively and wall-clock latency is always
        recorded.
        """
        input_tokens = output_tokens = total_tokens = None
        cost = None

        usage = None
        if history is not None:
            try:
                usage = history.usage
            except Exception:
                usage = getattr(history, "usage", None)

        if usage is not None:
            def _get(obj, *names):
                for n in names:
                    val = getattr(obj, n, None)
                    if val is None and isinstance(obj, dict):
                        val = obj.get(n)
                    if val is not None:
                        return val
                return None

            input_tokens = _get(usage, "total_prompt_tokens", "prompt_tokens", "input_tokens")
            output_tokens = _get(usage, "total_completion_tokens", "completion_tokens", "output_tokens")
            total_tokens = _get(usage, "total_tokens", "tokens")
            cost = _get(usage, "total_cost", "cost")

        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        self.metrics.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "call_label": "phase2_website_agent",
            "provider": provider,
            "model": model,
            "web_search": True,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(cost, 6) if isinstance(cost, (int, float)) else None,
            "success": history is not None,
            "error": None if history is not None else "agent returned no history",
            "dtx_name": dtx_name,
        })

    def _candidate_studies_from_items(
        self,
        items: List[Dict[str, Any]],
        dtx_data: Dict[str, Any],
        company_website: str,
        model: str,
    ) -> List[Dict[str, Any]]:
        """Build Layer-1 candidate study dicts from parsed agent items.

        Every field declared in ``evidence_candidate.json`` is persisted on the
        candidate (defaulting to None when the agent did not provide it), so the
        saved structure is complete and ready for the next phase. Internal
        locator fields (title/url/source page) used by the verifier are kept too.
        """
        out: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            ext_url = str(item.get("external_url") or item.get("url") or "").strip()
            src_page = str(
                item.get("source_page_url") or item.get("source_url_on_website") or ""
            ).strip()
            snippet = str(item.get("snippet") or item.get("brief_summary") or "").strip()

            # Copy every evidence-candidate schema field (null default).
            evidence_fields = {key: item.get(key, None) for key in self.evidence_schema}
            # Ensure a usable source link: fall back to the external URL found.
            if not evidence_fields.get("sources_publications"):
                evidence_fields["sources_publications"] = ext_url or src_page or None

            if not ext_url and not evidence_fields.get("sources_publications"):
                continue

            study: Dict[str, Any] = dict(evidence_fields)
            # Give the verifier some text even when key_outcomes_findings is null.
            abstract_text = str(evidence_fields.get("key_outcomes_findings") or snippet or "")
            study.update(
                {
                    "study_id": _stable_study_id(ext_url or str(evidence_fields["sources_publications"]), title),
                    "title": title or ext_url or "Untitled website evidence",
                    "url": ext_url,
                    "source": "Website",
                    "abstract": abstract_text,
                    "brief_summary": abstract_text,
                    "_source_url_on_website": src_page,
                    "_company_website": company_website,
                    "_matched_query": f"website:{company_website}",
                    "_browser_use_model": model,
                    "_collected_at": datetime.utcnow().isoformat() + "Z",
                }
            )
            out.append(study)
        return out

    def _load_candidate_studies(self, path: Path) -> List[Dict[str, Any]]:
        if not path.is_file():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            studies = data.get("studies", [])
            return [s for s in studies if isinstance(s, dict)]
        except (OSError, json.JSONDecodeError):
            return []

    @staticmethod
    async def _fetch_html_httpx(url: str, timeout: float = 20.0) -> Tuple[str, str]:
        """Fetch page HTML via httpx. Returns (html, final_url). Empty on failure."""
        if not url:
            return "", ""
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
                },
            ) as client:
                resp = await client.get(url)
                final_url = str(resp.url)
                ct = resp.headers.get("content-type", "")
                if "pdf" in ct.lower():
                    return "", final_url
                if resp.status_code < 400:
                    return resp.text, final_url
                # Cloudflare challenge pages return 403 with a tiny "Just a moment" page.
                # Discard these; they have no useful content.
        except Exception:
            pass
        return "", ""

    @staticmethod
    async def _fetch_html_playwright(url: str, timeout_ms: int = 30000) -> Tuple[str, str]:
        """Fetch page HTML using Playwright (headed browser). Bypasses Cloudflare."""
        if not url:
            return "", ""
        try:
            import asyncio as _aio
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=False)
                page = await browser.new_page()
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    ct = (resp.headers.get("content-type", "") if resp else "")
                    if "pdf" in ct.lower():
                        return "", page.url

                    # Wait for Cloudflare challenge to resolve (up to 12s)
                    for _ in range(6):
                        title = await page.title()
                        if "just a moment" not in title.lower():
                            break
                        await _aio.sleep(2)

                    html = await page.content()
                    final_url = page.url
                    # Discard if still stuck on the challenge page
                    if "just a moment" in (await page.title()).lower():
                        return "", final_url
                    return html, final_url
                finally:
                    await browser.close()
        except Exception:
            pass
        return "", ""

    @classmethod
    async def _fetch_html(cls, url: str) -> Tuple[str, str]:
        """Fetch page HTML: try httpx first, fall back to Playwright for blocked sites."""
        html, final_url = await cls._fetch_html_httpx(url)
        if html:
            return html, final_url
        html, final_url = await cls._fetch_html_playwright(url)
        return html, final_url

    async def _fetch_and_save_raw_html(
        self,
        candidate_studies: List[Dict[str, Any]],
        raw_dir: Path,
    ) -> Dict[str, str]:
        """Fetch HTML from external_url + source_page_url for each candidate.

        Saves them in raw_dir as {study_id}_external.html / {study_id}_source_page.html.
        Returns a mapping study_id -> combined text (for use as raw_content in verifier).
        """
        raw_dir.mkdir(parents=True, exist_ok=True)
        result: Dict[str, str] = {}
        for study in candidate_studies:
            sid = study.get("study_id", "")
            if not sid:
                continue

            external_url = study.get("url", "")
            source_page = study.get("_source_url_on_website", "")

            parts: List[str] = []

            # --- external URL (the evidence link) ---
            ext_path = raw_dir / f"{sid}_external.html"
            if ext_path.is_file():
                ext_html = ext_path.read_text(encoding="utf-8", errors="replace")
            else:
                ext_html, _ = await self._fetch_html(external_url)
                if ext_html:
                    ext_path.write_text(ext_html, encoding="utf-8")
            if ext_html:
                parts.append(ext_html)

            # --- source page (company website page where link was found) ---
            src_path = raw_dir / f"{sid}_source_page.html"
            if src_path.is_file():
                src_html = src_path.read_text(encoding="utf-8", errors="replace")
            else:
                src_html, _ = await self._fetch_html(source_page)
                if src_html:
                    src_path.write_text(src_html, encoding="utf-8")
            if src_html:
                parts.append(src_html)

            result[sid] = "\n".join(parts)
        return result

    def _save_candidates(
        self,
        country: str,
        dtx_slug: str,
        candidate_studies: List[Dict[str, Any]],
        openai_model: str,
        raw_agent_text: str,
    ) -> Path:
        """Layer 1: candidates/website/studies.json and raw/agent_output.txt."""
        now = datetime.utcnow().isoformat() + "Z"
        cand_dir = self.evidence_dir / country / dtx_slug / "candidates" / SOURCE_NAME
        cand_dir.mkdir(parents=True, exist_ok=True)
        raw_dir = cand_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = cand_dir / "studies.json"
        payload = {
            "studies": candidate_studies,
            "count": len(candidate_studies),
            "_saved_at": now,
            "_source": SOURCE_NAME,
            "_layer": "candidates",
            "_browser_use_openai_model": openai_model,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        raw_path = raw_dir / "agent_output.txt"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_agent_text or "")
        return path

    def _save_verified_and_rejected(
        self,
        country: str,
        dtx_slug: str,
        rct_studies: List[Dict[str, Any]],
        rwe_studies: List[Dict[str, Any]],
        rejected: List[Dict[str, Any]],
        openai_model: str,
    ) -> bool:
        """Layer 2: verified/RCT|RWE/website/ and rejected/website/.

        Only creates folders when there are actual studies to save.
        Copies raw HTML files from candidates/website/raw/ into verified/*/website/raw/
        to stay consistent with registry sources.
        """
        now = datetime.utcnow().isoformat() + "Z"
        base = self.evidence_dir / country / dtx_slug
        base_ver = base / "verified"
        cand_raw = base / "candidates" / SOURCE_NAME / "raw"

        def write_verified(evidence_type: str, studies: List[Dict[str, Any]]) -> None:
            if not studies:
                return
            folder = base_ver / evidence_type / SOURCE_NAME
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "studies.json"
            payload = {
                "studies": studies,
                "count": len(studies),
                "_saved_at": now,
                "_source": SOURCE_NAME,
                "_layer": "verified",
                "_evidence_type": evidence_type,
                "_browser_use_openai_model": openai_model,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

            if cand_raw.is_dir():
                raw_dst = folder / "raw"
                raw_dst.mkdir(parents=True, exist_ok=True)
                for study in studies:
                    sid = study.get("study_id", "")
                    if not sid:
                        continue
                    for suffix in ("_external.html", "_source_page.html"):
                        src = cand_raw / f"{sid}{suffix}"
                        if src.is_file():
                            shutil.copy2(src, raw_dst / f"{sid}{suffix}")

        write_verified("RCT", rct_studies)
        write_verified("RWE", rwe_studies)

        if rejected:
            rej_dir = base / "rejected" / SOURCE_NAME
            rej_dir.mkdir(parents=True, exist_ok=True)
            rej_path = rej_dir / "rejected.json"
            with open(rej_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "studies": rejected,
                        "count": len(rejected),
                        "_saved_at": now,
                        "_source": SOURCE_NAME,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        return True

    async def scrape_dtx_website(
        self,
        dtx_data: Dict[str, Any],
        country: str,
        *,
        max_agent_steps: int = 35,
        delay_seconds_after: float = 0.0,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Visit company_website, save candidates, verify, classify, save verified/rejected.

        If candidates/website/studies.json exists with at least one study and ``force`` is False,
        skips the browser agent and re-runs verification from saved candidates only.

        Args:
            dtx_data: One entry from dtx_list (needs dtx_name, company_website, etc.).
            country: "Germany" or "USA".
            max_agent_steps: browser-use Agent step limit.
            delay_seconds_after: optional sleep after run (rate limiting).
            force: If True, always run the browser agent and overwrite candidates.

        Returns:
            Summary dict with saved, rct_count, rwe_count, rejected_count, skipped_browser, error.
        """
        from utils.evidence_verifier import EvidenceClassifierV2, EvidenceVerifier

        dtx_name = dtx_data.get("dtx_name", "Unknown")
        company_website = (dtx_data.get("company_website") or "").strip()
        dtx_slug = _dtx_slug(dtx_name)
        cand_file = (
            self.evidence_dir / country / dtx_slug / "candidates" / SOURCE_NAME / "studies.json"
        )

        if not company_website:
            return {
                "saved": False,
                "rct_count": 0,
                "rwe_count": 0,
                "rejected_count": 0,
                "skipped_browser": False,
                "error": "company_website is empty",
            }

        # The browser-use agent's provider/model default to LLM_PROVIDER and that
        # provider's model (override with BROWSER_USE_PROVIDER / BROWSER_USE_MODEL).
        provider = LLMProvider.get_browser_use_provider()
        model = LLMProvider.get_browser_use_model()
        if not model:
            return {
                "saved": False,
                "rct_count": 0,
                "rwe_count": 0,
                "rejected_count": 0,
                "skipped_browser": False,
                "error": (
                    f"No browser-use model configured for provider '{provider}' "
                    "(set BROWSER_USE_MODEL or the provider's model var)"
                ),
            }

        raw_text = ""
        skipped_browser = False
        candidate_studies: List[Dict[str, Any]] = []

        if not force and cand_file.is_file():
            candidate_studies = self._load_candidate_studies(cand_file)
            if candidate_studies:
                skipped_browser = True
                raw_path = cand_file.parent / "raw" / "agent_output.txt"
                if raw_path.is_file():
                    try:
                        raw_text = raw_path.read_text(encoding="utf-8")
                    except OSError:
                        raw_text = ""

        if not skipped_browser:
            from browser_use import Agent, Browser

            try:
                llm, provider, model = LLMProvider.get_browser_use_llm(temperature=0.1)
            except (ValueError, ImportError) as exc:
                return {
                    "saved": False,
                    "rct_count": 0,
                    "rwe_count": 0,
                    "rejected_count": 0,
                    "skipped_browser": False,
                    "error": str(exc),
                }
            browser = Browser()
            company = dtx_data.get("company_provider", "") or ""
            desc = (dtx_data.get("description") or "")[:800]

            schema_json = json.dumps(self.evidence_schema, indent=2, ensure_ascii=False)
            task = f"""You are collecting clinical evidence for a Digital Therapeutic (DTx) product.

Product name: {dtx_name}
Company: {company}
Company website root: {company_website}
Context: {desc}

Goals:
1) Open the company website (start from the URL above if it is a full URL; otherwise search for it).
2) Find pages about research, publications, clinical studies, evidence, science, or trials.
   Use site navigation, footer, header menus, sitemap, or on-site search. Try these terms if needed:
   English: research, publications, studies, evidence, clinical trials, science.
   German: Forschung, Publikationen, Studien, Studie, Fachkreise, Evidenz, Wissenschaft, klinisch.
3) On those pages, find every reference to a clinical study, trial, or peer-reviewed publication
   about THIS product. Prefer clicking through to list pages over guessing element indices; scroll
   to load content.
4) GO DEEP. For each evidence item, OPEN the link. If it lands on an intermediate page (news post,
   blog, press release, or a study teaser/summary) that contains a "read the full publication",
   "full study", "view paper", "read more", DOI, PubMed, or PDF link/button, FOLLOW it through to
   the ACTUAL full publication (peer-reviewed journal page, PubMed, DOI page, ClinicalTrials.gov /
   DRKS registration, preprint, or study PDF). The full publication is the real target - do not stop
   at the company page.
5) On the full publication, read the content and extract the structured fields described in the
   schema below. If a field is not stated, use null. Always keep the real source link(s) you reached
   in "sources_publications" - never invent URLs.

For each distinct evidence item, also record these locator fields (in addition to the schema):
   - title: the publication / study title
   - external_url: the deepest publication link you reached (absolute URL)
   - source_page_url: the company page URL where you first found this evidence

Each evidence object MUST contain exactly these schema keys (values described; use null when unknown):
{schema_json}

When finished, respond with ONLY a valid JSON array of such objects (no markdown, no commentary).
If nothing is found, respond with: []
"""

            agent = Agent(
                task=task,
                llm=llm,
                browser=browser,
                max_actions_per_step=6,
                calculate_cost=True,
            )
            history = None
            agent_start = time.perf_counter()
            try:
                history = await agent.run(max_steps=max_agent_steps)
            finally:
                agent_latency_ms = round((time.perf_counter() - agent_start) * 1000, 2)
                try:
                    await browser.kill()
                except Exception:
                    try:
                        await browser.stop()
                    except Exception:
                        pass

            self._record_agent_metrics(history, provider, model, agent_latency_ms, dtx_name)

            if delay_seconds_after > 0:
                import asyncio

                await asyncio.sleep(delay_seconds_after)

            if history is not None:
                fr = history.final_result()
                if fr:
                    raw_text = fr
                else:
                    parts = []
                    for h in getattr(history, "history", []) or []:
                        for r in getattr(h, "result", []) or []:
                            ec = getattr(r, "extracted_content", None)
                            if ec:
                                parts.append(str(ec))
                    raw_text = "\n".join(parts)

            items = extract_json_array_from_text(raw_text) or []
            candidate_studies = self._candidate_studies_from_items(
                items, dtx_data, company_website, model
            )
            self._save_candidates(country, dtx_slug, candidate_studies, model, raw_text)

        raw_dir = (
            self.evidence_dir / country / dtx_slug / "candidates" / SOURCE_NAME / "raw"
        )
        raw_html_map = await self._fetch_and_save_raw_html(candidate_studies, raw_dir)

        verifier = EvidenceVerifier(metrics_sink=self.metrics)
        classifier = EvidenceClassifierV2(metrics_sink=self.metrics)

        rct_studies: List[Dict[str, Any]] = []
        rwe_studies: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        for study in candidate_studies:
            study = dict(study)
            sid = study.get("study_id", "")
            raw_content = raw_html_map.get(sid) or None
            try:
                verification = await verifier.verify_study(study, dtx_data, raw_content=raw_content)
            except Exception as e:
                verification = {
                    "is_relevant": False,
                    "confidence": 0,
                    "reason": f"verify_study error: {e}",
                    "matched_elements": [],
                }
            study["_verification"] = verification
            if not verification.get("is_relevant"):
                rejected.append(study)
                continue

            try:
                classification = await classifier.classify(study, raw_content=raw_content)
            except Exception as e:
                classification = {
                    "classification": "RWE",
                    "confidence": 50,
                    "reason": f"classify error: {e}",
                }
            study["_classification"] = classification
            label = classification.get("classification")
            # Keep the schema's evidence_type in sync with the final classification.
            study["evidence_type"] = label if label in ("RCT", "RWE") else "other"
            if label == "RCT":
                rct_studies.append(study)
            elif label == "RWE":
                rwe_studies.append(study)
            else:
                rejected.append(study)

        saved = self._save_verified_and_rejected(
            country, dtx_slug, rct_studies, rwe_studies, rejected, model
        )

        return {
            "saved": saved,
            "rct_count": len(rct_studies),
            "rwe_count": len(rwe_studies),
            "rejected_count": len(rejected),
            "skipped_browser": skipped_browser,
            "dtx_name": dtx_name,
            "slug": dtx_slug,
            "raw_agent_text_preview": (raw_text[:2000] if raw_text else ""),
        }
