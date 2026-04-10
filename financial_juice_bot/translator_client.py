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

        self.api_key = api_key
        self.source_lang = source_lang.upper()
        self.target_lang = target_lang.upper()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        )

    async def translate_and_explain(self, item: NewsItem) -> NewsInsight:
        translated_title = await self._build_translated_title(item.title)
        explanation = self._build_explanation(item.title, translated_title)
        return NewsInsight(
            guid=item.guid,
            title=item.title,
            translated_title=translated_title,
            explanation=explanation,
            link=item.link,
            published_at=item.published_at,
            is_breaking=item.is_breaking,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _build_translated_title(self, title: str) -> str:
        normalized = " ".join(title.split())

        specialized = self._translate_specialized_headline(normalized)
        if specialized:
            return specialized

        translated = await self._translate_with_deepl(normalized)
        translated = self._postprocess_translated_title(translated)
        if not translated:
            raise TranslationError("DeepL returned an empty headline.")
        return translated

    async def _translate_with_deepl(self, title: str) -> str:
        payload = {
            "text": title,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "preserve_formatting": True,
            "split_sentences": "0",
            "context": (
                "Translate as a concise Korean financial news headline. "
                "Preserve finance abbreviations such as PMI, CPI, PPI, PCE, ISM, NFP, GDP, and bp."
            ),
        }

        try:
            response = await self._client.post("/v2/translate", data=payload)
            response.raise_for_status()
            data = response.json()
            translated = data["translations"][0]["text"]
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise TranslationError(
                f"DeepL request failed with status {exc.response.status_code}: {detail}"
            ) from exc
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            raise TranslationError(f"Failed to parse DeepL response: {exc}") from exc

        return str(translated).strip()

    def _translate_specialized_headline(self, title: str) -> str:
        upper = title.upper()

        if "TREASURY YIELD" in upper or "TREASURY YIELDS" in upper:
            return self._translate_treasury_yields(title)

        if "PMI" in upper or re.search(r"\bISM\b", upper):
            return self._translate_diffusion_index(title)

        if any(term in upper for term in ("CPI", "PPI", "PCE")):
            return self._translate_price_index(title)

        if "PAYROLL" in upper or re.search(r"\bNFP\b", upper):
            return self._translate_payrolls(title)

        if "UNEMPLOYMENT RATE" in upper or "JOBLESS RATE" in upper:
            return self._translate_unemployment(title)

        return ""

    def _translate_diffusion_index(self, title: str) -> str:
        upper = title.upper()
        region = self._detect_region(title)
        sector = self._detect_sector(title)
        index_name = "ISM" if re.search(r"\bISM\b", upper) else "PMI"
        level = self._extract_level(title)
        verb = self._detect_direction(title)
        subject_parts = [part for part in (region, sector, index_name) if part]
        subject = " ".join(subject_parts).strip() or index_name

        if level is None:
            if verb == "상승":
                return f"{subject} 상승"
            if verb == "하락":
                return f"{subject} 하락"
            return f"{subject} 발표"

        if level >= 50:
            if verb == "상승":
                return f"{subject}가 {level:.1f}로 올라 50을 웃돌았다"
            if verb == "하락":
                return f"{subject}가 {level:.1f}로 낮아졌지만 50은 웃돌았다"
            return f"{subject}가 {level:.1f}를 기록했다"

        if verb == "상승":
            return f"{subject}가 {level:.1f}로 상승했지만 여전히 50을 밑돌았다"
        if verb == "하락":
            return f"{subject}가 {level:.1f}로 하락하며 50을 밑돌았다"
        return f"{subject}가 {level:.1f}로 여전히 50을 밑돌았다"

    def _translate_price_index(self, title: str) -> str:
        upper = title.upper()
        region = self._detect_region(title) or "해당국"
        month = self._detect_month(title)
        index_name = next(name for name in ("CPI", "PPI", "PCE") if name in upper)
        relation = self._detect_expectation_relation(title)
        direction = self._detect_direction(title)

        subject = " ".join(part for part in (region, month, index_name) if part)
        if relation == "softer":
            return f"{subject}가 예상보다 덜 올랐다"
        if relation == "hotter":
            return f"{subject}가 예상보다 더 강했다"
        if direction == "상승":
            return f"{subject}가 상승했다"
        if direction == "하락":
            return f"{subject}가 하락했다"
        return f"{subject} 발표"

    def _translate_payrolls(self, title: str) -> str:
        relation = self._detect_expectation_relation(title)
        if relation == "hotter":
            return "미국 비농업 고용이 예상보다 강했다"
        if relation == "softer":
            return "미국 비농업 고용이 예상보다 약했다"
        direction = self._detect_direction(title)
        if direction == "상승":
            return "미국 비농업 고용이 늘었다"
        if direction == "하락":
            return "미국 비농업 고용이 줄었다"
        return "미국 비농업 고용 지표 발표"

    def _translate_unemployment(self, title: str) -> str:
        region = self._detect_region(title) or "해당국"
        direction = self._detect_direction(title)
        if direction == "상승":
            return f"{region} 실업률이 올랐다"
        if direction == "하락":
            return f"{region} 실업률이 낮아졌다"
        return f"{region} 실업률 발표"

    def _translate_treasury_yields(self, title: str) -> str:
        relation = self._detect_expectation_relation(title)
        direction = self._detect_direction(title)
        if "PAYROLL" in title.upper() or re.search(r"\bNFP\b", title.upper()):
            if relation == "hotter":
                return "고용지표 호조에 미 국채금리 상승"
            if relation == "softer":
                return "고용지표 부진에 미 국채금리 하락"
        if direction == "상승":
            return "미 국채금리 상승"
        if direction == "하락":
            return "미 국채금리 하락"
        return "미 국채금리 동향"

    def _build_explanation(self, title: str, translated_title: str) -> str:
        parts = [self._build_summary(title, translated_title)]
        term_note = self._build_term_note(title)
        if term_note:
            parts.append(term_note)
        market_view = self._build_market_view(title)
        if market_view:
            parts.append(market_view)
        return " ".join(part for part in parts if part).strip()

    def _build_summary(self, title: str, translated_title: str) -> str:
        upper = title.upper()
        level = self._extract_level(title)

        if "PMI" in upper or re.search(r"\bISM\b", upper):
            if level is not None and level < 50:
                return "지표가 반등했더라도 기준선 50 아래면 경기 위축 구간이 이어진다는 뜻이다."
            if level is not None and level >= 50:
                return "지표가 50을 웃돌면 경기 확장 흐름으로 읽는 경우가 많다."
            return "확산지수 흐름을 통해 경기의 강약을 가늠할 수 있다."

        if any(term in upper for term in ("CPI", "PPI", "PCE")):
            relation = self._detect_expectation_relation(title)
            if relation == "softer":
                return "물가 압력이 예상보다 덜 강했다는 해석이 가능하다."
            if relation == "hotter":
                return "물가 압력이 예상보다 강했다는 신호로 볼 수 있다."
            return "물가 지표는 금리 전망에 직접적인 영향을 주는 경우가 많다."

        if "PAYROLL" in upper or re.search(r"\bNFP\b", upper):
            relation = self._detect_expectation_relation(title)
            if relation == "hotter":
                return "고용이 예상보다 강하면 연준의 금리 인하 기대가 늦춰질 수 있다."
            if relation == "softer":
                return "고용이 예상보다 약하면 경기 둔화 우려와 함께 금리 부담이 완화될 수 있다."
            return "고용 지표는 금리와 달러 방향에 민감하게 반영되기 쉽다."

        if "UNEMPLOYMENT RATE" in upper or "JOBLESS RATE" in upper:
            return "실업률은 고용 시장의 냉각 또는 과열 정도를 보여주는 핵심 지표다."

        if "TREASURY YIELD" in upper or "TREASURY YIELDS" in upper:
            return "국채금리 움직임은 주식과 달러 등 다른 자산 가격에도 바로 영향을 줄 수 있다."

        return f"핵심은 {translated_title}는 점이다."

    def _build_term_note(self, title: str) -> str:
        upper = title.upper()
        level = self._extract_level(title)

        if "PMI" in upper:
            if level is not None:
                return (
                    f"PMI는 기업 구매담당자 경기지수로 50이 기준선이며, {level:.1f}는 "
                    f"{self._describe_diffusion_level(level)}으로 읽는다."
                )
            return "PMI는 기업 구매담당자 경기지수로 50을 넘으면 확장, 50을 밑돌면 위축으로 읽는다."

        if re.search(r"\bISM\b", upper):
            if level is not None:
                return (
                    f"ISM은 미국 공급관리협회 경기지수로 50이 기준선이며, {level:.1f}는 "
                    f"{self._describe_diffusion_level(level)}으로 해석한다."
                )
            return "ISM은 미국 공급관리협회 경기지수로 50을 넘으면 확장, 50을 밑돌면 위축으로 읽는다."

        if "CPI" in upper:
            return "CPI는 소비자물가 지표로, 예상보다 높으면 긴축 우려를 키우고 낮으면 금리 부담을 덜 수 있다."

        if "PPI" in upper:
            return "PPI는 생산자물가 지표로, 기업 원가와 향후 소비자물가 압력을 가늠할 때 본다."

        if "PCE" in upper:
            return "PCE는 연준이 중요하게 보는 물가 지표라서 금리 기대 변화에 특히 민감하다."

        if "PAYROLL" in upper or re.search(r"\bNFP\b", upper):
            return "NFP는 미국 비농업 고용지표로, 고용이 강하면 금리 인하 기대가 늦춰질 수 있다."

        if "UNEMPLOYMENT RATE" in upper or "JOBLESS RATE" in upper:
            return "실업률이 오르면 고용 둔화 신호로, 낮아지면 노동시장이 여전히 타이트하다는 뜻으로 읽힌다."

        if "TREASURY YIELD" in upper or "TREASURY YIELDS" in upper:
            return "국채금리는 채권 수익률을 뜻하며, 오르면 보통 할인율 부담과 달러 강세 압력을 함께 키운다."

        if "BASIS POINT" in upper or "BPS" in upper or "BP " in upper:
            return "1bp는 0.01%포인트라서 25bp는 0.25%포인트 변화를 뜻한다."

        return ""

    def _build_market_view(self, title: str) -> str:
        upper = title.upper()
        level = self._extract_level(title)
        relation = self._detect_expectation_relation(title)
        direction = self._detect_direction(title)

        if "PMI" in upper or re.search(r"\bISM\b", upper):
            if level is not None and level < 50:
                return "시장에선 경기 회복 신호라기보다 부진 완화 정도로 해석할 가능성이 크다."
            if level is not None and level >= 50:
                return "시장에선 경기 확장 흐름이 이어진다는 쪽에 무게를 둘 수 있다."

        if any(term in upper for term in ("CPI", "PPI", "PCE")):
            if relation == "hotter":
                return "시장에선 금리 부담이 길어질 수 있는 재료로 볼 수 있다."
            if relation == "softer":
                return "시장에선 금리 부담 완화 쪽으로 해석할 수 있다."

        if "PAYROLL" in upper or re.search(r"\bNFP\b", upper):
            if relation == "hotter":
                return "시장에선 달러 강세와 채권금리 상승 재료로 읽을 수 있다."
            if relation == "softer":
                return "시장에선 금리 인하 기대를 되살리는 재료로 볼 수 있다."

        if "UNEMPLOYMENT RATE" in upper or "JOBLESS RATE" in upper:
            if direction == "상승":
                return "시장에선 고용 둔화 신호로 받아들일 수 있다."
            if direction == "하락":
                return "시장에선 노동시장이 여전히 견조하다는 쪽으로 해석할 수 있다."

        if "TREASURY YIELD" in upper or "TREASURY YIELDS" in upper:
            if direction == "상승":
                return "시장에선 성장주와 장기자산에 부담 요인이 될 수 있다."
            if direction == "하락":
                return "시장에선 주식 밸류에이션 부담을 덜어주는 쪽으로 볼 수 있다."

        return ""

    def _postprocess_translated_title(self, text: str) -> str:
        cleaned = " ".join(text.split())
        replacements = (
            ("Treasury yields", "미 국채금리"),
            ("Treasury yield", "미 국채금리"),
            ("Treasury", "미 국채"),
            ("payrolls", "비농업 고용"),
            ("Payrolls", "비농업 고용"),
            ("threshold", "기준선"),
            ("prior", "전월"),
        )
        for before, after in replacements:
            cleaned = cleaned.replace(before, after)

        cleaned = re.sub(r"\b([0-9]+)\s+월\b", r"\1월", cleaned)
        cleaned = re.sub(r"\s+,", ",", cleaned)
        return cleaned.strip(" ,.")

    @staticmethod
    def _detect_region(title: str) -> str:
        patterns = (
            (r"\bEUROZONE\b", "유로존"),
            (r"\bU\.?S\.?\b|\bUS\b|\bUNITED STATES\b", "미국"),
            (r"\bCHINA\b", "중국"),
            (r"\bJAPAN\b", "일본"),
            (r"\bUK\b|\bUNITED KINGDOM\b|\bBRITAIN\b", "영국"),
            (r"\bGERMANY\b", "독일"),
        )
        upper = title.upper()
        for pattern, label in patterns:
            if re.search(pattern, upper):
                return label
        return ""

    @staticmethod
    def _detect_sector(title: str) -> str:
        upper = title.upper()
        if "MANUFACTURING" in upper:
            return "제조업"
        if "SERVICES" in upper or re.search(r"\bSERVICE\b", upper):
            return "서비스업"
        if "COMPOSITE" in upper:
            return "종합"
        return ""

    @staticmethod
    def _detect_month(title: str) -> str:
        month_map = {
            "JANUARY": "1월",
            "FEBRUARY": "2월",
            "MARCH": "3월",
            "APRIL": "4월",
            "MAY": "5월",
            "JUNE": "6월",
            "JULY": "7월",
            "AUGUST": "8월",
            "SEPTEMBER": "9월",
            "OCTOBER": "10월",
            "NOVEMBER": "11월",
            "DECEMBER": "12월",
        }
        upper = title.upper()
        for english, korean in month_map.items():
            if english in upper:
                return korean
        return ""

    @staticmethod
    def _detect_expectation_relation(title: str) -> str:
        lower = title.lower()
        hotter_phrases = (
            "above expected",
            "above expectations",
            "more than expected",
            "hotter than expected",
            "beats expectations",
            "beat expectations",
            "stronger than expected",
            "higher than expected",
        )
        softer_phrases = (
            "below expected",
            "below expectations",
            "less than expected",
            "softer than expected",
            "misses expectations",
            "missed expectations",
            "weaker than expected",
            "lower than expected",
        )
        if any(phrase in lower for phrase in hotter_phrases):
            return "hotter"
        if any(phrase in lower for phrase in softer_phrases):
            return "softer"
        return ""

    @staticmethod
    def _detect_direction(title: str) -> str:
        lower = title.lower()
        up_phrases = ("rises", "rose", "higher", "gains", "edges higher", "up ")
        down_phrases = ("falls", "fell", "lower", "drops", "edges lower", "down ")
        if any(phrase in lower for phrase in up_phrases):
            return "상승"
        if any(phrase in lower for phrase in down_phrases):
            return "하락"
        return ""

    @staticmethod
    def _extract_level(title: str) -> float | None:
        matches = re.findall(r"\b([1-9][0-9](?:\.[0-9]+)?)\b", title)
        for match in matches:
            value = float(match)
            if value <= 70:
                return value
        return None

    @staticmethod
    def _describe_diffusion_level(level: float) -> str:
        if level >= 50:
            return "경기 확장 구간"
        if level >= 48:
            return "위축이 심하지 않은 둔화 구간"
        if level >= 45:
            return "부진이 비교적 뚜렷한 위축 구간"
        return "경기 둔화 압력이 강한 위축 구간"
