"""run_all.py — reproduce every validation artifact end to end.

    python validation/run_all.py            # uses cached data/ (fast)
    python validation/run_all.py --fetch     # re-pull fresh ticks + payouts first
"""
import os, subprocess, sys

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")


def sh(mod, *args):
    print("\n" + "#" * 80 + f"\n# {mod} {' '.join(args)}\n" + "#" * 80, flush=True)
    return subprocess.call([sys.executable, os.path.join(HERE, mod), *args])


def main():
    if "--fetch" in sys.argv:
        sh("fetch_data.py"); sh("fetch_payouts.py")
    sh("structural_invariant.py")
    sh("digit_uniformity.py")
    sh("test_untapped_fill.py")
    sh("test_higher_order.py")
    sh("test_skew_predictiveness.py")
    sh("test_regression_location.py")
    sh("controls.py")
    # full pipeline backtest
    print("\n" + "#" * 80 + "\n# backtest/backtest_lattice.py\n" + "#" * 80, flush=True)
    subprocess.call([sys.executable, os.path.join(ROOT, "backtest", "backtest_lattice.py")])
    sh("plots.py")
    print("\nAll artifacts regenerated in results/.")


if __name__ == "__main__":
    main()
