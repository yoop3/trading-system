"""
master_agent.py — ตัดสินใจขั้นสุดท้ายจาก signals ของทุก agent
เงื่อนไขเข้า trade: weighted score ชัดเจน AND ≥3/5 agents เห็นตรงกัน
"""

import os
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal


WEIGHTS: dict[str, float] = {
    "macro":     3.0,
    "technical": 2.0,
    "whale":     2.0,
    "sentiment": 1.5,
    "news":      1.0,
}

LONG_THRESHOLD  = +5.0
SHORT_THRESHOLD = -5.0

TOTAL_WEIGHT = sum(WEIGHTS.values())  # 9.5

# ฝ่ายชนะต้องถือ weight รวม > 40% ของทั้งหมด
# ป้องกันกรณีที่ 1 agent heavyweight ลากคนเดียว แต่ไม่เข้มจนเกินไป
MIN_WEIGHT_RATIO = 0.40


@dataclass
class MasterDecision:
    """ผลการตัดสินใจของ Master Agent"""
    signal: str
    total_score: float
    reasoning: str
    used_llm: bool
    timestamp: str
    consensus_long: int = 0   # จำนวน agent ที่ LONG
    consensus_short: int = 0  # จำนวน agent ที่ SHORT
    weight_ratio: float = 0.0  # weight ratio ของฝั่งที่ชนะ (ใช้ grading)


