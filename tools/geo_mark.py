#!/usr/bin/env python3
"""geo_mark.py — разметка сырых ответов в measurements.csv (шаг 4 пайплайна).

Читает <brand>/measurements/raw/*.md (после geo_run.py) и <brand>/brand.json,
пишет <brand>/measurements.csv по контракту geo_score.py (14 колонок).

Правила (из agent-task.md + SKILL.md):
  * упоминание ≠ рекомендация: brand_role = none|listed|recommended|top|negative;
  * «Белорусская традиция» в общем смысле («по белорусской традиции») — не упоминание марки;
  * Natura: только мясная марка Инко-Фуд; омонимы пишутся в homonyms.md и не считаются;
  * brand_cited — только если среди источников ответа есть домен бренда;
  * competitor — конкурент с самой сильной ролью в ответе.

Скрипт размечает механически и печатает сомнительные случаи: финальную сверку
цитат делает человек (`--audit` показывает по одной строке на файл).

Usage:
  python3 tools/geo_mark.py --brand beltradition
  python3 tools/geo_mark.py --brand natura --audit 15
"""
import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROLE_RANK = {"none": 0, "negative": 1, "listed": 2, "recommended": 3, "top": 4}

NEG = re.compile(r"недостат|минус|не совету|не рекоменд|плох|хуже|разочаров|претензи|"
                 r"нарекани|не стоит брать|сомнительн|фальсификат|дешев(ый|о) по качеств", re.I)
REC = re.compile(r"рекомендую|советую|стоит (взять|выбрать|попробовать)|берите|выбирайте|"
                 r"что (брать|взять|купить)|мой выбор|мой список|shortlist|подборк|"
                 r"обратите внимание|лучший выбор|отличн(ый|о) вариант|качественн|"
                 r"заслуженно|проверенн|топ(?![а-я])|рейтинг|лидер", re.I)
TOP = re.compile(r"№\s*1|номер 1|первое место|1-е место|лидер|лучш(ий|ая|ее)|эталон|"
                 r"топ-?1(?![0-9])|сам(ый|ая) (вкусн|популярн|известн)", re.I)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«])")

GENERIC_BT = re.compile(r"белорусск\w* традици", re.I)
BRAND_CONTEXT = re.compile(r"инко-?фуд|торгов\w* марк|бренд|марка|производител|"
                           r"beltradition|бел традиция|тм\s|компани", re.I)

NATURA_OMONYM = re.compile(r"natura\s*&\s*co|natura\s*cosm|косметик|arla|via natura|"
                           r"natur[aа]\s+(сыр|косметик)|эко-?косметик", re.I)


