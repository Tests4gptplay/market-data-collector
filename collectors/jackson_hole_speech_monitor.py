#!/usr/bin/env python3
"""Five-minute Jackson Hole keynote monitor (stdlib only).

Streams JSON Lines to stdout and writes snapshots/events/summary artifacts.
Market quotes are best-effort Yahoo Finance spot symbols with futures fallbacks.
The targeted Jackson Hole speech feed is the primary signal; markets are auxiliary.
"""
from __future__ import annotations
import argparse, datetime as dt, email.utils, hashlib, json, os, pathlib, sys, time
import urllib.parse, urllib.request, xml.etree.ElementTree as ET

UA = "market-data-collector/jackson-hole-speech-monitor (+github-actions)"
MARKET = {
    "xau_usd": ["XAUUSD=X", "GC=F"],
    "xag_usd": ["XAGUSD=X", "SI=F"],
    "dxy": ["DX-Y.NYB"],
    "us10y_nominal": ["^TNX"],
    "us30y_nominal": ["^TYX"],
}
PRIORITY_FEEDS = {
    "federal_reserve_speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
}
AUX_FEEDS = {
    "federal_reserve_press": "https://www.federalreserve.gov/feeds/press_all.xml",
    "us_treasury": "https://home.treasury.gov/news/press-releases/feed",
    "white_house": "https://www.whitehouse.gov/briefing-room/feed/",
}
FEEDS = {**PRIORITY_FEEDS, **AUX_FEEDS}
TARGET_TERMS = ("kevin warsh", "warsh", "jackson hole", "keynote remarks",
                "financial innovation", "payments and policy")
KEYWORDS = (
    "federal reserve", "monetary", "interest rate", "inflation", "treasury",
    "debt", "auction", "refunding", "fiscal", "deficit", "dollar",
    "currency", "sanction", "tariff", "credit", "funding", "liquidity",
    *TARGET_TERMS,
)
UTC = dt.timezone.utc

def now_iso():
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")

def emit(kind, payload):
    row = {"type": kind, "observed_at": now_iso(), **payload}
    print(json.dumps(row, ensure_ascii=False, separators=(",", ":")), flush=True)
    return row

def get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def yahoo_quote(symbols):
    joined = ",".join(urllib.parse.quote(s, safe="") for s in symbols)
    url = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=" + joined
    data = json.loads(get(url).decode("utf-8"))
    return {x.get("symbol"): x for x in data["quoteResponse"]["result"]}

def market_snapshot():
    all_symbols = [s for choices in MARKET.values() for s in choices]
    raw = yahoo_quote(all_symbols)
    values, provenance = {}, {}
    for key, choices in MARKET.items():
        selected = next((raw[s] for s in choices if s in raw and raw[s].get("regularMarketPrice") is not None), None)
        if not selected:
            values[key] = None
            provenance[key] = {"status": "unavailable", "candidates": choices}
            continue
        value = float(selected["regularMarketPrice"])
        values[key] = value
        provenance[key] = {
            "symbol": selected["symbol"],
            "exchange_time": selected.get("regularMarketTime"),
            "delay_seconds": selected.get("exchangeDataDelayedBy", 0),
            "market_state": selected.get("marketState"),
            "instrument": "spot" if selected["symbol"].endswith("=X") else ("futures_proxy" if selected["symbol"].endswith("=F") else "index"),
        }
    return values, provenance

def fred_real_yield():
    # Daily official 10-year TIPS constant-maturity series; not intraday.
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
    lines = get(url, timeout=10).decode("utf-8").strip().splitlines()
    for line in reversed(lines[1:]):
        day, value = line.split(",", 1)
        if value not in ("", "."):
            return float(value), day
    return None, None

def parse_date(text):
    try:
        d = email.utils.parsedate_to_datetime(text)
        if not d.tzinfo:
            d = d.replace(tzinfo=UTC)
        return d.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except Exception:
        return None

def official_items(source, url):
    root = ET.fromstring(get(url, timeout=10))
    out = []
    for item in root.findall(".//item")[:30]:
        def val(name):
            node = item.find(name)
            return (node.text or "").strip() if node is not None else ""
        title, link, desc = val("title"), val("link"), val("description")
        published = parse_date(val("pubDate")) or val("pubDate") or None
        hay = (title + " " + desc).lower()
        if any(k in hay for k in KEYWORDS):
            ident = hashlib.sha256((source + "|" + link + "|" + title).encode()).hexdigest()[:20]
            out.append({"event_id": ident, "source": source, "title": title, "url": link, "published_at": published,
                        "target_match": any(k in hay for k in TARGET_TERMS)})
    return out

def pct(now, base):
    return None if now is None or not base else (now / base - 1.0) * 100.0