class MasterAgent(BaseAgent):
    """
    เงื่อนไขเข้า trade ต้องผ่านพร้อมกัน 2 ข้อ:
      1. Weighted score > +5 (LONG) หรือ < -5 (SHORT)
      2. Agent ≥ 3/5 ตัว signal ทิศทางเดียวกัน (consensus)
    ถ้า score ใน grey zone → เรียก Claude ช่วยตัดสิน (แต่ยังต้องผ่าน consensus)
    """

    def __init__(self, data_fetcher, db, risk_agent=None):
        super().__init__("master", data_fetcher, db)
        self.risk_agent = risk_agent
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._last_decision: MasterDecision | None = None

    async def analyze(self) -> AgentSignal:
        """implement abstract method — ใช้ decide() โดยตรง"""
        return AgentSignal(
            agent_name=self.name,
            signal="HOLD",
            score=0.0,
            confidence=0.0,
            reason="ใช้ decide() แทน",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _count_consensus(self, signals: dict[str, AgentSignal]) -> tuple[int, int]:
        """นับ agent count (ใช้แสดง log เท่านั้น)"""
        long_count = sum(
            1 for name in WEIGHTS if name in signals and signals[name].signal == "LONG"
        )
        short_count = sum(
            1 for name in WEIGHTS if name in signals and signals[name].signal == "SHORT"
        )
        return long_count, short_count

    def _check_weighted_consensus(
        self, signals: dict[str, AgentSignal], direction: str
    ) -> tuple[bool, float]:
        """
        ตรวจว่าฝ่าย direction ถือ weight รวม > MIN_WEIGHT_RATIO ของทั้งหมด
        คืน (passed, ratio)

        ตัวอย่าง: macro(3.0)+whale(2.0) SHORT → 5.0/9.5 = 52.6% > 40% → pass
        """
        side_weight = sum(
            WEIGHTS[name]
            for name in WEIGHTS
            if name in signals and signals[name].signal == direction
        )
        ratio = side_weight / TOTAL_WEIGHT
        return ratio >= MIN_WEIGHT_RATIO, ratio

    async def decide(self, signals: dict[str, AgentSignal]) -> MasterDecision:
        """
        Logic:
          1. คำนวณ weighted score
          2. นับ consensus (กี่ agent LONG / SHORT)
          3. ถ้า score ชัด + consensus ≥ 3 → trade
          4. ถ้า score ชัด แต่ consensus < 3 → HOLD (รอ agent เห็นตรงกันมากกว่านี้)
          5. ถ้า grey zone → เรียก LLM (แต่ LLM ก็ต้องผ่าน consensus ด้วย)
        """
        # Step 1 — weighted score
        total_score = 0.0
        score_breakdown = []
        for name, weight in WEIGHTS.items():
            if name in signals:
                sig = signals[name]
                weighted = sig.score * weight
                total_score += weighted
                score_breakdown.append(
                    f"{name}: {sig.score:+.1f} × {weight} = {weighted:+.1f}"
                )

        logger.info(f"[master] Weighted scores: {' | '.join(score_breakdown)}")
        logger.info(f"[master] Total score: {total_score:+.2f}")

        # Step 2 — weighted consensus
        long_count, short_count = self._count_consensus(signals)
        long_ok,  long_ratio  = self._check_weighted_consensus(signals, "LONG")
        short_ok, short_ratio = self._check_weighted_consensus(signals, "SHORT")
        logger.info(
            f"[master] Consensus: LONG={long_count}({long_ratio:.0%}) "
            f"SHORT={short_count}({short_ratio:.0%}) "
            f"(ต้องการ ≥{MIN_WEIGHT_RATIO:.0%} of weight)"
        )

        # Step 3-5 — ตัดสินใจ
        used_llm = False
        weight_ratio = 0.0  # weight ratio ของฝั่งที่ชนะ (ใช้ grading)

        if total_score > LONG_THRESHOLD:
            if long_ok:
                final_signal = "LONG"
                weight_ratio = long_ratio
                reasoning = (
                    f"Score {total_score:+.1f} + LONG weight {long_ratio:.0%} ≥ {MIN_WEIGHT_RATIO:.0%}"
                )
            else:
                final_signal = "HOLD"
                reasoning = (
                    f"Score {total_score:+.1f} ชัด แต่ LONG weight ต่ำ "
                    f"({long_ratio:.0%} < {MIN_WEIGHT_RATIO:.0%})"
                )

        elif total_score < SHORT_THRESHOLD:
            if short_ok:
                final_signal = "SHORT"
                weight_ratio = short_ratio
                reasoning = (
                    f"Score {total_score:+.1f} + SHORT weight {short_ratio:.0%} ≥ {MIN_WEIGHT_RATIO:.0%}"
                )
            else:
                final_signal = "HOLD"
                reasoning = (
                    f"Score {total_score:+.1f} ชัด แต่ SHORT weight ต่ำ "
                    f"({short_ratio:.0%} < {MIN_WEIGHT_RATIO:.0%})"
                )

        else:
            # Grey zone → เรียก LLM
            llm_result = await self._ask_llm(signals, total_score, long_count, short_count)
            llm_signal = llm_result.get("signal", "HOLD")
            used_llm = True

            if llm_signal == "LONG" and long_ok:
                final_signal = "LONG"
                weight_ratio = long_ratio
                reasoning = llm_result.get("reasoning", "") + f" [LLM+LONG {long_ratio:.0%}]"
            elif llm_signal == "SHORT" and short_ok:
                final_signal = "SHORT"
                weight_ratio = short_ratio
                reasoning = llm_result.get("reasoning", "") + f" [LLM+SHORT {short_ratio:.0%}]"
            else:
                final_signal = "HOLD"
                reasoning = llm_result.get("reasoning", "") + (
                    f" [LLM={llm_signal} weight ไม่พอ]" if llm_signal != "HOLD" else " [LLM: HOLD]"
                )

        decision = MasterDecision(
            signal=final_signal,
            total_score=total_score,
            reasoning=reasoning,
            used_llm=used_llm,
            weight_ratio=weight_ratio,
            timestamp=datetime.now(timezone.utc).isoformat(),
            consensus_long=long_count,
            consensus_short=short_count,
        )
        self._last_decision = decision

        await self.db.save_master_decision(
            timestamp=decision.timestamp,
            final_signal=decision.signal,
            total_score=decision.total_score,
            llm_reasoning=decision.reasoning,
            was_executed=0,
        )

        logger.info(
            f"[master] Decision: {final_signal} | "
            f"{'LLM' if used_llm else 'Rule'} | {reasoning}"
        )
        return decision

    async def _ask_llm(
        self,
        signals: dict,
        total_score: float,
        long_count: int,
        short_count: int,
    ) -> dict:
        """เรียก Claude Sonnet วิเคราะห์ grey zone — คืน {'signal': str, 'reasoning': str}"""
        if not self.anthropic_key or self.anthropic_key == "your_anthropic_api_key_here":
            logger.info("[master] ไม่มี Anthropic key — HOLD")
            return {
                "signal": "HOLD",
                "reasoning": f"Grey zone score={total_score:+.1f}",
            }

        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)

            summary_lines = [
                f"- {name}: {sig.signal} (score={sig.score:+.1f}, conf={sig.confidence:.0%}) — {sig.reason}"
                for name, sig in signals.items()
            ]
            signals_text = "\n".join(summary_lines)

            prompt = f"""คุณเป็น AI Trading Advisor วิเคราะห์สัญญาณจาก 5 agents สำหรับ ETH/USDT Futures:

{signals_text}

Weighted Score รวม: {total_score:+.2f} (grey zone)
Agent ที่ LONG: {long_count}/5 | Agent ที่ SHORT: {short_count}/5

ระบบจะเข้า trade ได้เฉพาะเมื่อ ≥3/5 agents เห็นตรงกัน
ตอบเป็น JSON เท่านั้น:
{{"signal": "LONG"|"SHORT"|"HOLD", "reasoning": "<เหตุผลสั้นๆ ภาษาไทย ไม่เกิน 100 ตัวอักษร>"}}"""

            msg = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            result = json.loads(text[start:end])
            if result.get("signal") not in ("LONG", "SHORT", "HOLD"):
                result["signal"] = "HOLD"
            return result

        except Exception as e:
            logger.error(f"[master] LLM error: {e}")
            return {"signal": "HOLD", "reasoning": f"LLM error: {str(e)[:80]}"}

    @property
    def last_decision(self) -> MasterDecision | None:
        return self._last_decision
