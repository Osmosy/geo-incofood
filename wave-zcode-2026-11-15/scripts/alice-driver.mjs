// Драйвер замера для Алисы (alice.yandex.ru). Один вызов = один ответ.
// Экспорт: aliceRun({browser}, {promptId, repeat, prompt, brand, date})
import fs from "node:fs";
import path from "node:path";

const WS = "/home/lenovo/.zcode/workspace/draft-3232fabe-5048-49d5-8db9-29d9d2792480";
const STATE = path.join(WS, "scripts", "alice-state.json");

async function getTab(browser) {
  const tabs = await browser.tabs.list();
  for (const t of tabs) {
    if ((t.url || "").includes("alice.yandex")) return browser.tabs.get(t.id);
  }
  const tab = await browser.tabs.new();
  await tab.goto("https://alice.yandex.ru/");
  await tab.playwright.waitForLoadState({ state: "domcontentloaded" });
  await tab.playwright.waitForTimeout(4000);
  return tab;
}

function setState(obj) { fs.writeFileSync(STATE, JSON.stringify(obj)); }
function getState() {
  try { return JSON.parse(fs.readFileSync(STATE, "utf8")); } catch { return null; }
}

function extractAnswer(bodyText, prompt) {
  const marker = "Я\n" + prompt + "\n";
  const start = bodyText.indexOf(marker);
  if (start < 0) return "";
  let s = bodyText.slice(start + marker.length);
  const endMarkers = ["Спросите о чём угодно", "Алиса старается", "Выключить звук"];
  let end = s.length;
  for (const m of endMarkers) {
    const i = s.indexOf(m);
    if (i >= 0) end = Math.min(end, i);
  }
  return s.slice(0, end).trim();
}

export async function aliceRun({ browser }, { promptId, repeat, prompt, brand, date = "2026-11-15" }) {
  try {
    return await run({ browser }, { promptId, repeat, prompt, brand, date });
  } catch (e) {
    return { status: "hung", note: "страница не отвечает: " + String(e && e.message || e) };
  }
}

