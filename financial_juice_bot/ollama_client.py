from __future__ import annotations

from datetime import UTC
import json
import logging
import re

import httpx

from .models import NewsInsight, NewsItem


class OllamaError(RuntimeError):
    """Raised when Ollama cannot generate a response."""


logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout_seconds: float, temperature: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_seconds,
            headers={"Content-Type": "application/json"},
        )

    async def translate_and_explain(self, item: NewsItem) -> NewsInsight:
        published_utc = item.published_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        prompt = self._build_prompt(item, published_utc)
        schema = {
            "type": "object",
            "properties": {
                "translated_title": {"type": "string"},
                "summary": {"type": "string"},
                "term_note": {"type": "string"},
                "stock_impact": {"type": "string"},
                "bond_impact": {"type": "string"},
                "fx_impact": {"type": "string"},
                "commodity_impact": {"type": "string"},
                "market_view": {"type": "string"},
            },
            "required": [
                "translated_title",
                "summary",
                "term_note",
                "stock_impact",
                "bond_impact",
                "fx_impact",
                "commodity_impact",
                "market_view",
            ],
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You turn real-time English market headlines into natural Korean financial-wire "
                        "updates. Sound like a Korean market analyst, not a general translator."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": schema,
            "options": {"temperature": self.temperature},
        }

        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            content = str(data["message"]["content"])
            parsed = self._parse_json_content(content)
        except httpx.ReadTimeout as exc:
            raise OllamaError(
                "Ollama 응답 시간이 초과됐습니다. "
                "현재 모델이 느릴 수 있으니 .env의 OLLAMA_TIMEOUT_SECONDS 값을 더 크게 늘려 주세요."
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise OllamaError(
                f"Ollama request failed with status {exc.response.status_code}: {detail}"
            ) from exc
        except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Failed to parse Ollama response: {exc}") from exc

        translated_title = self._normalize_labeled_sentence(
            parsed.get("translated_title", ""),
            prefixes=("translated_title", "headline", "번역", "제목"),
        )
        summary = self._normalize_labeled_sentence(
            parsed.get("summary", ""),
            prefixes=("summary", "핵심", "요약"),
        )
        term_note = self._normalize_labeled_sentence(
            parsed.get("term_note", ""),
            prefixes=("term_note", "용어", "용어 설명"),
            allow_empty=True,
        )
        stock_impact = self._normalize_labeled_sentence(
            parsed.get("stock_impact", ""),
            prefixes=("stock_impact", "stock", "stocks", "equities", "주식"),
            allow_empty=True,
        )
        bond_impact = self._normalize_labeled_sentence(
            parsed.get("bond_impact", ""),
            prefixes=("bond_impact", "bond", "bonds", "rates", "채권", "금리"),
            allow_empty=True,
        )
        fx_impact = self._normalize_labeled_sentence(
            parsed.get("fx_impact", ""),
            prefixes=("fx_impact", "fx", "foreign exchange", "currencies", "외환", "달러"),
            allow_empty=True,
        )
        commodity_impact = self._normalize_labeled_sentence(
            parsed.get("commodity_impact", ""),
            prefixes=("commodity_impact", "commodity", "commodities", "원자재", "상품"),
            allow_empty=True,
        )
        market_view = self._normalize_labeled_sentence(
            parsed.get("market_view", ""),
            prefixes=("market_view", "view", "종합", "시장 해석", "해석"),
            allow_empty=True,
        )

        scenario = self._classify_headline(item.title)
        fallback_fields: list[str] = []

        if not summary:
            summary = self._build_fallback_summary(item.title, translated_title, scenario)
            if summary:
                fallback_fields.append("summary")

        if not translated_title:
            translated_title = self._build_fallback_translated_title(item.title, summary)
            if translated_title:
                fallback_fields.append("translated_title")

        if not term_note:
            term_note = self._build_fallback_term_note(item.title)
            if term_note:
                fallback_fields.append("term_note")

        if not stock_impact:
            stock_impact = self._build_fallback_asset_impact(scenario, "stock")
            fallback_fields.append("stock_impact")

        if not bond_impact:
            bond_impact = self._build_fallback_asset_impact(scenario, "bond")
            fallback_fields.append("bond_impact")

        if not fx_impact:
            fx_impact = self._build_fallback_asset_impact(scenario, "fx")
            fallback_fields.append("fx_impact")

        if not commodity_impact:
            commodity_impact = self._build_fallback_asset_impact(scenario, "commodity")
            fallback_fields.append("commodity_impact")

        if not market_view:
            market_view = self._build_fallback_market_view(scenario)
            fallback_fields.append("market_view")

        explanation = self._format_explanation(
            summary=summary,
            term_note=term_note,
            stock_impact=stock_impact,
            bond_impact=bond_impact,
            fx_impact=fx_impact,
            commodity_impact=commodity_impact,
            market_view=market_view,
        )

        if fallback_fields:
            logger.warning(
                "Ollama response was incomplete for guid=%s. Filled locally: %s",
                item.guid,
                ", ".join(fallback_fields),
            )

        if not translated_title or not explanation:
            raise OllamaError("Ollama returned no usable translation output.")

        return NewsInsight(
            guid=item.guid,
            title=item.title,
            translated_title=translated_title,
            explanation=explanation,
            link=item.link,
            published_at=item.published_at,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _build_prompt(self, item: NewsItem, published_utc: str) -> str:
        return (
            "You are a Korean macro and markets news editor writing for investors.\n"
            "Rewrite the English headline into natural Korean financial-news style and add a richer market note.\n"
            "Rules:\n"
            "- translated_title: one sharp Korean market headline, not a literal translation.\n"
            "- summary: 1 or 2 Korean sentences explaining the event in plain, natural Korean.\n"
            "- term_note: if the headline contains market jargon, a key macro term, a threshold, or an important number, explain it in 1 to 3 Korean sentences. Otherwise return an empty string.\n"
            "- stock_impact: exactly 1 Korean sentence on likely equity-market reaction.\n"
            "- bond_impact: exactly 1 Korean sentence on likely bond or yield reaction.\n"
            "- fx_impact: exactly 1 Korean sentence on likely FX or dollar reaction.\n"
            "- commodity_impact: exactly 1 Korean sentence on likely commodity reaction such as oil, gold, or industrial commodities.\n"
            "- market_view: 1 or 2 Korean sentences synthesizing the overall read.\n"
            "- Do not invent facts beyond the headline.\n"
            "- Every asset-impact field must be non-empty. If relevance is limited, say it is likely limited or indirect.\n"
            "- If the headline includes PMI, CPI, PPI, PCE, NFP, payrolls, ISM, GDP, unemployment rate, jobless rate, Treasury yield, basis points, OPEC, flash, or a threshold like 50, term_note should not be empty.\n"
            "- For PMI or ISM levels, explain the 50 threshold and whether the level signals expansion, borderline, or contraction.\n"
            "- Write like a Korean brokerage morning note or market desk update.\n"
            "- Avoid robotic phrases such as '시장적 영향은 아직 확인되지 않았다' or '관련 소식이다'.\n"
            "- Return only JSON.\n\n"
            "Style examples:\n"
            "English headline: US March CPI rises less than expected\n"
            "JSON: {\"translated_title\":\"미국 3월 CPI가 예상보다 덜 올랐다\",\"summary\":\"미국 소비자물가 상승세가 시장 예상보다 완만했다.\",\"term_note\":\"CPI는 소비자물가를 보여주는 대표 인플레이션 지표다. 예상보다 낮게 나오면 긴축 우려를 덜 수 있다.\",\"stock_impact\":\"주식은 금리 부담 완화 기대가 커지면 성장주 중심으로 우호적으로 해석될 수 있다.\",\"bond_impact\":\"채권은 금리 하락 기대로 강세를 보이고 국채금리는 내리기 쉽다.\",\"fx_impact\":\"외환에선 달러 강세가 다소 진정될 수 있다.\",\"commodity_impact\":\"금은 실질금리 부담 완화 기대에 지지를 받을 수 있고, 경기 민감 원자재는 후속 지표 확인이 필요하다.\",\"market_view\":\"시장에선 연준 인하 기대를 다시 키울 수 있는 재료로 본다.\"}\n\n"
            "English headline: OPEC+ says it will maintain current output policy\n"
            "JSON: {\"translated_title\":\"OPEC+, 기존 산유 정책 유지\",\"summary\":\"OPEC+가 현재의 원유 생산 방침을 그대로 유지하기로 했다.\",\"term_note\":\"OPEC+는 주요 산유국 협의체로 생산량 조절을 통해 유가와 공급 전망에 큰 영향을 준다.\",\"stock_impact\":\"주식은 에너지 업종에는 중립적이지만, 시장 전체로 보면 기존 유가 경로를 크게 바꾸지 않는 재료로 읽힐 수 있다.\",\"bond_impact\":\"채권은 인플레이션 전망에 큰 변화가 없다면 금리 반응도 제한적일 수 있다.\",\"fx_impact\":\"외환에선 산유국 통화와 달러에 미치는 영향이 크지 않을 가능성이 있다.\",\"commodity_impact\":\"원자재에선 유가가 기존 공급 전망을 유지하는 쪽으로 해석돼 반응이 제한적일 수 있다.\",\"market_view\":\"시장에선 새 공급 충격이 아니라 기존 경로를 재확인한 뉴스로 볼 가능성이 크다.\"}\n\n"
            "English headline: Eurozone flash manufacturing PMI 47.4 vs 46.8 prior\n"
            "JSON: {\"translated_title\":\"유로존 제조업 PMI 47.4로 반등했지만 여전히 위축 구간\",\"summary\":\"유로존 제조업 PMI가 전월보다 올랐지만 기준선 50에는 못 미쳤다.\",\"term_note\":\"PMI는 기업 구매담당자 경기지수로, 50을 넘으면 경기 확장, 50을 밑돌면 위축으로 읽는다. Flash는 정식 확정치보다 먼저 나오는 예비치다.\",\"stock_impact\":\"주식은 제조업 부진 완화 신호로 일부 경기민감주에 안도감을 줄 수 있지만, 전반적 강세 재료로 보긴 아직 이르다.\",\"bond_impact\":\"채권은 경기 둔화 우려가 완전히 걷히지 않아 금리 상방이 제한될 수 있다.\",\"fx_impact\":\"외환에선 유로 강세 재료가 되더라도 폭은 제한적일 수 있다.\",\"commodity_impact\":\"원자재는 유럽 제조업 수요 기대가 조금 나아졌다는 점에서 산업재에는 약한 지지 요인이 될 수 있다.\",\"market_view\":\"시장에선 바닥 통과 기대와 여전한 위축 신호가 함께 섞인 뉴스로 해석할 수 있다.\"}\n\n"
            f"Additional domain guidance:\n{self._build_domain_guidance(item.title)}\n\n"
            f"Headline: {item.title}\n"
            f"Published at: {published_utc}\n"
            f"Source URL: {item.link}\n"
        )

    @staticmethod
    def _parse_json_content(content: str) -> dict[str, object]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match is None:
                raise
            parsed = json.loads(match.group(0))

        if not isinstance(parsed, dict):
            raise ValueError("Ollama response JSON was not an object.")
        return parsed

    @classmethod
    def _normalize_labeled_sentence(
        cls,
        text: object,
        prefixes: tuple[str, ...],
        allow_empty: bool = False,
    ) -> str:
        cleaned = " ".join(str(text).split())
        cleaned = cleaned.strip()
        cleaned = re.sub(r"^[\-\*\u2022]+\s*", "", cleaned)
        if cleaned.lower() in {"none", "n/a", "na", "null"} or cleaned in {
            "",
            "없음",
            "해당 없음",
            "해당없음",
            "무관",
        }:
            cleaned = ""

        if cleaned:
            pattern = r"^(?:" + "|".join(re.escape(prefix) for prefix in prefixes) + r")\s*[:：-]\s*"
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

        return cls._normalize_sentence(cleaned, allow_empty=allow_empty)

    @staticmethod
    def _normalize_sentence(text: str, allow_empty: bool = False) -> str:
        cleaned = " ".join(text.split())
        cleaned = cleaned.replace("발표한 바에 따르면", "")
        cleaned = cleaned.replace("관련 소식이다.", "")
        cleaned = cleaned.strip(" :")
        if not cleaned and allow_empty:
            return ""
        if cleaned and cleaned[-1] not in ".!?":
            cleaned += "."
        return cleaned

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)

    @classmethod
    def _build_domain_guidance(cls, headline: str) -> str:
        notes = [
            "- Asset impacts must separately cover stocks, bonds, FX, and commodities.",
            "- If one asset class is only indirectly affected, say the impact is limited or secondary instead of leaving it blank.",
        ]
        upper_headline = headline.upper()

        if "PMI" in upper_headline:
            notes.append(
                "- PMI is a purchasing managers index. Explain the 50 threshold and whether the number signals expansion or contraction."
            )
        if "ISM" in upper_headline:
            notes.append(
                "- ISM is a US business survey index. Explain the 50 threshold if a level is shown."
            )
        if cls._contains_any(upper_headline, ("CPI", "PPI", "PCE")):
            notes.append(
                "- Inflation indicators usually flow through rates, the dollar, and growth-stock valuation."
            )
        if cls._contains_any(upper_headline, ("NFP", "PAYROLLS", "UNEMPLOYMENT RATE", "JOBLESS RATE")):
            notes.append(
                "- Labor data usually affects rate expectations, Treasury yields, the dollar, and equity risk appetite."
            )
        if cls._contains_any(upper_headline, ("OPEC", "CRUDE", "OIL", "BRENT", "WTI")):
            notes.append(
                "- Energy headlines should mention oil first, then the inflation and rates transmission channel."
            )
        if cls._contains_any(
            upper_headline,
            ("ATTACK", "MISSILE", "DRONE", "WAR", "SANCTION", "NUCLEAR", "RADIOLOGICAL"),
        ):
            notes.append(
                "- Geopolitical risk should mention risk-off, safe-haven bonds, dollar or yen strength, and oil or gold reaction when relevant."
            )
        if "FLASH" in upper_headline:
            notes.append("- Flash data means a preliminary estimate that can later be revised.")

        return "\n".join(notes)

    @classmethod
    def _format_explanation(
        cls,
        *,
        summary: str,
        term_note: str,
        stock_impact: str,
        bond_impact: str,
        fx_impact: str,
        commodity_impact: str,
        market_view: str,
    ) -> str:
        lines = [f"핵심: {summary}"]
        if term_note:
            lines.append(f"용어 설명: {term_note}")
        lines.append("자산 영향:")
        lines.append(f"- 주식: {stock_impact}")
        lines.append(f"- 채권: {bond_impact}")
        lines.append(f"- 외환: {fx_impact}")
        lines.append(f"- 원자재: {commodity_impact}")
        lines.append(f"종합: {market_view}")
        return "\n".join(lines)

    @classmethod
    def _build_fallback_translated_title(cls, headline: str, summary: str) -> str:
        candidate = summary.rstrip(".!? ").strip()
        if candidate and "핵심 내용" not in candidate and "관련 내용" not in candidate:
            return candidate
        return headline.strip()

    @classmethod
    def _build_fallback_summary(cls, headline: str, translated_title: str, scenario: str) -> str:
        upper_headline = headline.upper()

        if scenario == "geopolitical_energy":
            return cls._normalize_sentence(
                "중동발 지정학 이벤트나 공격 소식으로 에너지 시설과 공급 차질 우려가 부각됐다"
            )
        if scenario == "geopolitical":
            return cls._normalize_sentence("지정학 긴장이나 안전 우려를 키우는 소식이 전해졌다")
        if scenario == "diplomacy":
            return cls._normalize_sentence("관련국 고위 인사 간 접촉이나 협의가 이뤄졌다는 소식이다")
        if scenario.startswith("inflation_"):
            return cls._normalize_sentence("물가 지표가 금리 기대에 영향을 줄 수 있는 재료로 제시됐다")
        if scenario.startswith("growth_"):
            return cls._normalize_sentence("경기 흐름을 가늠할 수 있는 성장 지표 관련 소식이 나왔다")
        if scenario.startswith("labor_"):
            return cls._normalize_sentence("고용 흐름과 통화정책 기대를 자극할 수 있는 노동시장 소식이 전해졌다")
        if scenario.startswith("rates_"):
            return cls._normalize_sentence("금리 경로와 채권시장 반응에 직접 연결될 수 있는 소식이다")
        if scenario.startswith("oil_"):
            return cls._normalize_sentence("원유 공급과 에너지 가격 전망에 영향을 줄 수 있는 소식이다")

        if cls._contains_any(
            upper_headline,
            ("DRONE", "MISSILE", "ATTACK", "AIRSTRIKE", "STRIKE", "FIRE", "EXPLOSION"),
        ):
            return cls._normalize_sentence("공격이나 충돌과 관련한 피해 상황이 전해졌다")

        if cls._contains_any(upper_headline, ("PHONE CALL", "CALLED", "TALKS", "MEETING", "MET")):
            return cls._normalize_sentence("관련 인사들이 통화 또는 협의를 진행했다")

        if translated_title and translated_title != headline:
            return cls._normalize_sentence(f"{translated_title} 관련 소식이다")

        return cls._normalize_sentence("해당 헤드라인의 핵심 내용이 전해졌다")

    @classmethod
    def _build_fallback_term_note(cls, headline: str) -> str:
        upper_headline = headline.upper()
        level = cls._extract_index_level(headline)
        notes: list[str] = []

        if "PMI" in upper_headline:
            zone = cls._describe_diffusion_index(level)
            if zone:
                notes.append(
                    cls._normalize_sentence(
                        f"PMI는 기업 구매담당자 경기지수로, 50을 넘으면 경기 확장인데 {level:.1f}는 {zone}으로 읽는다"
                    )
                )
            else:
                notes.append(
                    cls._normalize_sentence(
                        "PMI는 기업 구매담당자 경기지수로, 50을 넘으면 경기 확장, 50을 밑돌면 위축으로 읽는다"
                    )
                )

        if "ISM" in upper_headline:
            zone = cls._describe_diffusion_index(level)
            if zone:
                notes.append(
                    cls._normalize_sentence(
                        f"ISM은 미국 공급관리협회 경기지수로, 50이 기준선인데 {level:.1f}는 {zone}으로 해석한다"
                    )
                )
            else:
                notes.append(
                    cls._normalize_sentence(
                        "ISM은 미국 공급관리협회 경기지수로, 50을 넘으면 확장, 50을 밑돌면 위축으로 읽는다"
                    )
                )

        if "CPI" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "CPI는 소비자물가를 보여주는 대표 인플레이션 지표로, 예상보다 높으면 긴축 우려를 키우고 낮으면 금리 부담을 덜 수 있다"
                )
            )
        if "PPI" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "PPI는 생산자물가 지표로, 기업의 원가 부담과 향후 소비자물가 압력을 가늠할 때 함께 본다"
                )
            )
        if "PCE" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "PCE는 연준이 중요하게 보는 물가 지표로, 금리 인하 기대와 직접 연결되기 쉽다"
                )
            )
        if "NFP" in upper_headline or "NONFARM" in upper_headline or "PAYROLLS" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "NFP는 미국 비농업부문 고용지표로, 고용 강도와 연준 정책 기대를 동시에 자극하는 핵심 지표다"
                )
            )
        if "GDP" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "GDP는 경제 성장률을 보여주는 대표 지표로, 강하면 경기 확장 기대를 높이고 약하면 둔화 우려를 키울 수 있다"
                )
            )
        if "UNEMPLOYMENT RATE" in upper_headline or "JOBLESS RATE" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "실업률은 고용시장의 체온을 보여주는 지표로, 오르면 노동시장 둔화, 낮아지면 고용 타이트닝 신호로 읽힌다"
                )
            )
        if "TREASURY YIELD" in upper_headline or " YIELD" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "국채금리는 채권 수익률을 뜻하며, 오르면 긴축과 할인율 부담, 내리면 금리 완화 기대가 반영되는 경우가 많다"
                )
            )
        if "BASIS POINT" in upper_headline or "BPS" in upper_headline or "BP " in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "1bp는 0.01%포인트를 뜻해, 25bp 변화는 금리 0.25%포인트 변동을 의미한다"
                )
            )
        if "OPEC+" in upper_headline or "OPEC" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "OPEC+는 주요 산유국 협의체로, 생산량 조절을 통해 유가와 에너지 공급 전망에 큰 영향을 준다"
                )
            )
        if "FLASH" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "Flash 수치는 정식 확정치보다 먼저 나오는 예비치라 이후 수정될 수 있다"
                )
            )
        if "TARIFF" in upper_headline or "TARIFFS" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "관세는 수입품에 붙는 세금으로, 물가와 교역, 기업 마진에 함께 영향을 줄 수 있다"
                )
            )
        if "SANCTION" in upper_headline or "SANCTIONS" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "제재는 금융이나 무역 제약을 뜻해 공급망과 자금 흐름을 흔들 수 있는 변수다"
                )
            )
        if "RADIOLOGICAL" in upper_headline:
            notes.append(
                cls._normalize_sentence(
                    "Radiological release는 방사성 물질 유출 위험을 뜻해 원전 안전성과 주변 지역 오염 우려를 함께 자극한다"
                )
            )

        return " ".join(notes[:2]).strip()

    @classmethod
    def _build_fallback_asset_impact(cls, scenario: str, asset: str) -> str:
        mappings = {
            "inflation_hot": {
                "stock": "주식은 긴축 우려가 커지면 성장주와 금리 민감 업종에 부담으로 작용할 수 있다.",
                "bond": "채권은 기준금리 상방 우려로 약세를 보이기 쉽고 국채금리는 오르는 쪽으로 반응할 수 있다.",
                "fx": "외환에선 금리 격차 기대가 달러 강세 재료로 읽힐 수 있다.",
                "commodity": "원자재는 달러 강세가 부담이지만 인플레이션 헤지 성격이 있는 금은 해석이 엇갈릴 수 있다.",
            },
            "inflation_cool": {
                "stock": "주식은 금리 부담 완화 기대가 커지면 성장주와 기술주 중심으로 우호적으로 받아들일 수 있다.",
                "bond": "채권은 금리 하락 기대로 강세를 보이고 국채금리는 내리기 쉬운 재료다.",
                "fx": "외환에선 달러 강세가 다소 진정되거나 약세 압력을 받을 수 있다.",
                "commodity": "원자재에선 금이 실질금리 부담 완화 기대에 지지를 받을 수 있고 산업재는 후속 경기지표 확인이 필요하다.",
            },
            "inflation_neutral": {
                "stock": "주식은 물가 방향성보다 세부 수치와 연준 해석을 더 확인하려는 흐름이 나타날 수 있다.",
                "bond": "채권은 금리 경로를 새로 확신하기 어려워 반응이 제한적일 수 있다.",
                "fx": "외환에선 달러 방향성이 크게 열리기보다 다른 지표를 더 기다릴 가능성이 있다.",
                "commodity": "원자재는 물가 해석이 뚜렷하지 않으면 영향도 제한적일 수 있다.",
            },
            "growth_strong": {
                "stock": "주식은 경기민감주와 산업재 쪽에 우호적일 수 있지만 금리 상승 부담이 함께 따라붙을 수 있다.",
                "bond": "채권은 성장 기대 회복으로 약세를 보이기 쉽고 국채금리는 상승 압력을 받을 수 있다.",
                "fx": "외환에선 성장 기대가 통화 강세 재료가 될 수 있고 달러도 금리 기대와 함께 지지될 수 있다.",
                "commodity": "원자재는 실물 수요 기대가 살아나면 산업재와 에너지에 지지 요인이 될 수 있다.",
            },
            "growth_weak": {
                "stock": "주식은 경기민감 업종에 부담이 될 수 있고 방어주 선호가 상대적으로 강해질 수 있다.",
                "bond": "채권은 경기 둔화 우려로 안전자산 수요가 붙으면 강세를 보이고 금리는 내리기 쉽다.",
                "fx": "외환에선 위험회피 흐름이 강해지면 달러 같은 안전통화 선호가 나타날 수 있다.",
                "commodity": "원자재는 실물 수요 둔화 우려로 산업재와 에너지에 부담이 될 수 있다.",
            },
            "growth_neutral": {
                "stock": "주식은 경기 방향을 단정하기 어려워 업종별로 엇갈린 반응이 나올 수 있다.",
                "bond": "채권은 성장 우려와 금리 부담 사이에서 뚜렷한 방향 없이 움직일 수 있다.",
                "fx": "외환에선 성장 해석이 분명하지 않으면 주요 통화 반응도 제한적일 수 있다.",
                "commodity": "원자재는 수요 전망을 재평가하는 수준의 제한적 반응에 그칠 수 있다.",
            },
            "labor_strong": {
                "stock": "주식은 경기 자신감에는 도움이 되지만 금리 부담이 커지면 성장주에는 부담이 될 수 있다.",
                "bond": "채권은 노동시장 과열 해석이 붙으면 약세를 보이고 금리는 오르기 쉽다.",
                "fx": "외환에선 연준 긴축 기대가 높아지면 달러 강세 재료로 연결될 수 있다.",
                "commodity": "원자재는 경기 자신감 측면에서 산업재에는 우호적일 수 있지만 금은 금리 부담을 받을 수 있다.",
            },
            "labor_weak": {
                "stock": "주식은 경기 둔화 우려가 부담이지만 금리 인하 기대가 커지면 성장주에는 오히려 버팀목이 될 수 있다.",
                "bond": "채권은 연준 완화 기대로 강세를 보이고 국채금리는 내려가기 쉽다.",
                "fx": "외환에선 달러가 금리 기대 후퇴로 약해질 수 있지만 위험회피가 강하면 낙폭이 제한될 수 있다.",
                "commodity": "원자재는 경기 둔화 우려로 산업재에는 부담이고 금은 금리 하락 기대에 지지를 받을 수 있다.",
            },
            "labor_neutral": {
                "stock": "주식은 고용 해석이 엇갈리면 업종별로 선별적인 반응이 나올 수 있다.",
                "bond": "채권은 금리 경로를 바꿀 만큼 강한 신호가 아니면 변동이 제한될 수 있다.",
                "fx": "외환에선 달러 방향성도 다른 물가나 연준 발언을 더 확인하려는 흐름이 이어질 수 있다.",
                "commodity": "원자재는 경기 해석이 뚜렷하지 않으면 영향이 제한적일 수 있다.",
            },
            "rates_hawkish": {
                "stock": "주식은 할인율 부담이 커지면 밸류에이션이 높은 종목 중심으로 압박을 받을 수 있다.",
                "bond": "채권은 금리 상방이 열리면 가격이 약세를 보이고 수익률은 오르기 쉽다.",
                "fx": "외환에선 금리 우위 기대가 달러 강세 재료로 작용할 수 있다.",
                "commodity": "원자재는 달러 강세와 실질금리 부담으로 금에 불리할 수 있고 에너지는 별도 공급 재료를 더 봐야 한다.",
            },
            "rates_dovish": {
                "stock": "주식은 할인율 부담 완화 기대가 생기면 성장주와 위험자산 전반에 우호적일 수 있다.",
                "bond": "채권은 완화 기대로 강세를 보이고 금리는 내려가기 쉬운 구도다.",
                "fx": "외환에선 달러 강세가 약해지거나 조정을 받을 수 있다.",
                "commodity": "원자재에선 금이 실질금리 하락 기대에 상대적으로 유리할 수 있다.",
            },
            "rates_neutral": {
                "stock": "주식은 금리 방향이 선명하지 않으면 다른 실적이나 경기 재료를 더 보려는 흐름이 이어질 수 있다.",
                "bond": "채권은 새로운 정책 신호가 약하면 금리 반응도 제한적일 수 있다.",
                "fx": "외환에선 금리 차별화 기대가 뚜렷하지 않아 달러 반응도 크지 않을 수 있다.",
                "commodity": "원자재는 금리 재료 단독으로는 방향성이 약할 수 있다.",
            },
            "geopolitical_energy": {
                "stock": "주식은 지정학 리스크와 유가 상방 우려가 겹치면 전반적인 위험선호에 부담이 될 수 있다.",
                "bond": "채권은 안전자산 선호가 붙으면 강세를 보이고 금리는 내려갈 가능성이 있다.",
                "fx": "외환에선 달러나 엔화 같은 안전통화 선호가 강해질 수 있다.",
                "commodity": "원자재는 에너지 공급 불안이 부각되면 유가가 오르고 금도 함께 지지받기 쉽다.",
            },
            "geopolitical": {
                "stock": "주식은 위험회피 심리가 강해지면 지수 전반에 부담이 될 수 있다.",
                "bond": "채권은 안전자산 수요가 붙으면서 강세를 보일 가능성이 있다.",
                "fx": "외환에선 달러와 엔화 같은 안전통화가 상대적으로 강세를 보일 수 있다.",
                "commodity": "원자재는 금이 안전자산 성격으로 지지를 받을 수 있고 유가는 에너지 연결성이 낮으면 반응이 제한될 수 있다.",
            },
            "diplomacy": {
                "stock": "주식은 외교 긴장 완화 기대가 생기면 위험자산 심리에 소폭 우호적일 수 있다.",
                "bond": "채권은 안전자산 프리미엄이 일부 되돌려지면 금리 하락 폭이 제한될 수 있다.",
                "fx": "외환에선 달러 같은 안전통화 프리미엄이 다소 완화될 수 있다.",
                "commodity": "원자재는 중동 리스크 프리미엄이 완화되면 유가와 금 상승 압력이 일부 식을 수 있다.",
            },
            "oil_bullish": {
                "stock": "주식은 에너지 업종엔 우호적일 수 있지만 시장 전체로는 인플레이션 부담이 다시 거론될 수 있다.",
                "bond": "채권은 유가 상승이 물가 우려를 자극하면 금리 상방 압력을 받을 수 있다.",
                "fx": "외환에선 원자재 통화에는 일부 우호적일 수 있고 달러에는 인플레 경로를 통해 영향이 번질 수 있다.",
                "commodity": "원자재는 유가가 직접적인 수혜를 받을 수 있고 금도 인플레이션 헤지 논리로 거론될 수 있다.",
            },
            "oil_bearish": {
                "stock": "주식은 유가 부담 완화가 비용 압력을 덜어주면 일부 업종에 우호적으로 해석될 수 있다.",
                "bond": "채권은 에너지발 물가 우려가 줄면 금리 안정에 도움이 될 수 있다.",
                "fx": "외환에선 원자재 통화가 상대적으로 약해질 수 있고 달러 방향은 다른 금리 재료를 함께 봐야 한다.",
                "commodity": "원자재는 유가가 공급 완화 기대로 약세 압력을 받을 수 있다.",
            },
            "oil_neutral": {
                "stock": "주식은 에너지 가격 경로에 큰 변화가 없다면 영향도 제한적일 수 있다.",
                "bond": "채권은 인플레이션 전망 수정 폭이 크지 않다면 금리 반응도 제한적일 수 있다.",
                "fx": "외환에선 산유국 통화와 달러에 미치는 영향도 크지 않을 가능성이 있다.",
                "commodity": "원자재는 유가가 기존 공급 전망을 재확인하는 수준의 반응에 그칠 수 있다.",
            },
            "generic": {
                "stock": "주식은 이 헤드라인만으로 방향성을 단정하기보다 후속 정보와 맥락을 더 확인하려는 반응이 나올 수 있다.",
                "bond": "채권은 금리 경로에 직접 연결되는 재료가 아니라면 영향이 제한적일 수 있다.",
                "fx": "외환에선 달러나 주요 통화의 뚜렷한 방향성보다는 단기 심리 변화 정도만 반영될 수 있다.",
                "commodity": "원자재는 직접적인 수급 정보가 아니면 영향이 간접적이거나 제한적일 수 있다.",
            },
        }
        selected = mappings.get(scenario, mappings["generic"])
        return selected[asset]

    @classmethod
    def _build_fallback_market_view(cls, scenario: str) -> str:
        views = {
            "inflation_hot": "시장에선 금리 인하 기대가 뒤로 밀리면서 전반적인 위험선호가 다소 식을 수 있는 재료로 본다.",
            "inflation_cool": "시장에선 금리 부담 완화와 연준 완화 기대를 되살릴 수 있는 재료로 해석할 수 있다.",
            "inflation_neutral": "시장에선 물가 방향을 새로 확신하기보다 다음 지표와 연준 메시지를 함께 보려는 흐름이 나타날 수 있다.",
            "growth_strong": "시장에선 성장 모멘텀 회복과 금리 부담을 동시에 재평가하는 재료로 볼 수 있다.",
            "growth_weak": "시장에선 경기 둔화 우려가 다시 부각되지만 동시에 금리 인하 기대도 자극하는 혼합 재료로 읽힐 수 있다.",
            "growth_neutral": "시장에선 경기 바닥 기대와 둔화 우려가 함께 남아 있어 해석이 엇갈릴 수 있다.",
            "labor_strong": "시장에선 노동시장 탄탄함이 확인됐다고 보겠지만 그만큼 금리 부담도 다시 계산할 수 있다.",
            "labor_weak": "시장에선 경기 둔화 우려와 완화 기대를 함께 반영하는 재료로 볼 수 있다.",
            "labor_neutral": "시장에선 정책 경로를 바꿀 만큼 강한 신호인지 여부를 추가 데이터로 확인하려 할 가능성이 크다.",
            "rates_hawkish": "시장에선 금융여건이 다시 조여질 수 있다는 쪽으로 해석하기 쉬운 재료다.",
            "rates_dovish": "시장에선 금융여건 완화 기대가 살아나는 쪽으로 받아들일 수 있다.",
            "rates_neutral": "시장에선 금리 방향성에 대한 확신보다 세부 맥락을 더 확인하려는 반응이 나올 수 있다.",
            "geopolitical_energy": "시장에선 지정학 리스크와 에너지 공급 불안을 동시에 가격에 반영하려는 흐름이 나타날 수 있다.",
            "geopolitical": "시장에선 안전자산 선호와 위험자산 변동성 확대 쪽으로 먼저 반응할 가능성이 있다.",
            "diplomacy": "시장에선 긴장 완화 가능성을 가늠하는 재료로 보겠지만 후속 확인 없이는 반응이 과하게 커지지 않을 수 있다.",
            "oil_bullish": "시장에선 유가 상방 압력과 그에 따른 물가 재상승 가능성을 함께 의식할 수 있다.",
            "oil_bearish": "시장에선 에너지발 물가 부담이 완화될 수 있다는 점에 주목할 수 있다.",
            "oil_neutral": "시장에선 기존 공급 경로를 재확인한 소식으로 보고 반응이 제한될 가능성이 있다.",
            "generic": "시장에선 이 헤드라인 하나만으로 방향성을 단정하기보다 후속 뉴스와 가격 반응을 함께 확인하려 할 수 있다.",
        }
        return views.get(scenario, views["generic"])

    @classmethod
    def _classify_headline(cls, headline: str) -> str:
        upper_headline = headline.upper()
        risk_words = (
            "DRONE",
            "MISSILE",
            "ATTACK",
            "AIRSTRIKE",
            "STRIKE",
            "WAR",
            "SANCTION",
            "NUCLEAR",
            "RADIOLOGICAL",
            "EXPLOSION",
            "FIRE",
        )
        diplomacy_words = ("PHONE CALL", "CALLED", "TALKS", "MEETING", "MET", "NEGOTIATION", "CEASEFIRE")
        energy_words = ("OIL", "CRUDE", "BRENT", "WTI", "POWER", "ENERGY", "REFINERY", "DESALINATION")

        if cls._contains_any(upper_headline, diplomacy_words) and not cls._contains_any(
            upper_headline, risk_words
        ):
            return "diplomacy"
        if cls._contains_any(upper_headline, risk_words):
            if cls._contains_any(upper_headline, energy_words):
                return "geopolitical_energy"
            return "geopolitical"
        if cls._contains_any(upper_headline, ("OPEC", "CRUDE", "OIL", "BRENT", "WTI")):
            return f"oil_{cls._infer_oil_direction(upper_headline)}"
        if cls._contains_any(upper_headline, ("CPI", "PPI", "PCE", "INFLATION")):
            return f"inflation_{cls._infer_inflation_direction(upper_headline)}"
        if cls._contains_any(upper_headline, ("UNEMPLOYMENT RATE", "JOBLESS RATE")):
            return f"labor_{cls._infer_unemployment_direction(upper_headline)}"
        if cls._contains_any(upper_headline, ("NFP", "NONFARM", "PAYROLLS", "PAYROLL", "JOBS")):
            return f"labor_{cls._infer_growth_like_direction(upper_headline)}"
        if cls._contains_any(
            upper_headline,
            ("PMI", "ISM", "GDP", "RETAIL SALES", "INDUSTRIAL PRODUCTION", "MANUFACTURING"),
        ):
            return f"growth_{cls._infer_growth_direction(headline)}"
        if cls._contains_any(
            upper_headline,
            ("TREASURY YIELD", " YIELD", "BASIS POINT", "BPS", "RATE HIKE", "RATE CUT"),
        ):
            return f"rates_{cls._infer_rates_direction(upper_headline)}"
        return "generic"

    @classmethod
    def _infer_inflation_direction(cls, upper_headline: str) -> str:
        hot_words = (
            "HIGHER THAN EXPECTED",
            "ABOVE EXPECTED",
            "HOTTER",
            "MORE THAN EXPECTED",
            "STRONGER THAN EXPECTED",
            "ACCELERATES",
            "ACCELERATED",
            "JUMPS",
            "JUMPED",
            "SURGES",
            "SURGED",
            "BEATS",
            "BEAT",
        )
        cool_words = (
            "LOWER THAN EXPECTED",
            "BELOW EXPECTED",
            "LESS THAN EXPECTED",
            "COOLS",
            "COOLED",
            "SOFTER",
            "WEAKER THAN EXPECTED",
            "MISSES",
            "MISSED",
            "SLOWS",
            "SLOWED",
        )
        if cls._contains_any(upper_headline, hot_words):
            return "hot"
        if cls._contains_any(upper_headline, cool_words):
            return "cool"
        return "neutral"

    @classmethod
    def _infer_growth_direction(cls, headline: str) -> str:
        upper_headline = headline.upper()
        level = cls._extract_index_level(headline)
        if cls._contains_any(upper_headline, ("PMI", "ISM")) and level is not None:
            if level >= 50.0:
                return "strong"
            if level < 50.0:
                return "weak"

        strong_words = (
            "HIGHER THAN EXPECTED",
            "ABOVE EXPECTED",
            "STRONGER THAN EXPECTED",
            "BEATS",
            "BEAT",
            "RISES",
            "ROSE",
            "JUMPS",
            "REBOUNDS",
            "EXPANDS",
            "EXPANSION",
        )
        weak_words = (
            "LOWER THAN EXPECTED",
            "BELOW EXPECTED",
            "WEAKER THAN EXPECTED",
            "MISSES",
            "MISSED",
            "FALLS",
            "FELL",
            "DROPS",
            "DECLINES",
            "CONTRACTS",
            "CONTRACTION",
        )
        if cls._contains_any(upper_headline, strong_words):
            return "strong"
        if cls._contains_any(upper_headline, weak_words):
            return "weak"
        return "neutral"

    @classmethod
    def _infer_growth_like_direction(cls, upper_headline: str) -> str:
        strong_words = (
            "HIGHER THAN EXPECTED",
            "ABOVE EXPECTED",
            "STRONGER THAN EXPECTED",
            "BEATS",
            "BEAT",
            "JUMPS",
            "SURGES",
            "RISES",
            "ROSE",
        )
        weak_words = (
            "LOWER THAN EXPECTED",
            "BELOW EXPECTED",
            "WEAKER THAN EXPECTED",
            "MISSES",
            "MISSED",
            "FALLS",
            "FELL",
            "DROPS",
            "SLOWS",
            "SOFTENS",
        )
        if cls._contains_any(upper_headline, strong_words):
            return "strong"
        if cls._contains_any(upper_headline, weak_words):
            return "weak"
        return "neutral"

    @classmethod
    def _infer_unemployment_direction(cls, upper_headline: str) -> str:
        weak_words = (
            "HIGHER THAN EXPECTED",
            "ABOVE EXPECTED",
            "RISES",
            "ROSE",
            "RISING",
            "JUMPS",
            "JUMPED",
            "INCREASES",
            "INCREASED",
        )
        strong_words = (
            "LOWER THAN EXPECTED",
            "BELOW EXPECTED",
            "FALLS",
            "FELL",
            "FALLING",
            "DROPS",
            "DROPPED",
            "DECLINES",
            "DECLINED",
        )
        if cls._contains_any(upper_headline, weak_words):
            return "weak"
        if cls._contains_any(upper_headline, strong_words):
            return "strong"
        return "neutral"

    @classmethod
    def _infer_rates_direction(cls, upper_headline: str) -> str:
        hawkish_words = (
            "RATE HIKE",
            "HIKES",
            "HIKED",
            "RAISES RATES",
            "RAISED RATES",
            "YIELD RISES",
            "YIELDS RISE",
            "YIELD HIGHER",
            "YIELDS HIGHER",
            "UP ",
        )
        dovish_words = (
            "RATE CUT",
            "CUTS RATES",
            "CUT RATES",
            "YIELD FALLS",
            "YIELDS FALL",
            "YIELD LOWER",
            "YIELDS LOWER",
            "DOWN ",
        )
        if cls._contains_any(upper_headline, hawkish_words):
            return "hawkish"
        if cls._contains_any(upper_headline, dovish_words):
            return "dovish"
        return "neutral"

    @classmethod
    def _infer_oil_direction(cls, upper_headline: str) -> str:
        bullish_words = (
            "CUT",
            "REDUCE",
            "REDUCES",
            "REDUCED",
            "OUTAGE",
            "DISRUPTION",
            "DISRUPTED",
            "ATTACK",
            "DAMAGED",
            "DAMAGE",
            "TIGHTEN",
            "TIGHTER",
        )
        bearish_words = (
            "INCREASE",
            "INCREASES",
            "INCREASED",
            "BOOST",
            "BOOSTS",
            "BOOSTED",
            "RAISES OUTPUT",
            "HIGHER OUTPUT",
            "ADD SUPPLY",
            "RELEASE",
        )
        neutral_words = ("MAINTAIN", "UNCHANGED", "CURRENT OUTPUT", "STEADY")
        if cls._contains_any(upper_headline, bullish_words):
            return "bullish"
        if cls._contains_any(upper_headline, bearish_words):
            return "bearish"
        if cls._contains_any(upper_headline, neutral_words):
            return "neutral"
        return "neutral"

    @staticmethod
    def _extract_index_level(headline: str) -> float | None:
        matches = re.findall(r"\b([1-9][0-9](?:\.[0-9]+)?)\b", headline)
        for match in matches:
            value = float(match)
            if value <= 70:
                return value
        return None

    @staticmethod
    def _describe_diffusion_index(level: float | None) -> str | None:
        if level is None:
            return None
        if level >= 50:
            return "경기 확장 구간"
        if level >= 48:
            return "위축이 심하지 않은 둔화 구간"
        if level >= 45:
            return "부진이 비교적 뚜렷한 위축 구간"
        return "경기 둔화 압력이 강한 위축 구간"
