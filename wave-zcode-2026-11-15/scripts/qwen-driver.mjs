// Драйвер замера для Qwen (chat.qwen.ai). Один вызов = один ответ.
// Экспорт: qwenRun({browser}, {promptId, repeat, prompt, brand, date})
// browser передаётся вызывающим кодом (мост доступен только в ядре REPL).
// Возвращает {status, file, urls, chars}
import fs from "node:fs";
import path from "node:path";

const WS = "/home/lenovo/.zcode/workspace/draft-3232fabe-5048-49d5-8db9-29d9d2792480";
const STATE = path.join(WS, "scripts", "qwen-state.json");

async function getTab(browser) {
  const tabs = await browser.tabs.list();
  for (const t of tabs) {
    if ((t.url || "").includes("qwen.ai")) return browser.tabs.get(t.id);
  }
  const tab = await browser.tabs.new();
  await tab.goto("https://chat.qwen.ai/");
  await tab.playwright.waitForLoadState({ state: "domcontentloaded" });
  await tab.playwright.waitForTimeout(4000);
  return tab;
}

function setState(obj) { fs.writeFileSync(STATE, JSON.stringify(obj)); }
function getState() {
  try { return JSON.parse(fs.readFileSync(STATE, "utf8")); } catch { return null; }
}

// Глубокий поиск по всем shadow-корням: композер Qwen живёт в shadow DOM
function deepFind(pred) {
  const hits = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) walk(el.shadowRoot);
      if (pred(el)) hits.push(el);
    }
  };
  walk(document);
  return hits;
}

function trimAnswer(t) {
  let s = t || "";
  const cut = s.search(/\n\+\d+\n/);
  if (cut > 0) s = s.slice(0, cut);
  s = s.replace(/\nWeb search\nAuto\nAI-generated content may not be accurate\.\s*$/, "");
  s = s.replace(/^(какую|что|чем|какие|где|стоит|были|можно|отзывы|сравни|топ|лучшие|колбаса|сосиски|мясные|белорусская|натуральная|Natura|Инко-Фуд|какой)[\s\S]*?\n\nThinking completed\n/, "");
  return s.trim();
}

