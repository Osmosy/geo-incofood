#!/usr/bin/env python3
"""geo_run_api.py — замер через API-харнесс (без браузера и логина).

Зачем: веб-сессия DeepSeek разлогинилась посреди волны и залила сырьё
страницей входа. API не зависит от сессии и повторов «в новом чате»:
каждый запрос — чистый, без истории.

Ограничение, которое надо писать в отчёт: API — это канал памяти.
Веб-поиска в API нет, ссылок ответ не содержит, поэтому web_search=false
и cite=0 по определению. С веб-каналом такие строки не смешиваются.

Usage:
  python3 tools/geo_run_api.py --brand natura                  # все промпты, 3 повтора
  python3 tools/geo_run_api.py --brand natura --limit 2
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"  # DeepSeek-V4.1-Flash


def key():
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    env = Path.home() / ".hermes" / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("нет DEEPSEEK_API_KEY")


def ask(prompt, k, timeout=180, tries=4):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {k}", "Content-Type": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise
    choice = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return choice, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--prompts", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    bdir = ROOT / a.brand
    raw = bdir / "measurements" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader((bdir / "prompts.csv").open(encoding="utf-8")))
    want = {p.strip() for p in a.prompts.split(",") if p.strip()}
    if want:
        rows = [r for r in rows if r["id"] in want]
    if a.limit:
        rows = rows[: a.limit]
    k = key()
    date = time.strftime("%Y-%m-%d")
    done = skipped = failed = 0
    tokens = 0
    for row in rows:
        for rep in range(1, a.repeats + 1):
            path = raw / f"{date}-deepseek-api-{row['id']}-{rep}.md"
            if path.exists() and not a.force:
                skipped += 1
                continue
            try:
                answer, usage = ask(row["text"], k)
            except urllib.error.HTTPError as e:
                print(f"FAIL {row['id']} r{rep}: HTTP {e.code} {e.read()[:200]!r}", file=sys.stderr)
                failed += 1
                if e.code in (401, 402):
                    raise SystemExit("ключ/баланс: волна остановлена")
                time.sleep(5)
                continue
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {row['id']} r{rep}: {e}", file=sys.stderr)
                failed += 1
                continue
            tokens += usage.get("total_tokens", 0)
            body = ["---", "engine: deepseek-api", f"prompt_id: {row['id']}",
                    f"scenario_class: {row['scenario_class']}", f"weight: {row['weight']}",
                    f"repeat: {rep}", f"date: {date}", "url: api://deepseek/chat/completions",
                    f"model: {MODEL}",
                    f"captured: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
                    "channel: memory (API без веб-поиска)", "---", "",
                    f"# {row['text']}", "", "## Answer (cleaned)", "", answer.strip(), "",
                    "## Page text (raw)", "", answer.strip(), "", "## Sources", "",
                    "- (API не отдаёт веб-поиск и ссылки: канал памяти)"]
            path.write_text("\n".join(body) + "\n", encoding="utf-8")
            done += 1
            print(f"ok {row['id']} r{rep} — {len(answer)} знаков, {usage.get('total_tokens', 0)} ток.", flush=True)
    print(f"\nитог: записано {done}, пропущено {skipped}, сбоев {failed}, токенов {tokens}")


if __name__ == "__main__":
    main()
