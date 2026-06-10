"""
Edge monitor — "watch the algo, be ready".

Maintains a rolling statistical battery per symbol on the live tick stream:
  - last-digit frequencies + chi-square uniformity
  - parity (even/odd) bias with Wilson bounds
  - over/under cut-points win probabilities with Wilson bounds
  - streak / lag-1 Markov memory check
  - drift t-statistic on tick returns

and compares the *lower confidence bound* of every observable win probability
against the *live payout* break-even for that exact contract. The day the RNG
develops a bias bigger than the house edge, `exploitable()` returns the
contract to fire on. Until then it reports the honest truth: RED.

This is the mathematically correct version of "watching the algo".
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field


def wilson_lower(wins: int, n: int, z: float) -> float:
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def chi2_uniform_pvalue(counts: list[int]) -> float:
    """Chi-square GOF vs uniform over 10 digits; Wilson–Hilferty p approx."""
    n = sum(counts)
    if n < 100:
        return 1.0
    exp = n / 10.0
    chi2 = sum((c - exp) ** 2 / exp for c in counts)
    k = 9.0  # dof
    # Wilson–Hilferty cube-root normal approximation
    x = (chi2 / k) ** (1.0 / 3.0)
    mu = 1 - 2.0 / (9 * k)
    sd = math.sqrt(2.0 / (9 * k))
    zscore = (x - mu) / sd
    # one-sided upper tail p
    return 0.5 * math.erfc(zscore / math.sqrt(2))


@dataclass
class SymbolStats:
    window: int
    digits: deque = field(default_factory=deque)
    quotes: deque = field(default_factory=deque)
    digit_counts: list = field(default_factory=lambda: [0] * 10)
    pair_counts: dict = field(default_factory=dict)  # (prev,cur) parity transitions
    last_tick_ts: float = 0.0
    tick_count: int = 0

    def add(self, quote: float, digit: int):
        self.tick_count += 1
        self.last_tick_ts = time.time()
        if len(self.digits) >= self.window:
            old = self.digits.popleft()
            self.digit_counts[old] -= 1
            self.quotes.popleft()
        if self.digits:
            prev_par = self.digits[-1] % 2
            key = (prev_par, digit % 2)
            self.pair_counts[key] = self.pair_counts.get(key, 0) + 1
        self.digits.append(digit)
        self.digit_counts[digit] += 1
        self.quotes.append(quote)

    # ---- probabilities (empirical) ----
    def n(self) -> int:
        return len(self.digits)

    def p_even(self) -> tuple[int, int]:
        wins = sum(self.digit_counts[d] for d in (0, 2, 4, 6, 8))
        return wins, self.n()

    def p_over(self, barrier: int) -> tuple[int, int]:
        wins = sum(self.digit_counts[d] for d in range(barrier + 1, 10))
        return wins, self.n()

    def p_under(self, barrier: int) -> tuple[int, int]:
        wins = sum(self.digit_counts[d] for d in range(0, barrier))
        return wins, self.n()

    def p_differs(self, digit: int) -> tuple[int, int]:
        return self.n() - self.digit_counts[digit], self.n()

    def p_matches(self, digit: int) -> tuple[int, int]:
        return self.digit_counts[digit], self.n()

    def drift_t(self) -> float:
        q = list(self.quotes)
        if len(q) < 200:
            return 0.0
        rets = [(q[i + 1] - q[i]) / q[i] for i in range(len(q) - 1)]
        n = len(rets)
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        if var <= 0:
            return 0.0
        return mean / math.sqrt(var / n)

    def chi2_p(self) -> float:
        return chi2_uniform_pvalue(self.digit_counts)


@dataclass
class EdgeOpportunity:
    symbol: str
    contract_type: str
    barrier: int | None
    p_lower: float
    p_hat: float
    payout: float
    breakeven: float
    edge_pct: float
    n: int


class EdgeMonitor:
    def __init__(self, window: int, min_ticks: int, z: float):
        self.window = window
        self.min_ticks = min_ticks
        self.z = z
        self.symbols: dict[str, SymbolStats] = {}
        # live payout cache: (symbol, ctype, barrier) -> (payout_mult, ts)
        self.payouts: dict = {}
        self.last_scan: dict = {}

    def stats_for(self, symbol: str) -> SymbolStats:
        if symbol not in self.symbols:
            self.symbols[symbol] = SymbolStats(window=self.window)
        return self.symbols[symbol]

    def on_tick(self, symbol: str, quote: float, pip_size: float):
        # last digit per Deriv display precision
        digits = max(0, int(round(-math.log10(pip_size)))) if pip_size > 0 else 2
        txt = f"{quote:.{digits}f}"
        digit = int(txt[-1])
        self.stats_for(symbol).add(quote, digit)

    def set_payout(self, symbol: str, ctype: str, barrier, payout_mult: float):
        self.payouts[(symbol, ctype, barrier)] = (payout_mult, time.time())

    def get_payout(self, symbol: str, ctype: str, barrier) -> float | None:
        v = self.payouts.get((symbol, ctype, barrier))
        if not v:
            return None
        return v[0]

    # ------------------------------------------------------------------ scan
    def scan(self, symbol: str) -> list[EdgeOpportunity]:
        """All digit contracts whose Wilson-lower win prob beats live breakeven."""
        st = self.stats_for(symbol)
        n = st.n()
        out: list[EdgeOpportunity] = []
        if n < self.min_ticks:
            return out
        candidates: list[tuple[str, int | None, tuple[int, int]]] = [
            ("DIGITEVEN", None, st.p_even()),
            ("DIGITODD", None, (st.n() - st.p_even()[0], st.n())),
        ]
        for b in range(0, 9):
            candidates.append(("DIGITOVER", b, st.p_over(b)))
        for b in range(1, 10):
            candidates.append(("DIGITUNDER", b, st.p_under(b)))
        for d in range(0, 10):
            candidates.append(("DIGITDIFF", d, st.p_differs(d)))
            candidates.append(("DIGITMATCH", d, st.p_matches(d)))

        for ctype, barrier, (wins, tot) in candidates:
            payout = self.get_payout(symbol, ctype, barrier)
            if not payout or payout <= 1.0:
                continue
            breakeven = 1.0 / payout
            lo = wilson_lower(wins, tot, self.z)
            if lo > breakeven:
                out.append(
                    EdgeOpportunity(
                        symbol=symbol, contract_type=ctype, barrier=barrier,
                        p_lower=lo, p_hat=wins / tot, payout=payout,
                        breakeven=breakeven, edge_pct=(lo * payout - 1) * 100, n=tot,
                    )
                )
        self.last_scan[symbol] = time.time()
        return out

    def snapshot(self) -> dict:
        out = {}
        for sym, st in self.symbols.items():
            n = st.n()
            even_w, _ = st.p_even()
            out[sym] = {
                "ticks_in_window": n,
                "total_ticks_seen": st.tick_count,
                "chi2_uniform_p": round(st.chi2_p(), 4),
                "p_even_hat": round(even_w / n, 4) if n else None,
                "drift_t": round(st.drift_t(), 2),
                "digit_counts": list(st.digit_counts),
                "verdict": "RNG fair (no exploitable bias)" if st.chi2_p() > 0.001 else "DEVIATION — check scan",
            }
        return out