def load_raw(path):
    txt = path.read_text(encoding="utf-8", errors="replace")
    head, body = {}, txt
    m = re.match(r"---\n(.*?)\n---\n", txt, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                head[k.strip()] = v.strip()
        body = txt[m.end():]
    ans = re.search(r"## Answer \(cleaned\)\n(.*?)\n## ", body, re.S)
    src = re.search(r"## Sources\n(.*)$", body, re.S)
    answer = (ans.group(1) if ans else body).strip()
    sources = [l.lstrip("- ").strip() for l in (src.group(1).splitlines() if src else []) if l.strip().startswith("- http")]
    return head, answer, sources


def find_alias(text, alias):
    """Все вхождения алиаса (регистр, ё/е, дефисы) с границами слова.

    Границы обязательны: иначе алиас «Натура» ловит «натуральная колбаса»
    и бренд Natura получает упоминания из прилагательного.
    """
    pat = re.escape(alias.lower()).replace(r"\ ", r"[\s\-]+").replace("ё", "[ёе]")
    pat = r"(?<![а-яёa-z0-9])" + pat + r"(?![а-яёa-z0-9])"
    return [m.start() for m in re.finditer(pat, text.lower())]


def sentence_bounds(text, idx):
    start = max(text.rfind(". ", 0, idx), text.rfind("\n", 0, idx), 0)
    end = min([x for x in (text.find(". ", idx), text.find("\n", idx)) if x != -1] or [len(text)])
    return start, end


NUM_ITEM = re.compile(r"^\s*(?:[\d]+[.)]|[-–—•*])\s*")
RANK_HEAD = re.compile(r"топ|рейтинг|лучши|подборк|что (брать|взять|купить)|производител", re.I)
HEADING = re.compile(r"^(#{1,6}\s|\*\*[^*]+\*\*\s*$|[А-ЯЁA-Z][^.!?]{2,70}$)")


def table_role(text, idx):
    """Роль упоминания внутри markdown-таблицы: колонка «Место» задаёт ранг.

    DeepSeek через API отвечает таблицами («| Место | Производитель |»), и без
    этого правила все конкуренты из рейтинга размечены как listed — SoV уезжает.
    """
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    line = text[start:end if end != -1 else len(text)]
    if not line.lstrip().startswith("|"):
        return None
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    header, rank = "", None
    cur = start
    for _ in range(12):
        cur = text.rfind("\n", 0, cur - 1)
        if cur == -1:
            break
        lstart = cur + 1
        lend = text.find("\n", lstart)
        prev = text[lstart:lend if lend != -1 else len(text)].strip()
        if not prev.startswith("|"):
            break
        pcells = [c.strip().strip("*") for c in prev.strip().strip("|").split("|")]
        if pcells and not any(c.isdigit() for c in pcells[0]):
            header = " ".join(pcells)
            break
    if cells and cells[0].isdigit():
        rank = int(cells[0])
    if rank == 1:
        return "top"
    if rank and rank <= 5:
        return "recommended"
    if rank is None and re.search(r"место|топ|рейтинг|лучши", header, re.I):
        return "recommended"
    return None


def block_heading(text, idx):
    """Заголовок блока, в котором стоит упоминание: «что брать», «топ-10», «рейтинг»."""
    lines = text[:idx].splitlines()
    for line in reversed(lines):
        t = line.strip()
        if not t:
            continue
        if HEADING.match(t):
            return t.strip("*# ").strip()
        if len(t) < 80 and not t.endswith((".", "!", "?", ":", ";")):
            return t
    return ""


def classify(text, idx, alias, heading=""):
    """Роль упоминания: контекст предложения + заголовок блока.

    Заголовок нужен для списков: ответ вида «Что брать: … от Брестского» —
    это рекомендация, хотя в самом предложении слов «советую/лучший» нет.
    """
    s, e = sentence_bounds(text, idx)
    ctx = text[s:e + 120]
    if NEG.search(ctx):
        return "negative"
    t_role = table_role(text, idx)
    if t_role:
        return t_role
    if TOP.search(ctx) or TOP.search(heading):
        return "top"
    if REC.search(ctx) or REC.search(heading):
        return "recommended"
    # перечень внутри блока-рейтинга: «1. X», «- X» под заголовком «Топ производителей»
    if NUM_ITEM.match(text[s:e]) and RANK_HEAD.search(heading):
        return "recommended"
    return "listed"


def brand_occurrences(text, brand):
    """Упоминания бренда с учётом омонимов и общего смысла."""
    hits, notes = [], []
    names = [brand["name"]] + list(brand.get("aliases", []))
    is_bt = "белорусск" in brand["name"].lower()
    is_natura = brand["name"].lower().startswith("natura")
    for alias in names:
        for idx in find_alias(text, alias):
            window = text[max(0, idx - 130): idx + 160]
            if is_bt and GENERIC_BT.search(alias) and not BRAND_CONTEXT.search(window):
                # общее употребление: рядом нет признаков марки — и это не отдельный алиас
                if alias.lower() == brand["name"].lower():
                    notes.append(("generic", text[max(0, idx - 80): idx + 120].replace("\n", " ")))
                    continue
            if is_natura and alias.lower() == brand["name"].lower() and NATURA_OMONYM.search(window):
                notes.append(("homonym", text[max(0, idx - 80): idx + 120].replace("\n", " ")))
                continue
            hits.append((idx, alias))
    hits.sort()
    return hits, notes


DOMAIN_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:by|ru|kz|com|net|org|shop|bel))\b", re.I)


def strip_prompt(text, prompt):
    """Убирает эхо промпта в начале ответа: оно содержит имя бренда и даёт ложное упоминание."""
    if not prompt:
        return text
    norm = lambda x: re.sub(r"\s+", " ", x).strip().lower().rstrip("?.!")
    p_norm = norm(prompt)
    lines = text.splitlines()
    out = []
    for line in lines:
        if norm(line) == p_norm:
            continue
        out.append(line)
    return "\n".join(out)


