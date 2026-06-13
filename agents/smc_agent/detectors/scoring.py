"""
scoring.py — Scoring System
นับ criteria ที่ผ่าน (เต็ม 6 ข้อ) แล้วแปลงเป็น score -3..+3 ตามทิศทาง entry_signal
"""

CRITERIA_KEYS = ["htf_fvg", "session", "stop_hunt", "displacement", "ob_detected", "rr_ok"]

# จำนวน criteria ที่ผ่าน -> ขนาด score (criteria_met < 4 -> NO_SETUP)
SCORE_MAP = {4: 1, 5: 2, 6: 3}


def calculate_score(criteria: dict, entry_signal: str) -> dict:
    """
    criteria = {htf_fvg, session, stop_hunt, displacement, ob_detected, rr_ok} (bool ทุกตัว)
    entry_signal = "LONG" | "SHORT" | "NO_SETUP" จาก check_entry()

    Map criteria_met -> |score|:
        6/6 -> 3, 5/6 -> 2, 4/6 -> 1, < 4 -> NO_SETUP (score = 0)
    เครื่องหมาย score ตาม entry_signal: LONG = +, SHORT = -

    confidence = (criteria_met / 6) * 100

    คืน {signal, score, confidence}
    """
    criteria_met = sum(1 for key in CRITERIA_KEYS if criteria.get(key))
    confidence = round(criteria_met / len(CRITERIA_KEYS) * 100, 1)

    if criteria_met < 4 or entry_signal == "NO_SETUP":
        return {"signal": "NO_SETUP", "score": 0, "confidence": confidence}

    magnitude = SCORE_MAP[criteria_met]
    score = magnitude if entry_signal == "LONG" else -magnitude

    return {"signal": entry_signal, "score": score, "confidence": confidence}