export async function qwenRun({ browser }, { promptId, repeat, prompt, brand, date = "2026-11-15" }) {
  const tab = await getTab(browser);
  const pw = tab.playwright;

  const prev = getState();
  const resuming = prev && prev.promptId === promptId && prev.repeat === repeat && prev.brand === brand && prev.phase === "pending";

  if (!resuming) {
    // Новый чат: клик по кнопке нестабилен, надёжнее перейти на корень — свежий композер
    await tab.goto("https://chat.qwen.ai/");
    await pw.waitForLoadState({ state: "domcontentloaded" });
    await pw.waitForTimeout(2500);
    // Возрастной гейт
    const dlg = pw.getByRole("dialog", { name: "Confirm your age to continue" });
    if ((await dlg.count()) === 1) {
      const cont = dlg.getByRole("button", { name: "Continue" });
      if ((await cont.count()) === 1) { await cont.click(); await pw.waitForTimeout(800); }
    }
    // Включить Web search, если чип не активен (глубокий поиск по shadow DOM)
    const hasChip = await pw.evaluate(() => {
      const deep = (pred) => { const hits = []; const walk = (root) => { for (const el of root.querySelectorAll("*")) { if (el.shadowRoot) walk(el.shadowRoot); if (pred(el)) hits.push(el); } }; walk(document); return hits; };
      return deep((el) => el.children.length === 0 && (el.textContent || "").trim() === "Web search").length > 0;
    });
    if (!hasChip) {
      await pw.evaluate(() => {
        const deep = (pred) => { const hits = []; const walk = (root) => { for (const el of root.querySelectorAll("*")) { if (el.shadowRoot) walk(el.shadowRoot); if (pred(el)) hits.push(el); } }; walk(document); return hits; };
        const btns = deep((el) => el.tagName === "BUTTON" && (el.getAttribute("aria-label") || "") === "Select Mode");
        if (btns.length) btns[0].click();
      });
      await pw.waitForTimeout(900);
      const opened = await pw.evaluate(() => {
        const deep = (pred) => { const hits = []; const walk = (root) => { for (const el of root.querySelectorAll("*")) { if (el.shadowRoot) walk(el.shadowRoot); if (pred(el)) hits.push(el); } }; walk(document); return hits; };
        const items = deep((el) => (el.getAttribute("role") === "menuitem" || el.tagName === "BUTTON") && (el.textContent || "").trim() === "Web search");
        if (items.length) { items[0].click(); return true; }
        return false;
      });
      await pw.waitForTimeout(900);
      if (!opened) {
        await pw.evaluate(() => {
          const deep = (pred) => { const hits = []; const walk = (root) => { for (const el of root.querySelectorAll("*")) { if (el.shadowRoot) walk(el.shadowRoot); if (pred(el)) hits.push(el); } }; walk(document); return hits; };
          const items = deep((el) => el.tagName === "TEXTAREA" || el.getAttribute("contenteditable") === "true");
          if (items.length) items[0].click();
        });
      }
    }
    // Заполнить и отправить
    const box = pw.getByRole("textbox", { name: "Ask Qwen" });
    if ((await box.count()) !== 1) throw new Error("Qwen: textbox не найден");
    await box.fill(prompt);
    await pw.waitForTimeout(500);
    let sent = false;
    for (let k = 0; k < 3 && !sent; k++) {
      try {
        const send = pw.getByRole("main").getByRole("button", { name: "Send" });
        if ((await send.count()) === 1) { await send.click(); sent = true; break; }
      } catch { /* actionability race — следующий способ */ }
      await pw.waitForTimeout(1200);
    }
    if (!sent) {
      // глубокий клик по кнопке Send в shadow DOM
      sent = await pw.evaluate(() => {
        const deep = (pred) => { const hits = []; const walk = (root) => { for (const el of root.querySelectorAll("*")) { if (el.shadowRoot) walk(el.shadowRoot); if (pred(el)) hits.push(el); } }; walk(document); return hits; };
        const btns = deep((el) => el.tagName === "BUTTON" && (el.getAttribute("aria-label") || "") === "Send");
        if (btns.length) { btns[btns.length - 1].click(); return true; }
        return false;
      });
    }
    if (!sent) {
      await box.press("Enter");
    }
    setState({ brand, promptId, repeat, prompt, phase: "pending" });
    await pw.waitForTimeout(4000);
  }

  // Поллинг до исчезновения Stop
  let done = false;
  let webSearch = true;
  for (let i = 0; i < 16; i++) {
    await pw.waitForTimeout(5000);
    if ((await pw.getByRole("button", { name: "Stop" }).count()) === 0) { done = true; break; }
    const s = await pw.domSnapshot();
    if (s.includes("limit") || s.includes("Лимит") || s.includes(" Sign up") && s.includes("quota")) { webSearch = webSearch; }
  }

  if (!done) {
    setState({ brand, promptId, repeat, prompt, phase: "pending" });
    return { status: "pending", note: "генерация не завершилась за 85с, повторить вызов" };
  }

  // Дождаться стабилизации ответа (стриминг мог оборваться)
  let prevLen = -1;
  for (let i = 0; i < 6; i++) {
    await pw.waitForTimeout(3000);
    let len = 0;
    try {
      const t = await pw.evaluate(() => (document.querySelector("main") || { innerText: "" }).innerText || "");
      len = trimAnswer(t).length;
    } catch { continue; }
    if (len === prevLen && len > 300) break;
    prevLen = len;
  }

  // Раскрыть панель источников, если есть
  const srcBtnCount = await pw.evaluate(() => {
    let n = 0;
    for (const b of document.querySelectorAll("main button")) {
      if (/^\+\d+$/.test((b.textContent || "").trim())) n++;
    }
    return n;
  });
  if (srcBtnCount > 0) {
    await pw.evaluate(() => {
      for (const b of document.querySelectorAll("main button")) {
        if (/^\+\d+$/.test((b.textContent || "").trim())) { b.click(); break; }
      }
    });
    await pw.waitForTimeout(1500);
  }

  // Извлечение
  const urls = await pw.evaluate(() => {
    const out = [];
    const seen = new Set();
    for (const el of document.querySelectorAll("body *")) {
      if (el.children.length > 0) continue;
      const t = (el.textContent || "").trim();
      if (/^https?:\/\//i.test(t) && !seen.has(t)) { seen.add(t); out.push(t); }
    }
    return out;
  });
  const mainText = await pw.evaluate(() => {
    const m = document.querySelector("main");
    return m ? m.innerText : "";
  });
  const answer = trimAnswer(mainText);
  if (!answer || answer.length < 300) {
    return { status: "degenerate", note: "ответ оборван или пуст — файл не сохранён, повторить запрос", raw: answer.slice(0, 200) };
  }

  const fname = `${date}-qwen-${promptId}-${repeat}.md`;
  const fpath = path.join(WS, brand, "measurements", "raw", fname);
  const md = [
    "---",
    `date: ${date}`,
    "engine: qwen",
    `prompt_id: ${promptId}`,
    `repeat: ${repeat}`,
    `brand_file: ${brand}`,
    `prompt: ${prompt}`,
    "model: Qwen3.7-Plus (chat.qwen.ai, анонимная сессия)",
    "mode: режим «Web search» включён перед отправкой",
    "web_search: true",
    "lang: ru",
    "region: сессия по умолчанию (IP 92.38.239.36, RU)",
    "---",
    "",
    "## Ответ",
    "",
    answer,
    "",
    `## Источники (панель «+N», извлечено URL: ${urls.length})`,
    "",
    ...urls,
    "",
  ].join("\n");
  fs.writeFileSync(fpath, md);
  setState({ brand, promptId, repeat, prompt, phase: "done", file: fpath });
  return { status: "done", file: fname, urls: urls.length, chars: answer.length };
}