def mark_file(path, brand, competitors, prompts=None):
    head, text, sources = load_raw(path)
    text = strip_prompt(text, (prompts or {}).get(head.get("prompt_id", "")))
    # часть движков (DeepSeek) не отдаёт ссылки-анкоры: домены берём из текста ответа
    sources = list(sources) + [d for d in DOMAIN_RE.findall(text)]
    low = text.lower()
    occ, notes = brand_occurrences(text, brand)
    mentioned = bool(occ)

    role = "none"
    if occ:
        roles = [classify(text, i, a, block_heading(text, i)) for i, a in occ]
        role = max(roles, key=lambda r: ROLE_RANK[r])

    # позиция среди брендов (мы + конкуренты) по первому вхождению
    positions = []
    if occ:
        positions.append(("brand", occ[0][0]))
    comp_hits = []
    for c in competitors:
        idxs = []
        for a in [c["name"]] + list(c.get("aliases", [])):
            idxs += find_alias(text, a)
        if idxs:
            comp_hits.append((min(idxs), c["name"],
                              [classify(text, i, a, block_heading(text, i)) for i in sorted(idxs)]))
    positions += [("competitor", i) for i, _, _ in comp_hits]
    positions.sort(key=lambda x: x[1])
    brand_pos = next((n for n, (kind, _) in enumerate(positions, 1) if kind == "brand"), "")

    best_comp, best_role = "", "none"
    for _, name, roles in comp_hits:
        r = max(roles, key=lambda x: ROLE_RANK[x])
        if ROLE_RANK[r] > ROLE_RANK[best_role]:
            best_comp, best_role = name, r

    src_low = " ".join(sources).lower()
    brand_cited = any(d.lower() in src_low for d in brand.get("domains", []))
    competitor_cited = any(d.lower() in src_low for c in competitors for d in c.get("domains", []))

    search_markers = re.search(r"искал в интернете|прочитал \d+ |источник|поиск в интернете|"
                               r"найдено в|web search|searched", low)
    web_search = bool(sources) or bool(search_markers)

    row = {
        "date": head.get("date", ""),
        "engine": head.get("engine", ""),
        "prompt_id": head.get("prompt_id", ""),
        "scenario_class": head.get("scenario_class", ""),
        "weight": head.get("weight", ""),
        "repeat": head.get("repeat", ""),
        "web_search": str(web_search).lower(),
        "brand_mentioned": str(mentioned).lower(),
        "brand_role": role,
        "brand_pos": brand_pos,
        "competitor": best_comp,
        "competitor_role": best_role,
        "brand_cited": str(brand_cited).lower(),
        "competitor_cited": str(competitor_cited).lower(),
    }
    return row, text, sources, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--audit", type=int, default=0, help="напечатать N строк для сверки")
    a = ap.parse_args()

    bdir = ROOT / a.brand
    brief = json.loads((bdir / "brand.json").read_text(encoding="utf-8"))
    brand, competitors = brief["brand"], brief["competitors"]
    prompts = {}
    pcsv = bdir / "prompts.csv"
    if pcsv.exists():
        with pcsv.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                prompts[r["id"]] = r["text"]
    raws = sorted((bdir / "measurements" / "raw").glob("*.md"))
    if not raws:
        raise SystemExit("нет сырья: сначала geo_run.py")

    rows, homonyms = [], []
    for p in raws:
        row, text, sources, notes = mark_file(p, brand, competitors, prompts)
        if not row["engine"]:
            continue
        rows.append(row)
        if notes:
            for kind, quote in notes:
                homonyms.append((p.name, kind, quote.strip()))

    fields = ["date", "engine", "prompt_id", "scenario_class", "weight", "repeat", "web_search",
              "brand_mentioned", "brand_role", "brand_pos", "competitor", "competitor_role",
              "brand_cited", "competitor_cited"]
    out = bdir / "measurements.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        rows.sort(key=lambda r: (r["engine"], r["prompt_id"], r["repeat"]))
        w.writerows(rows)
    print(f"{out}: {len(rows)} строк")

    seen, uniq = set(), []
    for name, kind, quote in homonyms:
        key = (kind, quote[:60])
        if key in seen:
            continue
        seen.add(key)
        uniq.append((name, kind, quote))
    homonyms = uniq
    if homonyms:
        hpath = bdir / "homonyms.md"
        with hpath.open("w", encoding="utf-8") as f:
            f.write(f"# {brand['name']}: омонимы и общее употребление (не засчитаны)\n\n")
            for name, kind, quote in homonyms:
                f.write(f"- `{name}` — **{kind}**: «{quote}»\n")
        print(f"{hpath}: {len(homonyms)} случаев")
    if a.audit:
        for r in rows[: a.audit]:
            print(f"  {r['engine']:9} {r['prompt_id']:8} r{r['repeat']} "
                  f"mention={r['brand_mentioned']:5} role={r['brand_role']:11} pos={r['brand_pos']:>2} "
                  f"comp={r['competitor'][:22]:22} {r['competitor_role']:11} search={r['web_search']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