def classify(cur, base):
    moves = {k: pct(cur.get(k), base.get(k)) for k in ("xau_usd", "xag_usd", "dxy")}
    for k in ("us10y_nominal", "us30y_nominal", "us10y_real"):
        moves[k + "_bp"] = None if cur.get(k) is None or base.get(k) is None else (cur[k] - base[k]) * 100.0
    bull = sum([
        (moves["xau_usd"] or 0) >= .08, (moves["xag_usd"] or 0) >= .12,
        (moves["dxy"] or 0) <= -.03, (moves["us10y_nominal_bp"] or 0) <= -1.0,
        (moves["us30y_nominal_bp"] or 0) <= -1.0,
    ])
    bear = sum([
        (moves["xau_usd"] or 0) <= -.08, (moves["xag_usd"] or 0) <= -.12,
        (moves["dxy"] or 0) >= .03, (moves["us10y_nominal_bp"] or 0) >= 1.0,
        (moves["us30y_nominal_bp"] or 0) >= 1.0,
    ])
    if bull >= 3 and bull >= bear + 2:
        verdict, confidence = "偏多贵金属", "medium" if bull == 3 else "high"
    elif bear >= 3 and bear >= bull + 2:
        verdict, confidence = "偏空贵金属", "medium" if bear == 3 else "high"
    else:
        verdict, confidence = "暂不交易", "low"
    stronger = None
    if moves["xau_usd"] is not None and moves["xag_usd"] is not None:
        stronger = "silver" if moves["xag_usd"] > moves["xau_usd"] else "gold"
    return {"verdict": verdict, "confidence": confidence, "bull_votes": bull, "bear_votes": bear, "moves": moves, "relative_strength": stronger,
            "note": "Rule-based triage only; confirm event meaning and data freshness before trading."}

def append_jsonl(path, row):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-seconds", type=int, default=300)
    ap.add_argument("--market-interval", type=float, default=5)
    ap.add_argument("--news-interval", type=float, default=1,
                    help="Priority Federal Reserve speech-feed interval (1 second around release)")
    ap.add_argument("--aux-news-interval", type=float, default=20)
    ap.add_argument("--output-dir", default="output/jackson-hole-speech")
    args = ap.parse_args()
    out = pathlib.Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    snapshots, events = out / "snapshots.jsonl", out / "events.jsonl"
    start, next_market, next_news, next_aux_news, next_real = time.monotonic(), 0.0, 0.0, 0.0, 0.0
    baseline, latest, seen, errors = None, {}, set(), []
    emit("run_start", {"runtime_seconds": args.runtime_seconds, "market_interval_seconds": args.market_interval,
                       "priority_news_interval_seconds": args.news_interval,
                       "aux_news_interval_seconds": args.aux_news_interval,
                       "target": {"event": "2026 Jackson Hole keynote remarks", "speaker": "Chairman Kevin Warsh",
                                  "scheduled_at": "2026-08-28T14:00:00Z"},
                       "sources": FEEDS, "market_series": MARKET})
    while time.monotonic() - start < args.runtime_seconds:
        elapsed = time.monotonic() - start
        if elapsed >= next_market:
            try:
                values, provenance = market_snapshot()
                if elapsed >= next_real:
                    try:
                        values["us10y_real"], real_date = fred_real_yield()
                        latest["_real_date"] = real_date
                    except Exception as e:
                        values["us10y_real"] = latest.get("us10y_real")
                        errors.append({"at": now_iso(), "source": "fred_dfii10", "error": str(e)})
                    next_real = elapsed + 60
                else:
                    values["us10y_real"] = latest.get("us10y_real")
                latest.update(values)
                baseline = baseline or dict(latest)
                row = emit("snapshot", {"elapsed_seconds": round(elapsed, 3), "values": latest,
                    "provenance": provenance, "real_yield_observation_date": latest.get("_real_date"),
                    "signal": classify(latest, baseline)})
                append_jsonl(snapshots, row)
            except Exception as e:
                err = emit("source_error", {"source": "yahoo_finance", "error": str(e)})
                errors.append(err)
            next_market += args.market_interval
        if elapsed >= next_news:
            # Primary path: the official Federal Reserve speech feed. A matching
            # Jackson Hole/Warsh item is emitted immediately when first observed.
            for source, url in PRIORITY_FEEDS.items():
                try:
                    for item in official_items(source, url):
                        if item["event_id"] not in seen:
                            seen.add(item["event_id"])
                            if item["target_match"]:
                                row = emit("TARGET_EVENT", {**item, "priority": "critical"})
                                append_jsonl(events, row)
                except Exception as e:
                    err = emit("source_error", {"source": source, "error": str(e)})
                    errors.append(err)
            next_news += args.news_interval
        if elapsed >= next_aux_news:
            # Auxiliary context only; never delays the primary speech poll.
            for source, url in AUX_FEEDS.items():
                try:
                    for item in official_items(source, url):
                        if item["event_id"] not in seen:
                            seen.add(item["event_id"])
                            row = emit("official_context", {**item, "priority": "auxiliary"})
                            append_jsonl(events, row)
                except Exception as e:
                    err = emit("source_error", {"source": source, "error": str(e)})
                    errors.append(err)
            next_aux_news += args.aux_news_interval
        time.sleep(min(.25, max(0, args.runtime_seconds - (time.monotonic() - start))))
    summary = {"schema_version": "1.0", "started_at": dt.datetime.fromtimestamp(time.time()-(time.monotonic()-start), UTC).isoformat(),
               "finished_at": now_iso(), "baseline": baseline, "latest": latest,
               "signal": classify(latest, baseline) if baseline else {"verdict": "暂不交易", "confidence": "low"},
               "observed_item_count": len(seen), "target_event_seen": any(
                   '"type":"TARGET_EVENT"' in line for line in events.read_text(encoding="utf-8").splitlines()
               ) if events.exists() else False, "error_count": len(errors),
               "limitations": ["Yahoo quotes may be delayed and can fall back to futures proxies.",
                  "DFII10 is an official daily series, not an intraday real-yield quote.",
                  "Feed appearance can lag publication; GitHub Actions scheduling can start late."]}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("run_complete", summary)

if __name__ == "__main__":
    main()
