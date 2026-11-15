// Драйвер замера для Z.ai (chat.z.ai, гостевой режим, GLM-5.3-Flash).
// Экспорт: zaiRun({browser}, {promptId, repeat, prompt, brand, date})
// Поиск в Z.ai включён по умолчанию (агент сам делает Searches/WebFetch).
import fs from "node:fs";
import path from "node:path";

const WS = "/home/lenovo/.zcode/workspace/draft-3232fabe-5048-49d5-8db9-29d9d2792480";
const STATE = path.join(WS, "scripts", "zai-state.json");

async function getTab(browser) {
  const tabs = await browser.tabs.list();
  for (const t of tabs) {
    if ((t.url || "").includes("z.ai")) return browser.tabs.get(t.id);
  }
  const tab = await browser.tabs.new();
  await tab.goto("https://chat.z.ai/");
  await tab.playwright.waitForLoadState({ state: "domcontentloaded" });
  await tab.playwright.waitForTimeout(5000);
  return tab;
}

function setState(obj) { fs.writeFileSync(STATE, JSON.stringify(obj)); }
function getState() {
  try { return JSON.parse(fs.readFileSync(STATE, "utf8")); } catch { return null; }
}

export async function zaiRun({ browser }, { promptId, repeat, prompt, brand, date = "2026-11-15" }) {
  try {
    return await run({ browser }, { promptId, repeat, prompt, brand, date });
  } catch (e) {
    return { status: "hung", note: String(e && e.message || e) };
  }
}

