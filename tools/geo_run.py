#!/usr/bin/env python3
"""geo_run.py — замер GEO: гоняет промпты через браузерные движки и складывает сырые ответы.

Usage:
  python3 tools/geo_run.py --brand beltradition --engine alice --limit 2 --repeats 2
  python3 tools/geo_run.py --brand natura --engine perplexity --prompts cat-01,cat-02

Пишет <brand>/measurements/raw/<date>-<engine>-<prompt_id>-<r>.md
Ничего не размечает — это шаг geo_mark.py.
"""
import argparse
import csv
import re
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATE = date.today().isoformat()

CHROME_RE = re.compile(
    r"^(поделиться|новый чат|студия|мои заказы|для учёбы|недавние|не прочитано|главный чат|"
    r"больше возможностей|бесплатный тариф|выключить звук|интеллект:|алиса старается|"
    r"задайте уточняющий|войдите|показать все|посмотреть планы|проекты|нет проектов|"
    r"коллеги|наймите своего|создать нового|новое\s|поиск|модель|завершено|\d+ с$)", re.I)


def clean_answer(text):
    """Убирает обвязку интерфейса из снятого текста страницы."""
    out, skip = [], False
    for line in text.splitlines():
        t = line.strip()
        if not t:
            continue
        if CHROME_RE.match(t):
            continue
        out.append(t)
    return "\n".join(out)


ENGINES = {
    "alice": {"url": "https://alice.yandex.ru/", "input_hint": "Спросите о чём угодно"},
    "perplexity": {"url": "https://www.perplexity.ai/", "input_hint": "Задайте любой вопрос"},
    "zai": {"url": "https://chat.z.ai/", "input_hint": "Send a Message"},
    "deepseek": {"url": "https://chat.deepseek.com/", "input_hint": "Сообщение для DeepSeek",
                 "enable": ["Умный поиск"]},
    "qwen": {"url": "https://chat.qwen.ai/", "input_hint": "Спросить Qwen"},
}

SESSION = ""


class SessionDead(RuntimeError):
    pass


def ab(*args, timeout=120):
    cmd = ["agent-browser"]
    if SESSION:
        cmd += ["--session", SESSION]
    p = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip()


def js(expr, timeout=60):
    out = ab("eval", expr, timeout=timeout)
    for _ in range(3):
        if not isinstance(out, str):
            return out
        try:
            out = json.loads(out)
        except Exception:
            return None
    return out


def wait_stable(min_len=350, stable_polls=4, poll=3, max_wait=150):
    """Ждёт, пока текст страницы перестанет расти (ответ дописан)."""
    prev, same, waited = -1, 0, 0
    while waited < max_wait:
        time.sleep(poll)
        waited += poll
        info = js("(() => { const m=document.querySelector('main')||document.body;"
                  " const t=(m.innerText||'');"
                  " const busy=/Думаю|Ищу |Отвечаю в режиме|Ответь быстрее|Генерирую|Thinking|Searching|Generating|Thought Process|Searches|Using tools/.test(t);"
                  " return JSON.stringify({len:t.length, busy}); })()") or {}
        cur = info.get("len", 0)
        if info.get("busy"):
            prev, same = -1, 0
            continue
        if cur == prev and cur >= min_len:
            same += 1
            if same >= stable_polls:
                return True, waited
        else:
            same = 0
        prev = cur
    return False, waited


SELECTORS = {
    "alice": '[class*="AliceChat-Column"]',
    "deepseek": '.ds-virtual-list-items',
}


def extract(engine=""):
    sel = SELECTORS.get(engine, "")
    expr = """(() => {
      const sel = %s;
      const m = (sel && document.querySelector(sel)) || document.querySelector('main') || document.body;
      const text = (m.innerText || '').replace(/\\u00a0/g, ' ');
      const links = [...document.querySelectorAll('main a[href^=http], a[href^=http]')]
        .map(a => a.href).filter(h => !/yandex\\.(ru|com)|perplexity\\.ai|google\\./.test(h));
      return JSON.stringify({text, links: [...new Set(links)].slice(0, 40),
                             title: document.title, url: location.href});
    })()""" % (repr(sel))
    return js(expr)


