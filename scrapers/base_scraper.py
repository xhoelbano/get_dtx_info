"""Base scraper class with browser-use setup."""
import os
import json
from abc import ABC, abstractmethod
from pathlib import Path
from dotenv import load_dotenv
from browser_use import Agent, Browser

# Import the correct Azure OpenAI class
try:
    from browser_use import ChatAzureOpenAI
except ImportError:
    from langchain_openai import AzureChatOpenAI as ChatAzureOpenAI


class BaseScraper(ABC):
    """Base class for all scrapers with browser-use setup."""
    
    def __init__(self, config_path: str = None):
        """Initialize the scraper with configuration.
        
        Args:
            config_path: Path to the country configuration JSON file.
        """
        load_dotenv()
        
        self.config = {}
        if config_path:
            self.config = self._load_config(config_path)
        
        self.browser = None
        self.llm = None
        self._setup_llm()
    
    def _load_config(self, config_path: str) -> dict:
        """Load configuration from JSON file."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def _setup_llm(self):
        """Setup the Azure OpenAI LLM."""
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
        self.llm = ChatAzureOpenAI(
            model=deployment,
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        )
    
    async def _create_browser(self) -> Browser:
        """Create and return a browser instance."""
        if self.browser is None:
            self.browser = Browser()
        return self.browser
    
    async def _create_agent(self, task: str, browser: Browser = None) -> Agent:
        """Create an agent with the given task.
        
        Args:
            task: The task description for the agent.
            browser: Optional browser instance. If not provided, creates a new one.
            
        Returns:
            Agent instance configured with the task.
        """
        if browser is None:
            browser = await self._create_browser()
        
        return Agent(
            task=task,
            llm=self.llm,
            browser=browser,
        )
    
    async def close(self):
        """Close the browser and clean up resources."""
        if self.browser:
            await self.browser.close()
            self.browser = None
    
    @abstractmethod
    async def scrape(self, **kwargs):
        """Main scraping method to be implemented by subclasses."""
        pass
