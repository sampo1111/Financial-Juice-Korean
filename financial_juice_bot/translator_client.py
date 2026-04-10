from __future__ import annotations

import re

import httpx

from .models import NewsInsight, NewsItem


PROTECTED_TERMS = {
    "TotalEnergies": "TotalEnergies",
    "Saudi Aramco": "Saudi Aramco",
    "S&P 500": "S&P 500",
    "Nasdaq": "Nasdaq",
    "Dow Jones": "Dow Jones",
}

MANUAL_TITLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"^(?P<entity>[^:]+):\s*Incidents damaged one refinery processing train$",
            re.IGNORECASE,
        ),
        "{entity}: 사고로 정제 공정 라인 1기 손상",
    ),
    (
        re.compile(
            r"^(?P<entity>[^:]+):\s*Units shut down after incidents$",
            re.IGNORECASE,
        ),
        "{entity}: 사고 이후 설비 가동 중단",
    ),
]

CAMEL_CASE_PATTERN = re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z]*\b")


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
        manual_translation = self._translate_with_manual_rules(normalized)
        if manual_translation is not None:
            return manual_translation

        normalized_for_translation = self._normalize_source_for_translation(normalized)
        prepared_title, placeholder_map = self._protect_terms(normalized_for_translation)
        payload = {
            "text": prepared_title,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "preserve_formatting": True,
            "split_sentences": "0",
            "context": (
                "Translate literally into Korean using market-standard financial wording. "
                "Preserve every number, percentage, parenthesis, qualifier, and original field order. "
                "Do not summarize, reinterpret, or omit information. "
                "Keep abbreviations such as CPI, PPI, PCE, PMI, ISM, NFP, GDP, HICP, MoM, YoY, QoQ, bp, "
                "Actual, Forecast, and Previous when appropriate. "
                "Use these standards when relevant: risk-off=위험회피, risk-on=위험선호, "
                "hawkish=매파적, dovish=비둘기파적, priced in=선반영, guidance=가이던스, "
                "beats forecasts=예상 상회, misses forecasts=예상 하회, "
                "refinery processing train=정제 공정 라인, units shut down=설비 가동 중단. "
                "Company names must not be translated as generic everyday words."
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

        translated = self._restore_terms(translated, placeholder_map)
        translated = self._postprocess(normalized, translated)
        if not translated:
            raise TranslationError("DeepL returned an empty headline.")
        return translated

    def _translate_with_manual_rules(self, title: str) -> str | None:
        for pattern, template in MANUAL_TITLE_PATTERNS:
            match = pattern.match(title)
            if match is None:
                continue
            entity = self._translate_entity_name(match.group("entity").strip())
            return template.format(entity=entity)
        return None

    @staticmethod
    def _translate_entity_name(entity: str) -> str:
        normalized = " ".join(entity.split())
        return PROTECTED_TERMS.get(normalized, normalized)

    @staticmethod
    def _protect_terms(text: str) -> tuple[str, dict[str, str]]:
        protected = text
        placeholder_map: dict[str, str] = {}
        used_terms: set[str] = set()

        for term in CAMEL_CASE_PATTERN.findall(text):
            used_terms.add(term)
        used_terms.update(term for term in PROTECTED_TERMS if term in text)

        for index, source_term in enumerate(sorted(used_terms, key=len, reverse=True)):
            target_term = PROTECTED_TERMS.get(source_term, source_term)
            placeholder = f"__FJTERM_{index}__"
            protected = protected.replace(source_term, placeholder)
            placeholder_map[placeholder] = target_term

        return protected, placeholder_map

    @staticmethod
    def _normalize_source_for_translation(text: str) -> str:
        normalized = text
        normalized = re.sub(r"\brisk-off\b", "risk aversion", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\brisk-on\b", "risk appetite", normalized, flags=re.IGNORECASE)
        normalized = re.sub(
            r"\bprice(?:s|d)? in\b",
            "already reflect",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bbeats?\s+(?:forecasts?|estimates?|expectations?)\b",
            "comes in above forecasts",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bmiss(?:es|ed)?\s+(?:forecasts?|estimates?|expectations?)\b",
            "comes in below forecasts",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"\bcuts guidance\b", "lowers guidance", normalized, flags=re.IGNORECASE)
        return normalized

    @staticmethod
    def _restore_terms(text: str, placeholder_map: dict[str, str]) -> str:
        restored = text
        for placeholder, target_term in placeholder_map.items():
            restored = restored.replace(placeholder, target_term)
        return restored

    @staticmethod
    def _postprocess(source_text: str, text: str) -> str:
        cleaned = " ".join(text.split())
        cleaned = re.sub(r"\s+,", ",", cleaned)
        cleaned = re.sub(r"\(\s+", "(", cleaned)
        cleaned = re.sub(r"\s+\)", ")", cleaned)

        if "processing train" in source_text.lower():
            cleaned = cleaned.replace("정유 처리 열차", "정제 공정 라인")
            cleaned = cleaned.replace("처리 열차", "처리 설비")
            cleaned = cleaned.replace("프로세싱 트레인", "정제 공정 라인")

        if re.search(r"\bunits shut down\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("가동 중단된 유닛 수", "설비 가동 중단")
            cleaned = cleaned.replace("유닛 수", "설비")
            cleaned = cleaned.replace("유닛", "설비")

        if re.search(r"\bpriced in\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("가격에 반영", "선반영")
            cleaned = cleaned.replace("이미 반영", "이미 선반영")

        if re.search(r"\brisk-off\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("리스크 오프", "위험회피")
            cleaned = cleaned.replace("위험 회피", "위험회피")

        if re.search(r"\brisk-on\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("리스크 온", "위험선호")
            cleaned = cleaned.replace("위험 선호", "위험선호")

        if re.search(r"\bhawkish\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("호키시", "매파적")
            cleaned = cleaned.replace("매파적인", "매파적")

        if re.search(r"\bdovish\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("도비시", "비둘기파적")
            cleaned = cleaned.replace("비둘기파적인", "비둘기파적")

        if re.search(r"\bguidance\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("안내", "가이던스")
        if re.search(r"\bcuts guidance\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("가이던스를 하회", "가이던스를 하향")
            cleaned = cleaned.replace("가이던스 하회", "가이던스 하향")
            cleaned = cleaned.replace("가이던스를 낮춘다", "가이던스를 하향한다")

        if re.search(r"\bbeats?\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("예상을 이겼", "예상을 상회했")
            cleaned = cleaned.replace("전망을 이겼", "전망을 상회했")

        if re.search(r"\bmiss(?:es|ed)?\b", source_text, re.IGNORECASE):
            cleaned = cleaned.replace("예상을 놓쳤", "예상을 하회했")
            cleaned = cleaned.replace("전망을 놓쳤", "전망을 하회했")

        if "TotalEnergies" in source_text:
            cleaned = cleaned.replace("총 에너지", "TotalEnergies")
            cleaned = cleaned.replace("토탈 에너지", "TotalEnergies")

        return cleaned.strip(" ,.")
