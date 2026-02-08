"""PubMed evidence scraper using E-utilities API.

This module searches PubMed for clinical evidence and downloads PDFs from 
PubMed Central when available. Also saves raw XML responses for future analysis.
"""
import asyncio
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus

from .base_evidence_scraper import BaseEvidenceScraper


class PubMedScraper(BaseEvidenceScraper):
    """Scraper for PubMed using E-utilities API.
    
    Uses free E-utilities API to search PubMed and fetch article details.
    Can also download PDFs from PubMed Central when available.
    """
    
    SOURCE_NAME = "pubmed"
    
    # PubMed E-utilities endpoints
    ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
    
    # Europe PMC PDF URL (more accessible than NCBI PMC)
    # NCBI PMC now uses JavaScript bot protection, Europe PMC is more accessible
    EUROPEPMC_PDF_URL = "https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmc_id}&blobtype=pdf"
    
    # Fallback to NCBI PMC (may fail due to bot protection)
    PMC_PDF_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/pdf/"
    
    def _get_raw_folder(self, country: str, dtx_name: str, evidence_type: str) -> Path:
        """Get or create the raw XML folder for PubMed downloads.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path to the raw folder.
        """
        folder = self._get_dtx_folder(country, dtx_name, evidence_type) / "raw"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    async def _fetch_and_save_raw_xml(
        self, 
        pmid: str, 
        country: str, 
        dtx_name: str, 
        evidence_type: str
    ) -> Optional[str]:
        """Fetch raw XML for a single article and save it.
        
        Args:
            pmid: PubMed ID.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path to the saved XML file, or None if failed.
        """
        raw_folder = self._get_raw_folder(country, dtx_name, evidence_type)
        save_path = raw_folder / f"{pmid}.xml"
        
        # Skip if already downloaded
        if save_path.exists():
            return str(save_path)
        
        client = await self._get_http_client()
        
        try:
            fetch_params = {
                "db": "pubmed",
                "id": pmid,
                "retmode": "xml",
                "rettype": "full"  # Full record for more data
            }
            
            response = await client.get(self.EFETCH_URL, params=fetch_params)
            response.raise_for_status()
            
            # Save raw XML
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(response.text)
            
            return str(save_path)
            
        except Exception as e:
            # Non-critical, just log
            return None
    
    async def search(self, query: str, max_results: int = 50) -> List[Dict]:
        """Search PubMed for articles matching the query.
        
        PubMed E-utilities supports double quotes for exact phrase matching.
        If the query contains quoted phrases like "Cara Care", PubMed will
        search for that exact phrase rather than individual words.
        
        The httpx library handles URL encoding properly, so quotes in the
        query string are preserved (encoded as %22).
        
        Args:
            query: Search query string (may contain quoted phrases).
            max_results: Maximum number of results to return.
            
        Returns:
            List of article dictionaries with metadata.
        """
        client = await self._get_http_client()
        
        try:
            # Step 1: Search for PMIDs
            # Note: PubMed supports double quotes for exact phrase matching
            # httpx properly URL-encodes the query, preserving quotes
            search_params = {
                "db": "pubmed",
                "term": query,
                "retmax": max_results,
                "retmode": "json",
                "sort": "relevance"
            }
            
            search_response = await client.get(self.ESEARCH_URL, params=search_params)
            search_response.raise_for_status()
            search_data = search_response.json()
            
            pmids = search_data.get("esearchresult", {}).get("idlist", [])
            
            if not pmids:
                return []
            
            # Step 2: Fetch article details
            fetch_params = {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
                "rettype": "abstract"
            }
            
            fetch_response = await client.get(self.EFETCH_URL, params=fetch_params)
            fetch_response.raise_for_status()
            
            # Parse XML response
            articles = self._parse_pubmed_xml(fetch_response.text)
            
            # Step 3: Get PMC IDs for PDF download
            if articles:
                pmc_ids = await self._get_pmc_ids(pmids)
                for article in articles:
                    pmid = article.get("pmid")
                    if pmid in pmc_ids:
                        article["pmc_id"] = pmc_ids[pmid]
                        article["pdf_available"] = True
                    else:
                        article["pdf_available"] = False
            
            return articles
            
        except Exception as e:
            print(f"    PubMed search error: {e}")
            return []
    
    async def _get_pmc_ids(self, pmids: List[str]) -> Dict[str, str]:
        """Get PubMed Central IDs for a list of PMIDs.
        
        Includes retry logic with exponential backoff for rate limiting (429)
        and graceful error handling for network issues.
        
        Args:
            pmids: List of PubMed IDs.
            
        Returns:
            Dictionary mapping PMID to PMC ID.
        """
        if not pmids:
            return {}
        
        client = await self._get_http_client()
        pmc_map = {}
        
        # Batch PMIDs to reduce API calls (max 200 per request per NCBI guidelines)
        batch_size = 100
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i:i + batch_size]
            
            # Retry logic with exponential backoff
            max_retries = 3
            retry_delay = 1.0  # Start with 1 second
            
            for attempt in range(max_retries):
                try:
                    link_params = {
                        "dbfrom": "pubmed",
                        "db": "pmc",
                        "id": ",".join(batch),
                        "retmode": "json"
                    }
                    
                    response = await client.get(self.ELINK_URL, params=link_params)
                    
                    # Handle rate limiting (429) with retry
                    if response.status_code == 429:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                            continue
                        else:
                            # Give up on this batch after max retries
                            break
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    linksets = data.get("linksets", [])
                    
                    for linkset in linksets:
                        pmid = str(linkset.get("ids", [None])[0])
                        linksetdbs = linkset.get("linksetdbs", [])
                        
                        for linkdb in linksetdbs:
                            if linkdb.get("dbto") == "pmc":
                                links = linkdb.get("links", [])
                                if links:
                                    pmc_map[pmid] = f"PMC{links[0]}"
                    
                    # Success - break retry loop
                    break
                    
                except asyncio.CancelledError:
                    raise  # Don't catch cancellation
                except Exception as e:
                    error_str = str(e)
                    # Silently skip non-critical errors (StreamReset, JSON parse errors)
                    # These are often transient network issues
                    if "StreamReset" in error_str or "Invalid control character" in error_str:
                        break  # Don't retry these - they won't help
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                    # Don't print errors for non-critical PMC ID lookups
                    # PDFs are optional, so failures here are acceptable
            
            # Small delay between batches to respect rate limits
            if i + batch_size < len(pmids):
                await asyncio.sleep(0.5)
        
        return pmc_map
    
    def _parse_pubmed_xml(self, xml_text: str) -> List[Dict]:
        """Parse PubMed XML response into article dictionaries.
        
        Args:
            xml_text: XML response from PubMed efetch.
            
        Returns:
            List of article dictionaries.
        """
        results = []
        
        try:
            root = ET.fromstring(xml_text)
            
            for article in root.findall(".//PubmedArticle"):
                try:
                    medline = article.find(".//MedlineCitation")
                    if medline is None:
                        continue
                    
                    pmid = medline.findtext("PMID", "")
                    
                    article_elem = medline.find(".//Article")
                    if article_elem is None:
                        continue
                    
                    # Title
                    title = article_elem.findtext(".//ArticleTitle", "")
                    
                    # Abstract
                    abstract_elem = article_elem.find(".//Abstract")
                    abstract = ""
                    if abstract_elem is not None:
                        abstract_parts = []
                        for text_elem in abstract_elem.findall(".//AbstractText"):
                            label = text_elem.get("Label", "")
                            text = "".join(text_elem.itertext()) or ""
                            if label:
                                abstract_parts.append(f"{label}: {text}")
                            else:
                                abstract_parts.append(text)
                        abstract = " ".join(abstract_parts)
                    
                    # Authors
                    authors = []
                    for author in article_elem.findall(".//Author"):
                        lastname = author.findtext("LastName", "")
                        forename = author.findtext("ForeName", "")
                        if lastname:
                            authors.append(f"{lastname} {forename}".strip())
                    author_str = authors[0] + " et al." if len(authors) > 1 else ", ".join(authors)
                    
                    # Publication date
                    pub_date = article_elem.find(".//PubDate")
                    year = pub_date.findtext("Year", "") if pub_date is not None else ""
                    month = pub_date.findtext("Month", "") if pub_date is not None else ""
                    day = pub_date.findtext("Day", "") if pub_date is not None else ""
                    
                    # Journal
                    journal = article_elem.findtext(".//Journal/Title", "")
                    journal_abbrev = article_elem.findtext(".//Journal/ISOAbbreviation", "")
                    
                    # DOI
                    doi = ""
                    for article_id in article.findall(".//ArticleId"):
                        if article_id.get("IdType") == "doi":
                            doi = article_id.text or ""
                            break
                    
                    # Publication types (useful for classification)
                    pub_types = []
                    for pt in medline.findall(".//PublicationType"):
                        if pt.text:
                            pub_types.append(pt.text)
                    
                    # MeSH terms
                    mesh_terms = []
                    for mesh in medline.findall(".//MeshHeading/DescriptorName"):
                        if mesh.text:
                            mesh_terms.append(mesh.text)
                    
                    # Keywords
                    keywords = []
                    for kw in medline.findall(".//Keyword"):
                        if kw.text:
                            keywords.append(kw.text)
                    
                    results.append({
                        "study_id": pmid,
                        "pmid": pmid,
                        "title": title,
                        "authors": author_str,
                        "authors_list": authors[:5],  # First 5 authors
                        "publication_year": year,
                        "publication_date": f"{year}-{month}-{day}".strip("-"),
                        "journal": journal,
                        "journal_abbrev": journal_abbrev,
                        "doi": doi,
                        "abstract": abstract,
                        "publication_types": pub_types,
                        "mesh_terms": mesh_terms[:10],  # Limit to 10
                        "keywords": keywords[:10],  # Limit to 10
                        "source": "PubMed",
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                    })
                    
                except Exception as e:
                    continue  # Skip problematic articles
                    
        except ET.ParseError as e:
            print(f"    XML parse error: {e}")
        
        return results
    
    async def get_study_details(self, study_id: str) -> Optional[Dict]:
        """Get detailed information for a specific study by PMID.
        
        Args:
            study_id: PubMed ID (PMID).
            
        Returns:
            Dictionary with study details or None.
        """
        results = await self.search(f"{study_id}[PMID]", max_results=1)
        return results[0] if results else None
    
    async def download_article_pdf(
        self,
        article: Dict,
        country: str,
        dtx_name: str,
        evidence_type: str
    ) -> Optional[str]:
        """Download PDF for an article if available in PMC.
        
        Tries Europe PMC first (more accessible), then falls back to NCBI PMC.
        
        Args:
            article: Article dictionary with pmc_id.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path to downloaded PDF or None.
        """
        pmc_id = article.get("pmc_id")
        if not pmc_id:
            return None
        
        pmid = article.get("pmid", "unknown")
        filename = f"{pmid}_{pmc_id}.pdf"
        
        # Try Europe PMC first (more accessible, no bot protection)
        europepmc_url = self.EUROPEPMC_PDF_URL.format(pmc_id=pmc_id)
        result = await self.download_pdf(
            url=europepmc_url,
            country=country,
            dtx_name=dtx_name,
            evidence_type=evidence_type,
            filename=filename
        )
        
        if result:
            return str(result)
        
        # Fallback to NCBI PMC (may fail due to bot protection)
        pmc_url = self.PMC_PDF_URL.format(pmc_id=pmc_id)
        result = await self.download_pdf(
            url=pmc_url,
            country=country,
            dtx_name=dtx_name,
            evidence_type=evidence_type,
            filename=filename
        )
        
        return str(result) if result else None
    
    async def search_and_save_with_pdfs(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        classifier,
        max_results_per_query: int = 50,
        download_pdfs: bool = True
    ) -> Dict[str, int]:
        """Search PubMed, classify results, and download PDFs.
        
        Includes relevance filtering to remove false positives.
        
        Args:
            queries: List of search query strings.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            classifier: LLM classifier for RCT/RWE determination
            max_results_per_query: Max results per query
            download_pdfs: Whether to download available PDFs
            
        Returns:
            Dictionary with counts and PDF stats.
        """
        all_results = []
        seen_pmids = set()
        filtered_count = 0
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by PMID and filter for relevance
                for result in results:
                    pmid = result.get("pmid")
                    if pmid and pmid not in seen_pmids:
                        seen_pmids.add(pmid)
                        
                        # Check relevance before adding
                        if self.is_result_relevant(result, dtx_name):
                            # Track which query found this result
                            result["matched_query"] = query
                            all_results.append(result)
                        else:
                            filtered_count += 1
                
                await asyncio.sleep(0.4)  # Rate limiting (max 3 req/sec)
                
            except Exception as e:
                print(f"    Error searching '{query[:50]}...': {e}")
        
        if filtered_count > 0:
            print(f"    Filtered {filtered_count} irrelevant articles")
        
        if not all_results:
            return {"rct": 0, "rwe": 0, "total": 0, "pdfs_downloaded": 0, "filtered": filtered_count}
        
        print(f"    Found {len(all_results)} relevant articles, classifying...")
        
        # Classify and organize results
        rct_results = []
        rwe_results = []
        pdfs_downloaded = 0
        
        for result in all_results:
            try:
                classification = await classifier.classify(result)
                result["classification"] = classification
                
                evidence_type = classification.get("classification", "RWE")
                
                if evidence_type == "RCT":
                    rct_results.append(result)
                else:
                    rwe_results.append(result)
                
                # Save raw XML for this article
                pmid = result.get("pmid")
                if pmid:
                    raw_xml_path = await self._fetch_and_save_raw_xml(
                        pmid, country, dtx_name, evidence_type
                    )
                    if raw_xml_path:
                        result["_raw_xml_path"] = raw_xml_path
                
                # Download PDF if available
                if download_pdfs and result.get("pdf_available"):
                    pdf_path = await self.download_article_pdf(
                        result, country, dtx_name, evidence_type
                    )
                    if pdf_path:
                        result["pdf_path"] = pdf_path
                        pdfs_downloaded += 1
                    
            except Exception as e:
                # Default to RWE if classification fails
                result["classification"] = {
                    "classification": "RWE", 
                    "confidence": 0, 
                    "reason": f"Classification failed: {e}"
                }
                rwe_results.append(result)
        
        # Save results
        if rct_results:
            self.save_metadata(country, dtx_name, "RCT", {
                "studies": rct_results,
                "count": len(rct_results),
                "queries_used": queries
            }, "studies.json")
            print(f"    Saved {len(rct_results)} RCT articles")
        
        if rwe_results:
            self.save_metadata(country, dtx_name, "RWE", {
                "studies": rwe_results,
                "count": len(rwe_results),
                "queries_used": queries
            }, "studies.json")
            print(f"    Saved {len(rwe_results)} RWE articles")
        
        return {
            "rct": len(rct_results),
            "rwe": len(rwe_results),
            "total": len(all_results),
            "pdfs_downloaded": pdfs_downloaded,
            "pdfs_available": sum(1 for r in all_results if r.get("pdf_available")),
            "filtered": filtered_count
        }
