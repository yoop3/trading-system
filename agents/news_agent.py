"""
news_agent.py — วิเคราะห์ข่าว BTC/crypto จาก RSS feeds ด้วย Claude Haiku
อัปเดตทุก 30 นาที | ดึง 3 แหล่ง: CoinTelegraph, CoinDesk, Reddit r/Bitcoin
ใช้ httpx + xml.etree.ElementTree (built-in ไม่ต้องติดตั้งเพิ่ม)
"""

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal


RSS_SOURCES = [
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Reddit BTC",    "https://reddit.com/r/Bitcoin/.rss"),
]

# namespace ที่ Reddit Atom feed ใช้
ATOM_NS = "{http://www.w3.org/2005/Atom}"


class NewsAgent(BaseAgent):
    """
    ดึง headline + description จาก 3 RSS sources รวม 10 ชิ้นล่าสุด
    ส่งให้ Claude Haiku วิเคราะห์ sentiment → score -3 ถึง +3
    """

    def __init__(self, data_fetcher, db):
        super().__init__("news", data_fetcher, db)
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    async def _fetch_rss(self, name: str, url: str) -> list[str]:
        """
        ดึง RSS feed และแปลงเป็น list ของ "headline: description"
        รองรับทั้ง RSS 2.0 (CoinTelegraph, CoinDesk) และ Atom (Reddit)
        """
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)"}
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()

            root = ET.fromstring(resp.text)
            items: list[str] = []

            # RSS 2.0 format: <channel><item><title>...</title><description>...</description>
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                desc  = (item.findtext("description") or "").strip()
                # ตัด HTML tags อย่างง่าย
                desc = desc[:150].split("<")[0].strip()
                if title:
                    items.append(f"{title}: {desc}" if desc else title)

            # Atom format (Reddit): <entry><title>...</title><content>...</content>
            if not items:
                for entry in root.iter(f"{ATOM_NS}entry"):
                    title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
                    if title and title != "":
                        items.append(title)

            logger.debug(f"NewsAgent [{name}]: ดึงได้ {len(items)} items")
            return items[:5]  # เอาแค่ 5 ชิ้นต่อแหล่ง

        except Exception as e:
            logger.warning(f"NewsAgent [{name}] fetch error: {e}")
            return []

    async def _fetch_all_news(self) -> list[str]:
        """
        ดึงจาก 3 แหล่งพร้อมกัน รวมได้สูงสุด 15 headlines
        แล้วตัดเหลือ 10 ชิ้นล่าสุด
        """
        import asyncio
        tasks = [self._fetch_rss(name, url) for name, url in RSS_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        combined: list[str] = []
        for i, result in enumerate(results):
            if isinstance(result, list):
                combined.extend(result)
            else:
                logger.warning(f"NewsAgent source {RSS_SOURCES[i][0]} failed: {result}")

        # เอาแค่ 10 ชิ้นแรก
        return combined[:10]

    async def _analyze_with_llm(self, headlines: list[str]) -> dict:
        """
        ส่ง headlines ให้ Claude Haiku วิเคราะห์ sentiment ต่อ ETH
        คืน {'score': float, 'reason': str, 'confidence': float}
        """
        if not self.anthropic_key or self.anthropic_key == "your_anthropic_api_key_here":
            logger.info("NewsAgent: ไม่มี Anthropic key — ใช้ score 0")
            return {"score": 0, "reason": "ไม่มี API key", "confidence": 0.0}

        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)

            headlines_text = "\n".join(f"- {h}" for h in headlines)
            prompt = f"""วิเคราะห์ข่าว crypto ต่อไปนี้ว่า sentiment ต่อ BTC/ตลาด crypto โดยรวม เป็นบวกหรือลบ:

{headlines_text}

ตอบเป็น JSON เท่านั้น ไม่ต้องอธิบายเพิ่ม:
{{"score": <-3 ถึง +3>, "reason": "<สรุปสั้นๆ ภาษาไทย ไม่เกิน 80 ตัวอักษร>", "confidence": <0.0 ถึง 1.0>}}"""

            msg = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            start = text.find("{")
            end   = text.rfind("}") + 1
            result = json.loads(text[start:end])
            result["score"]      = max(-3.0, min(3.0, float(result.get("score", 0))))
            result["confidence"] = max(0.0,  min(1.0, float(result.get("confidence", 0.5))))
            return result

        except Exception as e:
            logger.error(f"NewsAgent LLM error: {e}")
            return {"score": 0, "reason": f"LLM error: {str(e)[:50]}", "confidence": 0.0}

    async def analyze(self) -> AgentSignal:
        price = 0.0
        try:
            price = await self.data_fetcher.get_current_price()
        except Exception:
            pass

        headlines = await self._fetch_all_news()

        if not headlines:
            return AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                score=0.0,
                confidence=0.0,
                reason="ดึง RSS ไม่ได้ทุกแหล่ง (network error)",
                timestamp=datetime.now(timezone.utc).isoformat(),
                next_action="retry ใน 30 นาที",
                price=price,
            )

        llm_result  = await self._analyze_with_llm(headlines)
        score       = float(llm_result.get("score", 0))
        confidence  = float(llm_result.get("confidence", 0.5))
        reason      = llm_result.get("reason", "วิเคราะห์จาก RSS")

        if score > 1:
            signal = "LONG"
        elif score < -1:
            signal = "SHORT"
        else:
            signal = "HOLD"

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=f"[{len(headlines)} headlines] {reason}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action="อัปเดตอีกครั้งใน 30 นาที",
            price=price,
        )
