"""
position_monitor.py — ตรวจสอบ paper trades ว่าโดน TP หรือ SL แล้วหรือยัง
รันทุก 30 วินาทีใน main loop — คำนวณ PnL และปิด trade อัตโนมัติ
"""

from loguru import logger


class PositionMonitor:
    """
    เช็ก open trades ทุกรอบ:
    - ถ้าราคาแตะ TP → ปิด CLOSED + บันทึก PnL บวก
    - ถ้าราคาแตะ SL → ปิด STOPPED + บันทึก PnL ลบ
    ใช้ได้ทั้ง paper trade และ real trade
    """

    def __init__(self, data_fetcher, db):
        self.data_fetcher = data_fetcher
        self.db = db

    async def check(self) -> list[dict]:
        """
        ตรวจ open trades ทั้งหมด เทียบกับราคาปัจจุบัน
        คืน list ของ trades ที่ปิดในรอบนี้
        """
        closed_this_round = []

        try:
            open_trades = await self.db.get_open_trades()
            if not open_trades:
                return []

            price = await self.data_fetcher.get_current_price()

            for trade in open_trades:
                result = self._check_trade(trade, price)
                if result:
                    exit_price, pnl, status = result
                    await self.db.close_trade(trade["id"], exit_price, pnl, status)
                    closed_this_round.append({
                        "id": trade["id"],
                        "side": trade["side"],
                        "entry": trade["entry_price"],
                        "exit": exit_price,
                        "pnl": pnl,
                        "status": status,
                    })
                    icon = "✅" if pnl > 0 else "❌"
                    logger.info(
                        f"{icon} Trade #{trade['id']} {status} | "
                        f"{trade['side']} entry={trade['entry_price']:.2f} "
                        f"exit={exit_price:.2f} PnL={pnl:+.4f} ETH"
                    )

        except Exception as e:
            logger.error(f"PositionMonitor.check error: {e}")

        return closed_this_round

    def _check_trade(self, trade: dict, price: float):
        """
        ตรวจว่า trade โดน TP หรือ SL ไหม
        คืน (exit_price, pnl, status) หรือ None ถ้ายังไม่โดน

        PnL คำนวณเป็น USDT:
          LONG:  (exit - entry) * size
          SHORT: (entry - exit) * size
        """
        side       = trade.get("side", "")
        entry      = float(trade.get("entry_price", 0) or 0)
        tp         = float(trade.get("tp_price", 0) or 0)
        sl         = float(trade.get("sl_price", 0) or 0)
        size       = float(trade.get("size", 0) or 0)

        if not entry or not size:
            return None

        if side == "LONG":
            if tp and price >= tp:
                pnl = (tp - entry) * size
                return tp, round(pnl, 4), "CLOSED"
            if sl and price <= sl:
                pnl = (sl - entry) * size
                return sl, round(pnl, 4), "STOPPED"

        elif side == "SHORT":
            if tp and price <= tp:
                pnl = (entry - tp) * size
                return tp, round(pnl, 4), "CLOSED"
            if sl and price >= sl:
                pnl = (entry - sl) * size
                return sl, round(pnl, 4), "STOPPED"

        return None
