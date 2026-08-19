"""Backtest performance report: the gate before any demo execution.

Specifically surfaces the daily-return distribution, since the question this
whole system needs to answer honestly is whether a 2-5%/day target is
plausible for the encoded strategy — not just whether it's profitable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ctrader_bot.backtest.engine import BacktestResult


@dataclass
class Report:
    total_trades: int
    win_rate_pct: float
    profit_factor: float | None
    expectancy: float
    max_drawdown_pct: float
    final_equity: float
    total_return_pct: float
    daily_return_mean_pct: float
    daily_return_median_pct: float
    daily_return_std_pct: float
    pct_days_meeting_2pct: float
    pct_days_meeting_5pct: float
    num_trading_days: int


def _max_drawdown_pct(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min() * 100)


def build_report(result: BacktestResult, initial_equity: float) -> Report:
    trades = result.trades
    eq = result.equity_curve

    wins = [t for t in trades if t.pnl and t.pnl > 0]
    losses = [t for t in trades if t.pnl and t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    expectancy = (sum(t.pnl for t in trades if t.pnl is not None) / len(trades)) if trades else 0.0
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0

    final_equity = float(eq["equity"].iloc[-1]) if not eq.empty else initial_equity
    total_return_pct = (final_equity / initial_equity - 1) * 100
    max_dd = _max_drawdown_pct(eq["equity"]) if not eq.empty else 0.0

    if not eq.empty:
        daily = eq.copy()
        daily["date"] = daily["timestamp"].dt.date
        daily_close = daily.groupby("date")["equity"].last()
        daily_returns_pct = daily_close.pct_change().dropna() * 100
    else:
        daily_returns_pct = pd.Series(dtype=float)

    return Report(
        total_trades=len(trades),
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_drawdown_pct=max_dd,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        daily_return_mean_pct=float(daily_returns_pct.mean()) if len(daily_returns_pct) else 0.0,
        daily_return_median_pct=float(daily_returns_pct.median()) if len(daily_returns_pct) else 0.0,
        daily_return_std_pct=float(daily_returns_pct.std()) if len(daily_returns_pct) else 0.0,
        pct_days_meeting_2pct=float((daily_returns_pct >= 2.0).mean() * 100) if len(daily_returns_pct) else 0.0,
        pct_days_meeting_5pct=float((daily_returns_pct >= 5.0).mean() * 100) if len(daily_returns_pct) else 0.0,
        num_trading_days=len(daily_returns_pct),
    )


def format_report(report: Report) -> str:
    pf = f"{report.profit_factor:.2f}" if report.profit_factor is not None else "n/a (no losing trades)"
    return f"""
Backtest Report
===============
Total trades:        {report.total_trades}
Win rate:             {report.win_rate_pct:.1f}%
Profit factor:        {pf}
Expectancy/trade:     {report.expectancy:.2f}
Max drawdown:         {report.max_drawdown_pct:.2f}%
Final equity:         {report.final_equity:.2f}
Total return:         {report.total_return_pct:.2f}%

Daily return distribution ({report.num_trading_days} trading days)
  mean:               {report.daily_return_mean_pct:.3f}%
  median:             {report.daily_return_median_pct:.3f}%
  std dev:            {report.daily_return_std_pct:.3f}%
  days >= 2% target:  {report.pct_days_meeting_2pct:.1f}% of trading days
  days >= 5% target:  {report.pct_days_meeting_5pct:.1f}% of trading days

Reality check: compare daily_return_mean_pct against the 2-5%/day target
before drawing any conclusions about live viability. A high win rate with
low mean daily return usually means position sizing (risk_per_trade_pct) is
the lever to revisit, not the signal logic.
"""