def ask(engine, prompt):
    """Один ответ: новый чат → ввод → ожидание → извлечение."""
    cfg = ENGINES[engine]
    ab("open", cfg["url"], timeout=120)
    time.sleep(3)
    ref = None
    for _ in range(4):
        snap = ab("snapshot", "-i", timeout=90)
        for line in snap.splitlines():
            if "textbox" in line and cfg["input_hint"].split()[0].lower() in line.lower():
                m = re.search(r"\[ref=(e\d+)\]", line)
                if m:
                    ref = m.group(1)
                    break
        if ref:
            break
        m = re.search(r"textbox[^\n]*\[ref=(e\d+)\]", snap)
        if m:
            ref = m.group(1)
            break
        # закрыть онбординг/модалку, если она перекрывает поле ввода
        m = re.search(r'button "(Close|Закрыть|Понятно|Skip|Пропустить)"[^\n]*\[ref=(e\d+)\]', snap, re.I)
        if m:
            ab("click", "@" + m.group(2), timeout=60)
            time.sleep(2)
            continue
        time.sleep(2)
    if ref is None:
        raise RuntimeError("не нашёл поле ввода")
    for name in cfg.get("enable", []):
        snap2 = ab("snapshot", "-i", timeout=90)
        m2 = re.search(r'"' + re.escape(name) + r'"[^\n]*\[ref=(e\d+)\]', snap2)
        if m2:
            ab("click", "@" + m2.group(1), timeout=60)
            time.sleep(1)
    ab("fill", f"@{ref}", "--", prompt, timeout=90)
    ab("press", "Enter", timeout=60)
    ok, waited = wait_stable()
    data = extract(engine) or {}
    dead = re.search(r"Login with Apple|Log in with Google|Войти с помощью Google|"
                     r"sign_in|Please complete security verification|Забыли пароль", data.get("text", ""), re.I)
    if dead or (len(data.get("text", "")) < 300 and not ok):
        raise SessionDead("движок отдал страницу входа/проверки — сессия истекла")
    return {
        "ok": ok, "waited": waited, "text": data.get("text", ""),
        "links": data.get("links", []), "url": data.get("url", ""), "title": data.get("title", ""),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--engine", required=True, choices=sorted(ENGINES))
    ap.add_argument("--prompts", default="", help="id через запятую; пусто = все")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="перезаписать существующие ответы")
    ap.add_argument("--session", default="", help="сессия agent-browser (свой браузер)")
    a = ap.parse_args()
    global SESSION
    SESSION = a.session

    brand_dir = ROOT / a.brand
    raw_dir = brand_dir / "measurements" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader((brand_dir / "prompts.csv").open(encoding="utf-8")))
    want = {p.strip() for p in a.prompts.split(",") if p.strip()}
    if want:
        rows = [r for r in rows if r["id"] in want]
    if a.limit:
        rows = rows[: a.limit]

    done = skipped = failed = 0
    for row in rows:
        for rep in range(1, a.repeats + 1):
            path = raw_dir / f"{DATE}-{a.engine}-{row['id']}-{rep}.md"
            if path.exists() and not a.force:
                skipped += 1
                continue
            try:
                res = ask(a.engine, row["text"])
            except SessionDead as e:
                print(f"STOP на {row['id']} r{rep}: {e} — волна прервана, "
                      f"записано {done}, остальные ответы не снимались", file=sys.stderr, flush=True)
                print(f"\nитог: записано {done}, пропущено {skipped}, сбоев {failed} (прервано: сессия)")
                return 2
            except Exception as e:
                failed += 1
                print(f"FAIL {row['id']} r{rep}: {e}", file=sys.stderr)
                continue
            body = [f"---", f"engine: {a.engine}", f"prompt_id: {row['id']}",
                    f"scenario_class: {row['scenario_class']}", f"weight: {row['weight']}",
                    f"repeat: {rep}", f"date: {DATE}", f"url: {res['url']}",
                    f"captured: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
                    f"stable: {res['ok']}", "---", "", f"# {row['text']}", "",
                    "## Answer (cleaned)", "", clean_answer(res["text"]), "",
                    "## Page text (raw)", "", res["text"].strip(), "", "## Sources", ""]
            body += [f"- {u}" for u in res["links"]] or ["- (нет ссылок в ответе)"]
            path.write_text("\n".join(body) + "\n", encoding="utf-8")
            done += 1
            print(f"ok {row['id']} r{rep} — {len(res['text'])} знаков, {len(res['links'])} ссылок, "
                  f"{res['waited']} с, stable={res['ok']}", flush=True)
    print(f"\nитог: записано {done}, пропущено {skipped}, сбоев {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
