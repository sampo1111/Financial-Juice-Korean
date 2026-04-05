from __future__ import annotations

from datetime import UTC
import json
import re

import httpx

from .models import NewsInsight, NewsItem


class OllamaError(RuntimeError):
    """Raised when Ollama cannot generate a response."""


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
        prompt = (
            "You are a Korean macro and markets news editor writing for investors.\n"
            "Rewrite the English headline into natural Korean financial-news style and add a short market-aware explanation.\n"
            "Rules:\n"
            "- translated_title: one sharp Korean market headline, not a literal translation.\n"
            "- summary: exactly 1 Korean sentence explaining the event in plain, natural Korean.\n"
            "- term_note: if the headline contains technical macro/market jargon or an important level, add 1 Korean sentence explaining what the term means and how to read the number; otherwise return an empty string.\n"
            "- If the headline includes PMI, CPI, PPI, PCE, NFP, payrolls, ISM, GDP, unemployment rate, jobless rate, Treasury yield, basis points, or a threshold like 50, term_note is mandatory and must not be empty.\n"
            "- For PMI or ISM levels, explicitly explain the 50 threshold and say whether the shown level signals expansion, borderline, or contraction.\n"
            "- market_view: exactly 1 Korean sentence explaining the likely market reading only when it can be inferred from the headline.\n"
            "- Do not invent facts beyond the headline.\n"
            "- Avoid stiff phrases such as '발표한 바에 따르면', '시장적 영향은 아직 확인되지 않았다', '관련 소식이다'.\n"
            "- Write like a Korean brokerage morning note or market desk update.\n"
            "- Prefer concise finance wording such as '수출 둔화', '위험선호', '원자재', '달러', '금리', '실적', '공급 우려' only when relevant.\n"
            "- market_view should usually begin with '시장에선', '투자자들은', or '직접적인 시장 영향은'.\n"
            "- Use soft analytical phrasing like '해석될 수 있다', '볼 수 있다', '제한적일 수 있다'.\n"
            "- If market relevance is unclear, say so naturally without sounding robotic.\n"
            "- Return only JSON.\n\n"
            "Style examples:\n"
            "English headline: US March CPI rises less than expected\n"
            "JSON: {\"translated_title\":\"미국 3월 CPI가 예상보다 덜 올랐다\",\"summary\":\"미국 소비자물가 상승세가 시장 예상보다 완만했다.\",\"term_note\":\"CPI는 소비자물가를 보여주는 대표 인플레이션 지표다.\",\"market_view\":\"시장에선 금리 인하 기대를 자극할 수 있는 재료로 해석할 수 있다.\"}\n\n"
            "English headline: OPEC+ says it will maintain current output policy\n"
            "JSON: {\"translated_title\":\"OPEC+, 기존 산유 정책 유지\",\"summary\":\"OPEC+가 현재의 원유 생산 방침을 그대로 유지하기로 했다.\",\"term_note\":\"\",\"market_view\":\"시장에선 원유 공급 전망에 큰 변화가 없다는 의미로 받아들일 수 있어 유가 반응은 제한적일 수 있다.\"}\n\n"
            "English headline: Eurozone flash manufacturing PMI 47.4 vs 46.8 prior\n"
            "JSON: {\"translated_title\":\"유로존 제조업 PMI 47.4로 반등했지만 여전히 위축 구간\",\"summary\":\"유로존 제조업 PMI가 전월보다 올랐지만 기준선 50에는 못 미쳤다.\",\"term_note\":\"PMI는 기업 구매담당자 경기지수로, 50을 넘으면 경기 확장, 50을 밑돌면 위축으로 읽는다.\",\"market_view\":\"시장에선 제조업 부진이 다소 완화됐지만 경기 회복 신호로 보기엔 아직 이르다고 해석할 수 있다.\"}\n\n"
            f"Additional domain guidance:\n{self._build_domain_guidance(item.title)}\n\n"
            f"Headline: {item.title}\n"
            f"Published at: {published_utc}\n"
            f"Source URL: {item.link}\n"
        )

        schema = {
            "type": "object",
            "properties": {
                "translated_title": {"type": "string"},
                "summary": {"type": "string"},
                "term_note": {"type": "string"},
                "market_view": {"type": "string"},
            },
            "required": ["translated_title", "summary", "term_note", "market_view"],
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You turn real-time English market headlines into natural Korean financial-wire updates. "
                        "Sound like a Korean market analyst, not a general translator."
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
            content = data["message"]["content"]
            parsed = json.loads(content)
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

        translated_title = str(parsed.get("translated_title", "")).strip()
        summary = self._normalize_sentence(str(parsed.get("summary", "")).strip())
        term_note = self._normalize_sentence(str(parsed.get("term_note", "")).strip(), allow_empty=True)
        if not term_note:
            term_note = self._build_fallback_term_note(item.title)
        market_view = self._normalize_sentence(str(parsed.get("market_view", "")).strip())
        explanation_parts = [summary]
        if term_note:
            explanation_parts.append(term_note)
        explanation_parts.append(market_view)
        explanation = " ".join(explanation_parts).strip()
        if not translated_title or not summary or not market_view:
            raise OllamaError("Ollama returned empty translation or explanation.")

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

    @staticmethod
    def _normalize_sentence(text: str, allow_empty: bool = False) -> str:
        cleaned = " ".join(text.split())
        cleaned = cleaned.replace("발표한 바에 따르면", "")
        cleaned = cleaned.replace("관련 소식이다.", "")
        cleaned = cleaned.strip()
        if not cleaned and allow_empty:
            return ""
        if cleaned and cleaned[-1] not in ".!?":
            cleaned += "."
        return cleaned

    @staticmethod
    def _build_domain_guidance(headline: str) -> str:
        notes: list[str] = []
        upper_headline = headline.upper()

        if "PMI" in upper_headline:
            notes.append(
                "- PMI is a purchasing managers index and a leading activity indicator. "
                "In Korean explain briefly that above 50 implies expansion and below 50 implies contraction."
            )
            if re.search(r"\b([0-9]{2}(?:\.[0-9])?)\b", headline):
                notes.append(
                    "- If a PMI level is shown, interpret it by zone: around 50 is borderline, 48-49 is mild contraction, 45-47 is meaningful weakness, below 45 is severe weakness."
                )

        if "CPI" in upper_headline:
            notes.append(
                "- CPI is a consumer inflation indicator. Explain that higher-than-expected CPI can support a hawkish rates view, while lower-than-expected CPI can ease rate pressure."
            )

        if "PPI" in upper_headline:
            notes.append(
                "- PPI is a producer price indicator and can hint at pipeline inflation pressure."
            )

        if "PCE" in upper_headline:
            notes.append(
                "- PCE is the inflation gauge closely watched by the Fed. Mention that it matters for rate expectations when relevant."
            )

        if "NFP" in upper_headline or "NONFARM" in upper_headline:
            notes.append(
                "- NFP refers to US nonfarm payrolls, a core labor-market indicator tied to rate expectations and dollar/yield moves."
            )

        if "ISM" in upper_headline:
            notes.append(
                "- ISM is a US business survey index. If a level is shown, mention the 50 expansion/contraction threshold."
            )

        if "GDP" in upper_headline:
            notes.append(
                "- GDP is the broad growth indicator. Explain whether the headline signals stronger or weaker growth momentum."
            )

        if "UNEMPLOYMENT RATE" in upper_headline or "JOBLESS RATE" in upper_headline:
            notes.append(
                "- If unemployment is mentioned, explain that a higher rate usually implies labor-market cooling while a lower rate implies tightness."
            )

        if not notes:
            notes.append("- No special glossary note is required unless the headline clearly contains market jargon.")

        return "\n".join(notes)

    @classmethod
    def _build_fallback_term_note(cls, headline: str) -> str:
        upper_headline = headline.upper()
        level = cls._extract_index_level(headline)

        if "PMI" in upper_headline:
            zone = cls._describe_diffusion_index(level)
            if zone:
                return cls._normalize_sentence(
                    f"PMI는 기업 구매담당자 경기지수로, 50을 넘으면 경기 확장인데 {level:.1f}는 {zone}으로 읽는다."
                )
            return cls._normalize_sentence(
                "PMI는 기업 구매담당자 경기지수로, 50을 넘으면 경기 확장, 50을 밑돌면 위축으로 읽는다."
            )

        if "ISM" in upper_headline:
            zone = cls._describe_diffusion_index(level)
            if zone:
                return cls._normalize_sentence(
                    f"ISM은 미국 공급관리협회 경기지수로, 50이 기준선인데 {level:.1f}는 {zone}으로 해석한다."
                )
            return cls._normalize_sentence(
                "ISM은 미국 공급관리협회 경기지수로, 50을 넘으면 확장, 50을 밑돌면 위축으로 읽는다."
            )

        if "CPI" in upper_headline:
            return cls._normalize_sentence(
                "CPI는 소비자물가를 보여주는 대표 인플레이션 지표로, 예상보다 높으면 긴축 우려를 키우고 낮으면 금리 부담을 덜 수 있다."
            )

        if "PPI" in upper_headline:
            return cls._normalize_sentence(
                "PPI는 생산자물가 지표로, 예상보다 높으면 기업의 원가 부담과 향후 물가 압력을 시사할 수 있다."
            )

        if "PCE" in upper_headline:
            return cls._normalize_sentence(
                "PCE는 연준이 중요하게 보는 물가 지표로, 예상보다 높으면 금리 인하 기대를 늦출 수 있다."
            )

        if "NFP" in upper_headline or "NONFARM" in upper_headline or "PAYROLLS" in upper_headline:
            return cls._normalize_sentence(
                "NFP는 미국 비농업부문 고용지표로, 고용이 강하면 금리 부담과 달러 강세 재료로 읽히기 쉽다."
            )

        if "GDP" in upper_headline:
            return cls._normalize_sentence(
                "GDP는 경제 성장률을 보여주는 대표 지표로, 예상보다 강하면 경기 확장 기대를 높이고 약하면 성장 둔화 우려를 키울 수 있다."
            )

        if "UNEMPLOYMENT RATE" in upper_headline or "JOBLESS RATE" in upper_headline:
            return cls._normalize_sentence(
                "실업률은 고용시장의 체온을 보여주는 지표로, 오르면 경기 둔화 신호로, 낮아지면 고용이 여전히 타이트하다는 뜻으로 읽힌다."
            )

        if "TREASURY YIELD" in upper_headline or " YIELD" in upper_headline:
            return cls._normalize_sentence(
                "국채금리는 채권 수익률을 뜻하며, 오르면 긴축과 할인율 부담, 내리면 금리 완화 기대로 해석되는 경우가 많다."
            )

        if "BASIS POINT" in upper_headline or "BPS" in upper_headline or "BP " in upper_headline:
            return cls._normalize_sentence(
                "1bp는 0.01%포인트를 뜻해, 25bp 인상은 기준금리나 금리가 0.25%포인트 오르는 의미다."
            )

        return ""

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
