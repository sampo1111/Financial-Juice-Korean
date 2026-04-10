from __future__ import annotations

import re

import httpx

from .models import NewsInsight, NewsItem


class TranslationError(RuntimeError):
    """Raised when headline translation cannot be produced."""


class DeepLTranslateClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        source_lang: str,
        target_lang: str,
        timeout_seconds: float,
    ) -> None:
        if not api_key or api_key.upper().startswith("REPLACE_ME"):
            raise TranslationError("DEEPL_API_KEY is required for DeepL translation.")

        self.source_lang = source_lang.upper()
        self.target_lang = target_lang.upper()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        )

    async def translate_and_explain(self, item: NewsItem) -> NewsInsight:
        translated_title = await self._translate_title(item.title)
        return NewsInsight(
            guid=item.guid,
            title=item.title,
            translated_title=translated_title,
            explanation="",
            link=item.link,
            published_at=item.published_at,
            is_breaking=item.is_breaking,
            image_url=item.image_url,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _translate_title(self, title: str) -> str:
        normalized = " ".join(title.split())
        payload = {
            "text": normalized,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "preserve_formatting": True,
            "split_sentences": "0",
            "context": (
                "Translate literally into Korean. Preserve every number, percentage, "
                "parenthesis, qualifier, and original field order. Do not summarize, "
                "reinterpret, or omit information. Keep abbreviations such as CPI, PPI, "
                "PCE, PMI, ISM, NFP, GDP, HICP, MoM, YoY, QoQ, bp, Actual, Forecast, "
                "and Previous when appropriate."
            ),
        }

        try:
            response = await self._client.post("/v2/translate", data=payload)
            response.raise_for_status()
            data = response.json()
            translated = str(data["translations"][0]["text"]).strip()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise TranslationError(
                f"DeepL request failed with status {exc.response.status_code}: {detail}"
            ) from exc
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            raise TranslationError(f"Failed to parse DeepL response: {exc}") from exc

        translated = self._postprocess(translated)
        if not translated:
            raise TranslationError("DeepL returned an empty headline.")
        return translated

    @staticmethod
    def _postprocess(text: str) -> str:
        cleaned = " ".join(text.split())
        cleaned = re.sub(r"\s+,", ",", cleaned)
        cleaned = re.sub(r"\(\s+", "(", cleaned)
        cleaned = re.sub(r"\s+\)", ")", cleaned)
        return cleaned.strip(" ,.")
