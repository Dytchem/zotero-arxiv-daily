from time import sleep
from typing import Any

import requests
from loguru import logger

from ..protocol import Paper
from .base import BaseRetriever, register_retriever


@register_retriever("biorxiv")
class BiorxivRetriever(BaseRetriever):
    server = "biorxiv"

    def __init__(self, config):
        super().__init__(config)
        if self.retriever_config.category is None:
            raise ValueError(f"category must be specified for {self.name}")

    def _retrieve_raw_papers(self) -> list[dict[str, Any]]:
        api_url = f"https://api.biorxiv.org/details/{self.server}/2d"
        retry_num = 10
        delay_time = 10
        for i in range(retry_num):
            try:
                response = requests.get(api_url)
                response.raise_for_status()
                break
            except Exception as e:
                if i == retry_num - 1:
                    raise e
                else:
                    logger.warning(f"Failed to retrieve papers: {e!s}. Retry in {delay_time} seconds.")
                    sleep(delay_time)
        result = response.json()
        collection = result['collection']
        if len(collection) == 0:
            logger.warning(f"No paper found. API Message: {result['messages']}")
            return []
        all_dates = {c['date'] for c in collection}
        latest_date = sorted(all_dates)[-1]
        collection = [c for c in collection if c['date'] == latest_date]
        categories = [c.lower() for c in self.retriever_config.category]
        collection = [c for c in collection if c['category'] in categories]
        if self.config.executor.debug:
            collection = collection[:10]
        return collection


    def convert_to_paper(self, raw_paper:dict[str, Any]) -> Paper | None:
        title = raw_paper['title']
        authors = [a.strip() for a in raw_paper['authors'].split(';')]
        abstract = raw_paper['abstract']
        abs_url = f"https://www.{self.server}.org/content/{raw_paper['doi']}v{raw_paper['version']}"
        pdf_url = abs_url + ".full.pdf"
        full_text = None
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=abs_url,
            pdf_url=pdf_url,
            full_text=full_text
        )

    def fetch_full_text(self, paper: Paper) -> str | None:
        """bioRxiv/medRxiv PDFs are open access; extract text from the PDF URL."""
        from .arxiv_retriever import extract_text_from_pdf
        return extract_text_from_pdf(paper)