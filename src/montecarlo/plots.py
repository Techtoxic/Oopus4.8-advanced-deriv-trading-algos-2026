"""Generate figures for the report (saved to results/*.png)."""
import os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["text.parse_math"] = False  # render literal $ in titles/labels
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))
import common
from engine import backtest
import strategies as S

RES = os.path.join(os.path.dirname(__file__), "..", "..", "results")
os.makedirs(RES, exist_ok=True)
RNG = np.random.default_rng(7)

def fig_house_edge_smile():
    # live payouts captured for 1HZ100V
    over = {0:1.09,1:1.23,2:1.40,3:1.63,4:1.95,5:2.43,6:3.21,7:4.72,8:8.93}
    trueP = {0:0.9,1:0.8,2:0.7,3:0.6,4:0.5,5:0.4,6:0.3,7:0.2,8:0.1}
    barriers = sorted(over)
    edges = [-(trueP[b]*over[b]-1)*100 for b in barriers]
    plt.figure(figsize=(8,4.5))
    plt.bar([f"Over {b}" for b in barriers], edges, color="#c0392b")
    plt.axhline(0, color="k", lw=0.8)
    plt.ylabel("House edge (%)")
    plt.title("Every Deriv digit contract has a POSITIVE house edge (1HZ100V, live payouts)")
    for i,e in enumerate(edges):
        plt.text(i, e+0.1, f"{e:.1f}", ha="center", fontsize=8)
    plt.tight_layout(); plt.savefig(os.path.join(RES,"house_edge_smile.png"), dpi=110); plt.close()

def fig_flat_bleed():
    e,p = common.load("1HZ100V"); d = common.last_digit(p,2)
    res = backtest("differs flat", p, d, S.always_differs, S.flat(0.35),
                   bankroll=10.0, min_stake=0.35, stop_at_ruin=True)
    plt.figure(figsize=(8,4.5))
    plt.plot(res.equity, color="#2c3e50", lw=0.9)
    plt.axhline(10, color="green", ls="--", lw=0.8, label="start $10")
    plt.axhline(0.35, color="red", ls=":", lw=0.8, label="ruin")
    plt.xlabel("trade #"); plt.ylabel("bankroll ($)")
    plt.title(f"'Safe' 90%-win Differs, flat stake: steady bleed at the house edge\n"
              f"{res.wins}/{res.n_trades} wins ({res.win_rate*100:.1f}%) yet ends ${res.end_bankroll:.2f}")
    plt.legend(); plt.tight_layout(); plt.savefig(os.path.join(RES,"flat_bleed.png"), dpi=110); plt.close()

def fig_martingale_paths():
    # Simulate many target-martingale equity curves (even/odd, p=0.5, payout 1.95)
    p_win, payout, start, target, min_stake = 0.5, 1.95, 10.0, 1.0, 0.35
    net = payout-1
    plt.figure(figsize=(8,4.5))
    n_paths = 40; horizon_days = 30
    ruined=0
    for k in range(n_paths):
        bankroll = start; curve=[start]; dead=False
        for day in range(horizon_days):
            invested=0.0
            while True:
                need=max((invested+target)/net, min_stake)
                if need>bankroll+1e-9:
                    bankroll=0.0; dead=True; break
                bankroll-=need; invested+=need
                if RNG.random()<p_win:
                    bankroll+=need*payout; break
            curve.append(bankroll)
            if dead: break
        if dead: ruined+=1
        plt.plot(curve, lw=0.8, alpha=0.6, color=("#c0392b" if dead else "#27ae60"))
    plt.axhline(10, color="k", ls="--", lw=0.8)
    plt.xlabel("day"); plt.ylabel("bankroll ($)")
    plt.title(f"$1/day martingale, $10 start, 30 days x {n_paths} runs\n"
              f"{ruined}/{n_paths} blew up to $0 (red). Green ones just haven't yet.")
    plt.tight_layout(); plt.savefig(os.path.join(RES,"martingale_paths.png"), dpi=110); plt.close()

def fig_crashboom():
    e,p = common.load("CRASH1000")
    seg = p[:6000]
    plt.figure(figsize=(8,4.5))
    plt.plot(seg, color="#2980b9", lw=0.7)
    plt.xlabel("tick"); plt.ylabel("price")
    plt.title("CRASH 1000: many tiny up-ticks, rare violent crashes.\n"
              "Net drift ~ 0 -> the crash repays every up-tick. Timing is memoryless.")
    plt.tight_layout(); plt.savefig(os.path.join(RES,"crash1000_segment.png"), dpi=110); plt.close()

if __name__ == "__main__":
    fig_house_edge_smile(); print("house_edge_smile.png")
    fig_flat_bleed();       print("flat_bleed.png")
    fig_martingale_paths(); print("martingale_paths.png")
    fig_crashboom();        print("crash1000_segment.png")
    print("figures written to results/")
