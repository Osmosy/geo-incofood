#!/usr/bin/env python3
"""geo_quotes.py — дословные цитаты для отчёта.

Для указанных промптов печатает по одному предложению из сырых ответов:
где назван конкурент и где бренд отсутствует/присутствует. Нужен, чтобы
цитаты в report.md были сверены с measurements/raw, а не написаны по памяти.

Usage:
  python3 tools/geo_quotes.py --brand beltradition --prompts cat-02,cmp-02 --limit 2
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_mark import load_raw, find_alias, strip_prompt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SENT = re.compile(r"(?<=[.!?])\s+")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--prompts", required=True)
    ap.add_argument("--limit", type=int, default=2, help="файлов на промпт")
    a = ap.parse_args()

    bdir = ROOT / a.brand
    brief = json.loads((bdir / "brand.json").read_text(encoding="utf-8"))
    competitors = brief["competitors"]
    prompts = {r["id"]: r["text"] for r in csv.DictReader((bdir / "prompts.csv").open(encoding="utf-8"))}

    for pid in [p.strip() for p in a.prompts.split(",")]:
        print(f"\n=== {pid}: {prompts.get(pid, '?')}")
        files = sorted((bdir / "measurements" / "raw").glob(f"*-{pid}-*.md"))[: a.limit * 2]
        for f in files[: a.limit]:
            head, text, sources = load_raw(f)
            text = strip_prompt(text, prompts.get(pid, ""))
            sents = SENT.split(re.sub(r"\s+", " ", text))
            comp = next((s for s in sents
                         if any(find_alias(s, c["name"]) or any(find_alias(s, al) for al in c.get("aliases", []))
                                for c in competitors)), "")
            brand_line = next((s for s in sents
                               if find_alias(s, brief["brand"]["name"])), "")
            print(f"  [{head.get('engine')} r{head.get('repeat')}] конкурент: {comp[:260]}")
            if brand_line:
                print(f"      бренд: {brand_line[:200]}")
        print()


if __name__ == "__main__":
    raise SystemExit(main())
