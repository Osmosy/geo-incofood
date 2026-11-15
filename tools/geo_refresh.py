#!/usr/bin/env python3
"""geo_refresh.py — повторное извлечение ответа из сохранённого URL.

Зачем: первый прогон снимал `main.innerText`, и в текст попадала боковая панель
(история чатов) — чужие промпты засчитывались как упоминания бренда. Скрипт
переоткрывает чат по URL из шапки файла и перезаписывает только секции ответа,
оставляя шапку и имя файла.

Usage:
  python3 tools/geo_refresh.py --brand beltradition --engine alice
  python3 tools/geo_refresh.py --brand beltradition --engine deepseek --limit 5
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_run import ROOT, SELECTORS, ab, js  # noqa: E402


def extract_with(engine):
    sel = SELECTORS.get(engine, "")
    expr = """(() => {
      const sel = %s;
      const m = (sel && document.querySelector(sel)) || document.querySelector('main') || document.body;
      const text = (m.innerText || '').replace(/\\u00a0/g, ' ');
      const links = [...m.querySelectorAll('a[href^=http]')].map(a => a.href);
      return JSON.stringify({text, links: [...new Set(links)].slice(0, 40),
                             found: !!document.querySelector(sel)});
    })()""" % (repr(sel))
    return js(expr) or {}


def rebuild(path, engine, data):
    txt = path.read_text(encoding="utf-8", errors="replace")
    head = re.match(r"---\n.*?\n---\n", txt, re.S)
    header = head.group(0) if head else "---\n---\n"
    from geo_run import clean_answer
    body = ["", f"# {path.stem}", "", "## Answer (cleaned)", "",
            clean_answer(data.get("text", "")), "", "## Page text (raw)", "",
            data.get("text", "").strip(), "", "## Sources", ""]
    body += [f"- {u}" for u in data.get("links", [])] or ["- (нет ссылок в ответе)"]
    path.write_text(header + "\n".join(body) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--engine", required=True, choices=[e for e in SELECTORS if SELECTORS[e]])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--session", default="")
    a = ap.parse_args()
    import geo_run
    geo_run.SESSION = a.session

    files = sorted((ROOT / a.brand / "measurements" / "raw").glob(f"*-{a.engine}-*.md"))
    if a.limit:
        files = files[: a.limit]
    ok = bad = 0
    for p in files:
        txt = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^url:\s*(\S+)$", txt, re.M)
        if not m:
            bad += 1
            continue
        ab("open", m.group(1), timeout=120)
        time.sleep(3)
        data = extract_with(a.engine)
        if len(data.get("text", "")) < 200:
            ab("open", m.group(1), timeout=120)  # второй шанс после дозагрузки
            time.sleep(4)
            data = extract_with(a.engine)
        if len(data.get("text", "")) < 200:
            bad += 1
            print(f"FAIL {p.name}: {len(data.get('text',''))} знаков", file=sys.stderr, flush=True)
            continue
        rebuild(p, a.engine, data)
        ok += 1
        print(f"ok {p.name} — {len(data['text'])} знаков, {len(data.get('links', []))} ссылок",
              flush=True)
    print(f"\nперезаписано {ok}, сбоев {bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
