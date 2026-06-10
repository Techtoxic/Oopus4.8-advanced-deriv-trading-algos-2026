"""
FastAPI entrypoint. Run: uvicorn app:app --host 0.0.0.0 --port $PORT

Endpoints
  GET  /            tiny live dashboard (auto-refresh)
  GET  /health      cheap health probe   ← point Render healthCheckPath here
  GET  /wake        alias of /health     ← point your cron keep-alive here
  GET  /status      full bot state JSON
  GET  /edge        live RNG battery per symbol
  GET  /trades      ledger snapshot (totals, per strategy, recent trades)
  POST /control     {"action": "pause"|"resume"}  header X-Control-Key
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from omnibot.bot import OmniBot
from omnibot.config import CFG

logging.basicConfig(
    level=getattr(logging, CFG.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)

BOT = OmniBot(CFG)
STARTED = time.time()


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(BOT.start())
    yield
    task.cancel()
    await BOT.stop()


app = FastAPI(title="Fable OmniBot", lifespan=lifespan)


@app.get("/health")
@app.get("/wake")
async def health():
    return {
        "status": "ok",
        "uptime_s": int(time.time() - STARTED),
        "ws_connected": BOT.client.connected.is_set(),
        "mode": CFG.mode,
        "pnl_today": round(BOT.risk.realized_pnl_today, 2),
    }


@app.get("/status")
async def status():
    return BOT.status()


@app.get("/edge")
async def edge():
    return {
        "explainer": "Wilson-99.9% lower bound of each contract's live win rate "
                     "must beat 1/payout for the sentinel to fire. chi2_uniform_p "
                     "small (<0.001) would mean the RNG is biased.",
        "symbols": BOT.edge.snapshot(),
    }


@app.get("/trades")
async def trades():
    return BOT.ledger.snapshot()


@app.post("/control")
async def control(body: dict, x_control_key: str = Header(default="")):
    if CFG.control_key and x_control_key != CFG.control_key:
        raise HTTPException(401, "bad control key")
    action = (body or {}).get("action", "")
    if action == "pause":
        BOT.set_paused(True)
    elif action == "resume":
        BOT.set_paused(False)
    else:
        raise HTTPException(400, "action must be pause|resume")
    return {"ok": True, "paused": BOT.paused}


@app.get("/", response_class=HTMLResponse)
async def index():
    s = BOT.status()
    led = BOT.ledger.snapshot()
    t = led["totals"]
    rows = ""
    for r in reversed(led["recent"][-15:]):
        cls = "win" if r.get("profit", 0) >= 0 else "loss"
        rows += (f"<tr class={cls}><td>{r.get('utc','')[:19]}</td><td>{r.get('strategy')}</td>"
                 f"<td>{r.get('symbol')}</td><td>{r.get('contract_type')}</td>"
                 f"<td>{r.get('stake'):.2f}</td><td>{r.get('profit'):+.2f}</td></tr>")
    html = f"""<!doctype html><html><head><meta http-equiv=refresh content=15>
<title>Fable OmniBot</title><style>
body{{font-family:ui-monospace,monospace;background:#0d1117;color:#c9d1d9;padding:20px}}
h1{{color:#58a6ff}} .k{{color:#8b949e}} table{{border-collapse:collapse;margin-top:10px}}
td,th{{padding:4px 10px;border-bottom:1px solid #21262d;text-align:left}}
.win td{{color:#3fb950}} .loss td{{color:#f85149}}
.badge{{padding:2px 8px;border-radius:6px;background:#21262d}}
</style></head><body>
<h1>🤖 Fable OmniBot <span class=badge>{s['mode']}</span></h1>
<p class=k>account {s['account']['loginid']} (virtual={s['account']['is_virtual']})
 balance <b>{s['account']['balance']}</b> {s['account']['currency'] or ''} |
 ws {'🟢' if s['ws']['connected'] else '🔴'} | uptime {s['uptime_s']}s |
 pnl today <b>{s['risk']['realized_pnl_today']}</b> | halted: {s['risk']['halted']}</p>
<p class=k>totals: {t['trades']} trades, win rate {t['win_rate']}, turnover {t['staked']:.2f},
 net P/L <b>{t['pnl']:+.2f}</b>, ROI on turnover {t['roi_on_turnover']}</p>
<table><tr><th>utc</th><th>strategy</th><th>symbol</th><th>type</th><th>stake</th><th>profit</th></tr>
{rows}</table>
<p class=k>The sentinel fires only if live stats beat live payouts (they don't, on a fair RNG —
that's the finding). Flow strategies are execution-readiness drills with hard risk caps.
Endpoints: /health /wake /status /edge /trades</p>
</body></html>"""
    return HTMLResponse(html)
