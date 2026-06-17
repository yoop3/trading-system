"""
master_agent.py — ตัดสินใจแยก BTC และ XAU จาก signals ของ agents
BTC: 7 agents → weighted score → เทรด BTC/USDT:USDT
XAU: 6 agents → weighted score → เทรด XAU/USDT:USDT

เงื่อนไขเข้า trade BTC: score > +6 หรือ < -6 หรือ LLM (grey zone)
เงื่อนไขเข้า trade XAU: score > +5 หรือ < -5 (6 agents, total weight 12.5)
"""

import os
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal


# ========== BTC Weights (7 agents) ==========
BTC_WEIGHTS: dict[str, float] = {
    "technical_btc": 2.0,
    "whale_btc":     2.0,
    "smc_btc":       3.0,
    "macro_btc":     2.5,
    "wyckoff_btc":   2.0,
    "sentiment":     1.5,
    "news":          1.0,
}
BTC_TOTAL_WEIGHT = sum(BTC_WEIGHTS.values())  # 14.0

BTC_LONG_THRESHOLD  = +6.0
BTC_SHORT_THRESHOLD = -6.0
BTC_MIN_WEIGHT_RATIO = 0.35  # 35% of 14.0 = 4.9 weight ขั้นต่ำ

# ========== XAU Weights (6 agents) ==========
XAU_WEIGHTS: dict[str, float] = {
    "wyckoff_xau":   2.0,
    "macro_xau":     3.0,
    "smc_xau":       3.0,
    "technical_xau": 2.0,
    "news":          2.0,
    "sentiment":     0.5,
}
XAU_TOTAL_WEIGHT = sum(XAU_WEIGHTS.values())  # 12.5

XAU_LONG_THRESHOLD  = +5.0
XAU_SHORT_THRESHOLD = -5.0
XAU_MIN_WEIGHT_RATIO = 0.40  # 40% of 12.5 = 5.0 weight ขั้นต่ำ


@dataclass
class MasterDecision:
    """ผลการตัดสินใจต่อ 1 asset"""
    signal: str
    total_score: float
    reasoning: str
    used_llm: bool
    timestamp: str
    asset: str = "BTC"
    consensus_long: int = 0
    consensus_short: int = 0
    weight_ratio: float = 0.0


