"""Shared helpers: load gzipped tick files, derive digits/returns."""
import gzip, json, os
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def load(symbol):
    path = os.path.join(DATA_DIR, f"{symbol}.json.gz")
    with gzip.open(path, "rt") as f:
        ticks = json.load(f)
    epochs = np.array([int(e) for e, _ in ticks], dtype=np.int64)
    prices = np.array([float(p) for _, p in ticks], dtype=float)
    return epochs, prices

def last_digit(prices, places=2):
    # Deriv last digit = last decimal digit at the instrument's pip precision.
    scaled = np.round(prices * (10 ** places)).astype(np.int64)
    return (scaled % 10).astype(int)

def infer_decimals(prices, maxp=6):
    """Infer the number of decimal places Deriv quotes for this symbol."""
    for p in range(0, maxp + 1):
        scaled = prices * (10 ** p)
        if np.max(np.abs(scaled - np.round(scaled))) < 1e-4:
            return p
    return maxp
