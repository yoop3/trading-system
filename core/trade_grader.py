"""
trade_grader.py — ให้เกรด A/B/C/D กับแต่ละ trade ก่อนเปิดจริง
เกณฑ์เต็มอยู่ใน docs/TRADE_GRADING.md — คำนวณจากข้อมูลที่ agent วิเคราะห์ไว้แล้ว ไม่เรียก LLM เพิ่ม
"""

from loguru import logger

# ATR/price ต่ำกว่านี้ถือว่าตลาดนิ่งเกินไป → ลดเกรดลง 1 ขั้น
LOW_VOLATILITY_THRESHOLD = 0.005

GRADES = ["A", "B", "C", "D"]


def _score_consensus(weight_ratio: float) -> int:
    """ให้คะแนน consensus weight ratio ของฝั่งที่ชนะ (0.0-1.0) → 1-3 คะแนน"""
    if weight_ratio >= 0.70:
        return 3
    if weight_ratio >= 0.55:
        return 2
    return 1


def _score_signal_strength(total_score: float) -> int:
    """ให้คะแนนความแรงของ weighted total score → 1-3 คะแนน"""
    abs_score = abs(total_score)
    if abs_score >= 15:
        return 3
    if abs_score >= 8:
        return 2
    return 1


def _score_confidence(confidence: float) -> int:
    """ให้คะแนน master confidence (0.0-1.0) → 1-3 คะแนน"""
    if confidence >= 0.85:
        return 3
    if confidence >= 0.70:
        return 2
    return 1


def grade_trade(
    weight_ratio: float,
    total_score: float,
    confidence: float,
    atr_ratio: float,
) -> tuple[str, str]:
    """
    คำนวณเกรด trade (A/B/C/D) ตามเกณฑ์ใน docs/TRADE_GRADING.md
    คืน (grade, breakdown) เช่น ("B", "consensus=2 strength=2 confidence=2 = 6/9")
    """
    c_score = _score_consensus(weight_ratio)
    s_score = _score_signal_strength(total_score)
    conf_score = _score_confidence(confidence)
    total = c_score + s_score + conf_score

    if total >= 8:
        grade = "A"
    elif total >= 6:
        grade = "B"
    elif total >= 4:
        grade = "C"
    else:
        grade = "D"

    low_vol = atr_ratio < LOW_VOLATILITY_THRESHOLD
    if low_vol and grade != "D":
        grade = GRADES[GRADES.index(grade) + 1]

    breakdown = (
        f"consensus={c_score} strength={s_score} confidence={conf_score} "
        f"= {total}/9{' | ATR ต่ำ → ลดเกรด' if low_vol else ''}"
    )
    logger.info(f"[grader] {breakdown} → grade {grade}")
    return grade, breakdown
