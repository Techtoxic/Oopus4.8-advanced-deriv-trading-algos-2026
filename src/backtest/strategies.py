"""
The popular Deriv strategies retail traders actually run and sell, expressed
against the backtest engine. None of them change the contract's marginal win
probability — that's the whole point — but we test them honestly anyway.
"""
import numpy as np
from engine import Order

# ---- entry signals: strategy(i, prices, digits) -> Order | None ----

def always_differs(i, prices, digits):
    # Bet the next settled digit DIFFERS from the current digit (lowest house edge).
    return Order("DIGITDIFF", barrier=int(digits[i]), duration=1)

def always_even(i, prices, digits):
    return Order("DIGITEVEN", duration=1)

def always_over0(i, prices, digits):
    # "90% win" contract: digit > 0.
    return Order("DIGITOVER", barrier=0, duration=1)

def gambler_low_then_over(i, prices, digits):
    # Fallacy: a low digit just printed, so "it must go high" -> bet Over 3.
    if digits[i] <= 2:
        return Order("DIGITOVER", barrier=3, duration=1)
    return None

def gambler_after_match_differs(i, prices, digits):
    # Fallacy: two equal digits in a row -> "won't repeat again" -> Differs.
    if i >= 1 and digits[i] == digits[i-1]:
        return Order("DIGITDIFF", barrier=int(digits[i]), duration=1)
    return None

def momentum_rise(k):
    def f(i, prices, digits):
        if i < k:
            return None
        d = np.diff(prices[i-k:i+1])
        if np.all(d > 0):          # k consecutive ups -> ride the trend
            return Order("CALL", duration=1)
        return None
    return f

def meanrev_rise(k):
    def f(i, prices, digits):
        if i < k:
            return None
        d = np.diff(prices[i-k:i+1])
        if np.all(d < 0):          # k consecutive downs -> bet the bounce
            return Order("CALL", duration=1)
        return None
    return f

# ---- stake sizing: stake_fn(state) -> $ ----

def flat(base):
    return lambda s: base

def martingale(base, mult=2.0):
    # Double (or x mult) the stake after every loss; reset to base after a win.
    return lambda s: base * (mult ** s["loss_streak"])

def anti_martingale(base, mult=2.0, cap=4):
    # Press winners: grow stake on a win streak up to cap, reset after a loss.
    return lambda s: base * (mult ** min(s["win_streak"], cap))

def kelly_zero(base):
    # Honest Kelly on a negative-EV bet prescribes ZERO; we floor at min stake
    # only to demonstrate that even minimal exposure bleeds the edge.
    return lambda s: s["min_stake"]
