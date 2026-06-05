"""
test_agents.py — ทดสอบ import และ structure ของ agents
ไม่ต้อง API key จริง เทสแค่ syntax และ interface
"""

import unittest
from unittest.mock import AsyncMock, MagicMock
from agents.base_agent import AgentSignal


class TestAgentSignal(unittest.TestCase):
    def test_signal_creation(self):
        sig = AgentSignal(
            agent_name="test",
            signal="LONG",
            score=5.0,
            confidence=0.8,
            reason="test reason",
            timestamp="2024-01-01T00:00:00",
        )
        self.assertEqual(sig.agent_name, "test")
        self.assertEqual(sig.signal, "LONG")
        self.assertEqual(sig.score, 5.0)
        self.assertEqual(sig.confidence, 0.8)
        self.assertFalse(sig.veto)

    def test_signal_defaults(self):
        sig = AgentSignal(
            agent_name="risk",
            signal="VETO",
            score=0.0,
            confidence=1.0,
            reason="daily loss limit",
            timestamp="2024-01-01T00:00:00",
            veto=True,
        )
        self.assertTrue(sig.veto)

    def test_all_agents_importable(self):
        from agents.technical_agent import TechnicalAgent
        from agents.macro_agent import MacroAgent
        from agents.sentiment_agent import SentimentAgent
        from agents.news_agent import NewsAgent
        from agents.whale_agent import WhaleAgent
        from agents.risk_agent import RiskAgent
        from agents.master_agent import MasterAgent
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
