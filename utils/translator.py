"""LLM-based translation utilities."""
from typing import List, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_provider import LLMProvider


class Translator:
    """Translate text using the configured LLM provider."""
    
    def __init__(self, source_lang: str = "de", target_lang: str = "en"):
        """Initialize the translator.
        
        Args:
            source_lang: Source language code (default: German).
            target_lang: Target language code (default: English).
        """
        self.source_lang = source_lang
        self.target_lang = target_lang
        # Translation never needs web search; binding the tool makes some
        # providers (e.g. OpenAI with web_search) return list-shaped content
        # blocks instead of a plain string, which broke `.strip()` below.
        self.llm = LLMProvider.get_llm(
            temperature=0.0, max_tokens=2000, enable_web_search=False
        )
    
    @staticmethod
    def _content_to_text(content) -> str:
        """Flatten a LangChain response content into plain text.

        Some providers (notably OpenAI with a bound web_search tool, and
        Anthropic/Gemini) return ``content`` as a list of blocks rather than a
        string. Keep only genuine text, never stringify tool-use blocks.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text_val = block.get("text")
                    if isinstance(text_val, str) and text_val:
                        parts.append(text_val)
            return "".join(parts)
        return "" if content is None else str(content)

    async def translate(self, text: str, preserve_terms: List[str] = None) -> str:
        """Translate a single text string.
        
        Args:
            text: Text to translate.
            preserve_terms: List of terms to preserve unchanged (e.g., ICD-10 codes).
            
        Returns:
            Translated text.
        """
        # Be tolerant if a non-string (e.g. list) is passed in.
        if isinstance(text, list):
            text = self._content_to_text(text)
        if not text or not str(text).strip():
            return text
        
        preserve_note = ""
        if preserve_terms:
            preserve_note = f"\nPreserve these terms exactly as-is: {', '.join(preserve_terms)}"
        
        system_prompt = f"""You are a professional translator. Translate text from German to English.
Keep the translation accurate and natural.
Preserve any technical terms, codes (like ICD-10, NCT numbers), and proper nouns.{preserve_note}
Only return the translation, nothing else."""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=text)
        ]
        
        response = await self.llm.ainvoke(messages)
        return self._content_to_text(response.content).strip()
    
    async def translate_batch(self, texts: List[str], preserve_terms: List[str] = None) -> List[str]:
        """Translate multiple texts in a batch.
        
        Args:
            texts: List of texts to translate.
            preserve_terms: List of terms to preserve unchanged.
            
        Returns:
            List of translated texts.
        """
        results = []
        for text in texts:
            if text and text.strip():
                translated = await self.translate(text, preserve_terms)
                results.append(translated)
            else:
                results.append(text)
        return results
    
    async def translate_dict_fields(
        self, 
        data: Dict, 
        fields_to_translate: List[str],
        preserve_terms: List[str] = None
    ) -> Dict:
        """Translate specific fields in a dictionary.
        
        Args:
            data: Dictionary containing data.
            fields_to_translate: List of field names to translate.
            preserve_terms: List of terms to preserve unchanged.
            
        Returns:
            Dictionary with translated fields.
        """
        result = data.copy()
        
        for field in fields_to_translate:
            if field in result and result[field]:
                value = result[field]
                if isinstance(value, str):
                    result[field] = await self.translate(value, preserve_terms)
                elif isinstance(value, list):
                    result[field] = await self.translate_batch(value, preserve_terms)
        
        return result
    
    def translate_with_mapping(self, text: str, mapping: Dict[str, str]) -> str:
        """Translate using a predefined mapping dictionary.
        
        Args:
            text: Text to translate.
            mapping: Dictionary mapping source terms to target terms.
            
        Returns:
            Translated text if found in mapping, otherwise original text.
        """
        return mapping.get(text, text)