async function run({ browser }, { promptId, repeat, prompt, brand, date = "2026-11-15" }) {
  const tab = await getTab(browser);
  const pw = tab.playwright;

  const prev = getState();
  const resuming = prev && prev.promptId === promptId && prev.repeat === repeat && prev.brand === brand && prev.phase === "pending";

  if (!resuming) {
    // Свежий чат: на корень; если SPA вернул на последний чат — жмём Home
    try {
      await tab.goto("https://chat.z.ai/");
      await pw.waitForLoadState({ state: "domcontentloaded" });
    } catch {}
    await pw.waitForTimeout(3000);
    if ((await tab.url()).includes("/c/")) {
      const homeBtn = pw.getByRole("button", { name: "Home" });
      if ((await homeBtn.count()) === 1) {
        await homeBtn.click();
        await pw.waitForTimeout(2000);
      }
    }
    // Зафиксировать модель GLM-5.3-Flash (по умолчанию у аккаунта может стоять другая)
    await pw.evaluate(() => {
      const b = [...document.querySelectorAll("button")].find(x => (x.getAttribute("aria-label") || "") === "Select a model");
      if (b && !/GLM-5\.3-Flash/.test(b.textContent || "")) b.click();
    });
    await pw.waitForTimeout(900);
    await pw.evaluate(() => {
      const btns = [...document.querySelectorAll("button")].filter(b => (b.getAttribute("aria-label") || "") === "model-item" && /GLM-5\.3-Flash/.test(b.textContent || ""));
      if (btns.length) btns[0].click();
    });
    await pw.waitForTimeout(700);
    // Ввод: type с проверкой значения в поле; отправка кликом по aria-label "Send Message";
    // успех = URL /c/. Один полный ретрай.
    let sent = false;
    const urlBefore = await tab.url();
    // ВАЖНО: ровно один клик отправки. Lite-план ставит запросы в очередь («Queued N/10»),
    // ретраи создают дубликаты в очереди.
    const box = pw.getByRole("textbox", { name: "Send a Message" });
    if ((await box.count()) !== 1) throw new Error("Z.ai: textbox не найден");
    await pw.evaluate(() => {
      const ta = [...document.querySelectorAll("textarea")].find(t => t.getBoundingClientRect().width > 100);
      if (ta) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
        setter.call(ta, "");
        ta.dispatchEvent(new Event("input", { bubbles: true }));
        ta.focus();
      }
    });
    await pw.waitForTimeout(600);
    await box.type(prompt);
    await pw.waitForTimeout(1200);
    await pw.evaluate(() => {
      const b = [...document.querySelectorAll("button")].find(x => (x.getAttribute("aria-label") || "") === "Send Message");
      if (b) b.click();
    });
    // ждём до 25с: либо открылся чат (/c/ и URL сменился), либо запрос встал в очередь
    for (let i = 0; i < 5 && !sent; i++) {
      await pw.waitForTimeout(5000);
      const urlNow = await tab.url();
      const queued = await pw.evaluate(() => /Queued \d+\/\d+/.test(document.body.innerText)).catch(() => false);
      sent = (urlNow !== urlBefore && urlNow.includes("/c/")) || queued;
    }
    if (!sent) throw new Error("Z.ai: промпт не ушёл");
    setState({ brand, promptId, repeat, prompt, phase: "pending" });
    await pw.waitForTimeout(4000);
  }

  // Поллинг: ответ готов, когда ОБРЕЗАННЫЙ ответ (без блока Thought Process) ≥ 400 знаков
  // и стабилен 2 опроса подряд. Блок рассуждений может держать длину main постоянной
  // до появления ответа — по main судить нельзя.
  let done = false;
  let lastLen = -1;
  let stable = 0;
  for (let i = 0; i < 17; i++) {
    await pw.waitForTimeout(5000);
    const mainText = await pw.evaluate(() => (document.querySelector("main") || { innerText: "" }).innerText).catch(() => null);
    if (mainText === null) {
      try { await tab.close(); } catch {}
      return { status: "hung", note: "поллинг: страница не отвечает, вкладка закрыта" };
    }
    const tlen = trimAnswer(mainText, prompt).length;
    if (tlen >= 400 && tlen === lastLen) { stable++; if (stable >= 2) { done = true; break; } }
    else stable = 0;
    lastLen = tlen;
    await pw.evaluate(() => {
      for (const b of document.querySelectorAll("button")) {
        if ((b.textContent || "").trim() === "Close") { b.click(); return; }
      }
    }).catch(() => {});
  }
  if (!done) {
    setState({ brand, promptId, repeat, prompt, phase: "pending" });
    return { status: "pending", note: "генерация не завершилась за ~85с, повторить вызов" };
  }
  await pw.waitForTimeout(3000);

  // Раскрыть панель поиска (Thought Process не трогаем: его текст замусоривает ответ)
  await pw.evaluate(() => {
    for (const b of document.querySelectorAll("button")) {
      const t = (b.textContent || "").trim();
      if (/Search(es)?/i.test(t) || /Explored/.test(t)) { b.click(); return; }
    }
  }).catch(() => {});
  await pw.waitForTimeout(1500);

  const urls = await pw.evaluate(() => {
    const out = [];
    const seen = new Set();
    const isJunk = (u) => /(^|\.)(z\.ai|chat\.z\.ai)($|\/)/i.test(u) && !/blog|contact|legal/.test(u) ? false : /^(https?:\/\/)?(www\.)?(chat\.)?z\.ai(\/|$)/i.test(u);
    for (const el of document.querySelectorAll("body *")) {
      if (el.children.length > 0) continue;
      const t = (el.textContent || "").trim();
      if (/^https?:\/\//i.test(t) && !seen.has(t) && !isJunk(t)) { seen.add(t); out.push(t); }
    }
    for (const a of document.querySelectorAll("a[href]")) {
      const href = a.href || "";
      if (/^https?:\/\//i.test(href) && !seen.has(href) && !isJunk(href)) { seen.add(href); out.push(href); }
    }
    return out;
  }).catch(() => []);

  let mainText = await pw.evaluate(() => (document.querySelector("main") || { innerText: "" }).innerText || "");
  let answer = trimAnswer(mainText, prompt);
  // Если панель рассуждений раскрыта и глушит текст — сохраняем весь main дословно:
  // разметка отделяет блок «Thought Process» от ответа сама.
  let includeThought = false;
  if (answer.length < 400 && mainText.length > 800 && mainText.includes("Thought Process")) {
    answer = mainText.replace(/^(?:Search[^\n]*|WebFetch|Read[^\n]*|Explore[^\n]*|Done)\n?/gm, "").trim();
    includeThought = true;
  }
  if (!answer || answer.length < 300) {
    return { status: "degenerate", note: "ответ оборван или пуст — повторить запрос", raw: mainText.slice(0, 250) };
  }

  const fname = `${date}-zai-${promptId}-${repeat}.md`;
  const fpath = path.join(WS, brand, "measurements", "raw", fname);
  const md = [
    "---",
    `date: ${date}`,
    "engine: zai",
    `prompt_id: ${promptId}`,
    `repeat: ${repeat}`,
    `brand_file: ${brand}`,
    `prompt: ${prompt}`,
    "model: GLM-5.3-Flash (chat.z.ai)",
    `mode: веб-поиск включён агентом по умолчанию${includeThought ? "; В ФАЙЛЕ ЕСТЬ БЛОК THOUGHT PROCESS (панель была раскрыта) — для разметки брать текст после него" : ""}`,
    "web_search: true",
    "lang: ru",
    "region: сессия по умолчанию (IP 92.38.239.36, RU)",
    "---",
    "",
    "## Ответ",
    "",
    answer,
    "",
    `## Источники (панель поиска, извлечено URL: ${urls.length})`,
    "",
    ...urls,
    "",
  ].join("\n");
  fs.writeFileSync(fpath, md);
  setState({ brand, promptId, repeat, prompt, phase: "done", file: fpath });
  // вкладку закрываем: SPA деградирует при длинной серии
  try { await tab.close(); } catch {}
  return { status: "done", file: fname, urls: urls.length, chars: answer.length };
}

function trimAnswer(t, prompt) {
  let s = t || "";
  const endMarkers = ["Generated by AI. For reference only.", "Connect IM", "Unlock Your Insights?"];
  let end = s.length;
  for (const m of endMarkers) {
    const i = s.indexOf(m);
    if (i >= 0) end = Math.min(end, i);
  }
  s = s.slice(0, end);
  // отрезаем шапку: до и включая строку со словом Searches/Thought Process
  const tp = s.indexOf("Thought Process");
  const sr = s.search(/Explored[^\n]*Searches/);
  let start = 0;
  if (sr >= 0) {
    const nl = s.indexOf("\n", sr);
    start = nl >= 0 ? nl + 1 : sr;
  } else if (tp >= 0) {
    const nl = s.indexOf("\n", tp);
    start = nl >= 0 ? nl + 1 : tp;
  }
  s = s.slice(start);
  // панель поиска и шаги агента (Search/WebFetch/Done) — убираем в начале
  s = s.replace(/^(?:Search[^\n]*|WebFetch|Read[^\n]*|Explore[^\n]*|Done|Thought Process)\n?/gm, "");
  return s.trim();
}