async function run({ browser }, { promptId, repeat, prompt, brand, date = "2026-11-15" }) {
  const tab = await getTab(browser);
  const pw = tab.playwright;

  const prev = getState();
  const resuming = prev && prev.promptId === promptId && prev.repeat === repeat && prev.brand === brand && prev.phase === "pending";

  if (!resuming) {
    // Новый чат: под логином работает кнопка; анонимом был фолбэк на корень
    let fresh = false;
    const nc = pw.getByRole("complementary").getByRole("button", { name: "Новый чат" });
    if ((await nc.count()) === 1) {
      try { await nc.click(); await pw.waitForTimeout(1800); fresh = !(await tab.url()).includes("/chat/"); } catch {}
    }
    if (!fresh) {
      try {
        await tab.goto("https://alice.yandex.ru/");
        await pw.waitForLoadState({ state: "domcontentloaded" });
      } catch {}
      await pw.waitForTimeout(2500);
    }
    // Поле ввода: атомарно — нативный сеттер + input + клик «Отправить» одним evaluate
    // (разрыв между шагами даёт рендер-гонку: значение теряется, уходит пустая отправка)
    let sent = false;
    for (let attempt = 0; attempt < 2 && !sent; attempt++) {
      const res = await pw.evaluate((text) => {
        const deep = (pred) => { const hits = []; const walk = (root) => { for (const el of root.querySelectorAll("*")) { if (el.shadowRoot) walk(el.shadowRoot); if (pred(el)) hits.push(el); } }; walk(document); return hits; };
        const tas = deep((el) => el.tagName === "TEXTAREA" && el.getBoundingClientRect().width > 100);
        if (!tas.length) return { found: false };
        const ta = tas[tas.length - 1];
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
        setter.call(ta, text);
        ta.dispatchEvent(new Event("input", { bubbles: true }));
        const vlen = (ta.value || "").length;
        const btns = deep((el) => el.tagName === "BUTTON" && ((el.getAttribute("aria-label") || "") + " " + (el.textContent || "")).trim().includes("Отправить"));
        if (btns.length && vlen > 0) { btns[btns.length - 1].click(); return { found: true, sent: true, vlen }; }
        return { found: true, sent: false, vlen };
      }, prompt);
      if (!res.found) throw new Error("Alice: textarea не найдена");
      if (res.sent) {
        await pw.waitForTimeout(6000);
        const body = await pw.evaluate(() => document.body.innerText);
        const stopNow = await pw.getByRole("button", { name: "Алиса, стоп" }).count().catch(() => 0);
        if (body.includes("Я\n" + prompt) || body.includes("Думаю") || body.includes("Готовлю ответ") || stopNow >= 1) { sent = true; break; }
      }
      // Фолбэк: React не принял синтетический ввод — реальные клавиши по координатам поля
      const pos = await pw.evaluate(() => {
        const tas = [...document.querySelectorAll("textarea")].filter(t => t.getBoundingClientRect().width > 100);
        if (!tas.length) return null;
        const ta = tas[tas.length - 1];
        const r = ta.getBoundingClientRect();
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
      });
      if (pos) {
        await tab.cua.click({ x: pos.x, y: pos.y });
        await pw.waitForTimeout(500);
        await tab.cua.keypress({ keys: ["Control", "a"] });
        await tab.cua.keypress({ keys: ["Delete"] });
        await tab.cua.type({ text: prompt });
        await pw.waitForTimeout(900);
        const sendPos = await pw.evaluate(() => {
          const deep = (pred) => { const hits = []; const walk = (root) => { for (const el of root.querySelectorAll("*")) { if (el.shadowRoot) walk(el.shadowRoot); if (pred(el)) hits.push(el); } }; walk(document); return hits; };
          const btns = deep((el) => el.tagName === "BUTTON" && ((el.getAttribute("aria-label") || "") + " " + (el.textContent || "")).trim().includes("Отправить"));
          if (!btns.length) return null;
          const b = btns[btns.length - 1];
          const r = b.getBoundingClientRect();
          return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
        });
        if (sendPos) {
          await tab.cua.click({ x: sendPos.x, y: sendPos.y });
        } else {
          await tab.cua.keypress({ keys: ["Enter"] });
        }
        await pw.waitForTimeout(6000);
        const body = await pw.evaluate(() => document.body.innerText);
        const stopNow = await pw.getByRole("button", { name: "Алиса, стоп" }).count().catch(() => 0);
        if (body.includes("Я\n" + prompt) || body.includes("Думаю") || body.includes("Готовлю ответ") || stopNow >= 1) { sent = true; break; }
      }
      await pw.waitForTimeout(2000);
    }
    if (!sent) throw new Error("Alice: промпт не ушёл после двух попыток");
    setState({ brand, promptId, repeat, prompt, phase: "pending" });
    await pw.waitForTimeout(4000);
  }

  // Поллинг по тексту страницы: «Готовлю ответ»/«Думаю» = генерация идёт;
  // завершение = «Хороший ответ» ПОСЛЕ сообщения пользователя (текст может стримиться без индикаторов)
  let done = false;
  for (let i = 0; i < 14; i++) {
    await pw.waitForTimeout(5000);
    let body = "";
    try { body = await pw.evaluate(() => document.body.innerText); }
    catch {
      try { await tab.close(); } catch {}
      return { status: "hung", note: "поллинг: страница не отвечает, вкладка закрыта" };
    }
    const gen = body.includes("Готовлю ответ") || body.includes("Думаю");
    const marker = "Я\n" + prompt;
    const mi = body.indexOf(marker);
    const hi = body.indexOf("Хороший ответ");
    if (!gen && mi >= 0 && hi > mi) { done = true; break; }
    if (body.includes("войти в аккаунт") && !gen && mi < 0) {
      return { status: "error", note: "запрос не принят: страница вернулась на главную" };
    }
  }
  if (!done) {
    setState({ brand, promptId, repeat, prompt, phase: "pending" });
    return { status: "pending", note: "генерация не завершилась за 85с, повторить вызов" };
  }
  await pw.waitForTimeout(3000);

  // Извлечение текста и ссылок
  let bodyText, links;
  try {
    bodyText = await pw.evaluate(() => document.body.innerText);
  } catch {
    return { status: "hung", note: "извлечение: страница не отвечает" };
  }
  const answer = extractAnswer(bodyText, prompt);
  try {
    links = await pw.evaluate(() => {
      const out = [];
      const seen = new Set();
      for (const a of document.querySelectorAll("a[href]")) {
        const href = a.href || "";
        if (!/^https?:\/\//i.test(href)) continue;
        if (/yandex\.ru\/legal|passport\.yandex|support\.yandex/i.test(href)) continue;
        if (!seen.has(href)) { seen.add(href); out.push(href); }
      }
      return out;
    });
  } catch { links = []; }
  if (!answer || answer.length < 50) {
    return { status: "error", note: "пустой ответ или промпт не найден", raw: bodyText.slice(0, 400) };
  }

  const fname = `${date}-yandex-alice-${promptId}-${repeat}.md`;
  const fpath = path.join(WS, brand, "measurements", "raw", fname);
  const md = [
    "---",
    `date: ${date}`,
    "engine: yandex-alice",
    `prompt_id: ${promptId}`,
    `repeat: ${repeat}`,
    `brand_file: ${brand}`,
    `prompt: ${prompt}`,
    "model: Алиса (alice.yandex.ru, анонимная сессия, режим «Интеллект: Авто»)",
    "web_search: true",
    "lang: ru",
    "region: сессия по умолчанию (IP 92.38.239.36, RU)",
    "---",
    "",
    "## Ответ",
    "",
    answer,
    "",
    `## Источники (ссылки на странице, ${links.length})`,
    "",
    ...links,
    "",
  ].join("\n");
  fs.writeFileSync(fpath, md);
  setState({ brand, promptId, repeat, prompt, phase: "done", file: fpath });
  // закрываем вкладку: SPA деградирует после нескольких чатов в гостевой сессии
  try { await tab.close(); } catch {}
  return { status: "done", file: fname, urls: links.length, chars: answer.length };
}