class MasterAgent(BaseAgent):
    """
    ตัดสินใจแยกสำหรับ BTC และ XAU:
    - decide_btc(signals): ใช้ BTC_WEIGHTS (7 agents), threshold ±6
    - decide_xau(signals): ใช้ XAU_WEIGHTS (2 agents), threshold ±3
    Grey zone → LLM (BTC only, XAU ใช้ HOLD ถ้าไม่ถึง threshold)
    """

    def __init__(self, data_fetcher, db, risk_agent=None):
        super().__init__("master", data_fetcher, db)
        self.risk_agent = risk_agent
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._last_btc_decision: MasterDecision | None = None
        self._last_xau_decision: MasterDecision | None = None

    async def analyze(self) -> AgentSignal:
        """implement abstract method — ใช้ decide_btc()/decide_xau() โดยตรง"""
        return AgentSignal(
            agent_name=self.name,
            signal="HOLD",
            score=0.0,
            confidence=0.0,
            reason="ใช้ decide_btc()/decide_xau() แทน",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def decide_btc(self, signals: dict[str, AgentSignal]) -> MasterDecision:
        """ตัดสินใจ BTC จาก 7 agents"""
        return await self._decide(
            signals=signals,
            weights=BTC_WEIGHTS,
            total_weight=BTC_TOTAL_WEIGHT,
            long_threshold=BTC_LONG_THRESHOLD,
            short_threshold=BTC_SHORT_THRESHOLD,
            min_weight_ratio=BTC_MIN_WEIGHT_RATIO,
            asset="BTC",
            use_llm=True,
        )

    async def decide_xau(self, signals: dict[str, AgentSignal]) -> MasterDecision:
        """ตัดสินใจ XAU จาก 6 agents — ไม่ใช้ LLM (ใช้ HOLD ถ้า grey zone)"""
        return await self._decide(
            signals=signals,
            weights=XAU_WEIGHTS,
            total_weight=XAU_TOTAL_WEIGHT,
            long_threshold=XAU_LONG_THRESHOLD,
            short_threshold=XAU_SHORT_THRESHOLD,
            min_weight_ratio=XAU_MIN_WEIGHT_RATIO,
            asset="XAU",
            use_llm=False,
        )

    async def _decide(
        self,
        signals: dict[str, AgentSignal],
        weights: dict[str, float],
        total_weight: float,
        long_threshold: float,
        short_threshold: float,
        min_weight_ratio: float,
        asset: str,
        use_llm: bool,
    ) -> MasterDecision:
        """Core decision logic สำหรับทั้ง BTC และ XAU"""
        # Step 1 — Weighted score
        total_score = 0.0
        score_breakdown = []
        for name, weight in weights.items():
            if name in signals:
                sig = signals[name]
                weighted = sig.score * weight
                total_score += weighted
                score_breakdown.append(f"{name}: {sig.score:+.1f}×{weight}={weighted:+.1f}")

        logger.info(f"[master_{asset.lower()}] Scores: {' | '.join(score_breakdown)}")
        logger.info(f"[master_{asset.lower()}] Total: {total_score:+.2f} (threshold ±{long_threshold})")

        # Step 2 — Count consensus
        long_count = sum(1 for n in weights if n in signals and signals[n].signal == "LONG")
        short_count = sum(1 for n in weights if n in signals and signals[n].signal == "SHORT")

        # Check dominant side weight
        long_weight  = sum(weights[n] for n in weights if n in signals and signals[n].signal == "LONG")
        short_weight = sum(weights[n] for n in weights if n in signals and signals[n].signal == "SHORT")
        long_ratio   = long_weight  / total_weight
        short_ratio  = short_weight / total_weight

        logger.info(
            f"[master_{asset.lower()}] Consensus: LONG={long_count}({long_ratio:.0%}) "
            f"SHORT={short_count}({short_ratio:.0%})"
        )

        # Step 3 — Decide
        used_llm = False
        weight_ratio = 0.0

        if total_score > long_threshold:
            if long_ratio >= min_weight_ratio:
                final_signal = "LONG"
                weight_ratio = long_ratio
                reasoning = f"{asset} Score {total_score:+.1f} + LONG weight {long_ratio:.0%}"
            else:
                final_signal = "HOLD"
                reasoning = f"{asset} Score {total_score:+.1f} ชัด แต่ LONG weight ต่ำ ({long_ratio:.0%})"
        elif total_score < short_threshold:
            if short_ratio >= min_weight_ratio:
                final_signal = "SHORT"
                weight_ratio = short_ratio
                reasoning = f"{asset} Score {total_score:+.1f} + SHORT weight {short_ratio:.0%}"
            else:
                final_signal = "HOLD"
                reasoning = f"{asset} Score {total_score:+.1f} ชัด แต่ SHORT weight ต่ำ ({short_ratio:.0%})"
        elif use_llm:
            # Grey zone → LLM
            llm_result = await self._ask_llm(signals, total_score, long_count, short_count, asset, weights)
            llm_signal = llm_result.get("signal", "HOLD")
            used_llm = True
            if llm_signal == "LONG" and long_ratio >= min_weight_ratio:
                final_signal = "LONG"
                weight_ratio = long_ratio
                reasoning = llm_result.get("reasoning", "") + f" [LLM+{asset} LONG {long_ratio:.0%}]"
            elif llm_signal == "SHORT" and short_ratio >= min_weight_ratio:
                final_signal = "SHORT"
                weight_ratio = short_ratio
                reasoning = llm_result.get("reasoning", "") + f" [LLM+{asset} SHORT {short_ratio:.0%}]"
            else:
                final_signal = "HOLD"
                reasoning = llm_result.get("reasoning", f"{asset} grey zone score={total_score:+.1f}") + " → HOLD"
        else:
            final_signal = "HOLD"
            reasoning = f"{asset} grey zone score={total_score:+.1f} (±{long_threshold} required)"

        decision = MasterDecision(
            signal=final_signal,
            total_score=total_score,
            reasoning=reasoning,
            used_llm=used_llm,
            weight_ratio=weight_ratio,
            timestamp=datetime.now(timezone.utc).isoformat(),
            asset=asset,
            consensus_long=long_count,
            consensus_short=short_count,
        )

        if asset == "BTC":
            self._last_btc_decision = decision
        else:
            self._last_xau_decision = decision

        await self.db.save_master_decision(
            timestamp=decision.timestamp,
            final_signal=f"{asset}:{decision.signal}",
            total_score=decision.total_score,
            llm_reasoning=decision.reasoning,
            was_executed=0,
        )

        logger.info(
            f"[master_{asset.lower()}] Decision: {final_signal} | "
            f"{'LLM' if used_llm else 'Rule'} | {reasoning}"
        )
        return decision

    async def _ask_llm(
        self,
        signals: dict,
        total_score: float,
        long_count: int,
        short_count: int,
        asset: str,
        weights: dict,
    ) -> dict:
        """เรียก Claude Haiku วิเคราะห์ grey zone"""
        if not self.anthropic_key or self.anthropic_key == "your_anthropic_api_key_here":
            return {"signal": "HOLD", "reasoning": f"{asset} grey zone score={total_score:+.1f}"}
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)

            summary_lines = [
                f"- {name} (w={weights.get(name, '?')}): {sig.signal} score={sig.score:+.1f} conf={sig.confidence:.0%} — {sig.reason}"
                for name, sig in signals.items()
                if name in weights
            ]
            prompt = f"""คุณเป็น AI Trading Advisor วิเคราะห์สัญญาณจาก agents สำหรับ {asset}/USDT Futures:

{chr(10).join(summary_lines)}

Weighted Score รวม: {total_score:+.2f} (grey zone)
LONG agents: {long_count} | SHORT agents: {short_count}

ตอบเป็น JSON เท่านั้น:
{{"signal": "LONG"|"SHORT"|"HOLD", "reasoning": "<เหตุผลสั้นๆ ภาษาไทย ไม่เกิน 100 ตัวอักษร>"}}"""

            msg = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
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
            logger.error(f"[master] LLM error ({asset}): {e}")
            return {"signal": "HOLD", "reasoning": f"LLM error: {str(e)[:80]}"}

    @property
    def last_btc_decision(self) -> MasterDecision | None:
        return self._last_btc_decision

    @property
    def last_xau_decision(self) -> MasterDecision | None:
        return self._last_xau_decision
