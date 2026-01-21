"""LLM-based translation utilities."""
import os
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Use langchain_openai for translation (more flexible API)
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


class Translator:
    """Translate text using Azure OpenAI LLM."""
    
    def __init__(self, source_lang: str = "de", target_lang: str = "en"):
        """Initialize the translator.
        
        Args:
            source_lang: Source language code (default: German).
            target_lang: Target language code (default: English).
        """
        load_dotenv()
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.llm = self._setup_llm()
    
    def _setup_llm(self):
        """Setup the Azure OpenAI LLM."""
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
        return AzureChatOpenAI(
            model=deployment,
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        )
    
    async def translate(self, text: str, preserve_terms: List[str] = None) -> str:
        """Translate a single text string.
        
        Args:
            text: Text to translate.
            preserve_terms: List of terms to preserve unchanged (e.g., ICD-10 codes).
            
        Returns:
            Translated text.
        """
        if not text or text.strip() == "":
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
        return response.content.strip()
    
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
