// Records the real Open WebUI. Authentication takes place before recording.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = process.env.DIGIT_URL || 'http://127.0.0.1:8789';
const model = 'digit-companion-fachbereiche';
const home = base + '/?models=' + model;
const out = path.resolve(process.env.DIGIT_VIDEO_DIR || 'run/fachbereiche-video');
const passdir = process.env.DIGIT_DEMO_LOGINS;
const scenes = {
  quellen: ['planung', 'Welche Wissensquellen kann ich hier nutzen?', /Für dein Konto sind/],
  plan: ['planung', 'Was zeigt die rote Linie im Lageplan Kleineschholz und wie groß ist der Umgriff?', /Amtlichen Lageplan öffnen/],
  auftrag: ['planung', 'Erstelle einen privaten Prüfvermerk für die Lageprüfung im Kleineschholz. Die Flurstücksnummer fehlt.', /Über MCP ausgeführt/],
  kaemmerei: ['finanzen', 'Welche Prüfschwelle nennt unser interner Finanzvermerk für die externe Vermessung?', /fiktiver Übungsfall/],
  sperre: ['planung', 'Öffne den internen Finanzvermerk zur Vermessung.', /keine Lesefreigabe/],
  bibliothek_planung: ['planung', '', /Stadtplanung · Interne Prüfunterlagen/],
  bibliothek_finanzen: ['finanzen', '', /Kämmerei · Interne Finanzvermerke/],
  notiz: ['planung', '', /Anliegen: Lageprüfung/],
  nachweis: ['planung', '', /Über MCP ausgeführt/],
};

async function auth(browser, role) {
  const ctx = await browser.newContext({ locale: 'de-DE' });
  try {
    const page = await ctx.newPage();
    await page.goto(home);
    const login = JSON.parse(fs.readFileSync(path.join(passdir, role + '.credentials.json'), 'utf8'));
    await page.locator('input[name=email]').fill(login.email);
    const password = page.locator('input[type=password]');
    await password.fill(login.password);
    await password.press('Enter');
    await page.locator('[contenteditable=true]').last().waitFor({ timeout: 40000 });
    return await ctx.storageState();
  } finally {
    await ctx.close();
  }
}

async function capture(browser, key, state) {
  const [role, question, expected] = scenes[key];
  const dir = path.join(out, key);
  fs.mkdirSync(dir, { recursive: true });
  const viewport = { width: 1440, height: 900 };
  const ctx = await browser.newContext({ storageState: state, viewport, locale: 'de-DE', colorScheme: 'light',
    recordVideo: { dir, size: viewport } });
  const page = await ctx.newPage();
  const video = page.video();
  const t0 = Date.now();
  const clock = {};
  const mark = (s) => { clock[s] = (Date.now() - t0) / 1000; };
  const snap = (s) => page.screenshot({ path: path.join(dir, s + '.png') });
  const sleep = ms => page.waitForTimeout(ms);
  const store = (v) => fs.writeFileSync(path.join(dir, 'szene.json'), JSON.stringify(v, null, 2));
  let chat = '';
  try {
    if (key === 'nachweis') {
      if (!process.env.DIGIT_CHAT_URL?.startsWith(base + '/c/')) throw Error('Link zum aufgenommenen Chat fehlt');
      chat = process.env.DIGIT_CHAT_URL;
      await page.goto(chat);
      const card = page.getByText('Von Lea · Stadtplanung', {exact: true}).filter({visible:true}).first();
      await card.waitFor();
      mark('antwort');
      await snap('antwort');
      await sleep(6500);
      mark('antwortEnde');
      await card.hover();
      await sleep(900);
      await card.click();
      await page.getByText(/Anliegen: Lageprüfung/).filter({visible:true}).first().waitFor({timeout:15000});
      await sleep(600);
      mark('dokument');
      await snap('dokument');
      await sleep(7000);
      mark('dokumentEnde');
      return;
    }
    if (key.startsWith('bibliothek_') || key === 'notiz') {
      if (key === 'notiz') {
        if (!process.env.DIGIT_NOTIZ_URL?.startsWith(base + '/notes/')) throw Error('Lokaler Link zur gespeicherten Notiz fehlt');
        await page.goto(process.env.DIGIT_NOTIZ_URL);
      } else {
        await page.goto(base + '/workspace/knowledge');
      }
      await page.getByText(expected).filter({visible:true}).first().waitFor({timeout:22000});
      await sleep(550);
      mark('antwort');
      await snap('antwort');
      console.log(key, (await page.locator('body').innerText()).slice(-1300));
      await sleep(7000);
      mark('antwortEnde');
      return;
    }
    await page.goto(home);
    const editor = page.locator('[contenteditable=true]').last();
    await editor.waitFor({ timeout: 35000 });
    await sleep(1100);
    // Close native release or onboarding dialogs if this WebUI version shows one.
    const modal = page.getByRole('dialog').last();
    if (await modal.isVisible()) {
      const close = modal.getByRole('button', { name: /okay|verstanden|schließen|got it|close/i }).first();
      if (await close.isVisible()) await close.click();
    }
    mark('bereit');
    await sleep(1350);
    await editor.click();
    await editor.pressSequentially(question, { delay: 34 });
    mark('eingabe');
    await sleep(1200);
    await snap('eingabe');
    await editor.press('Enter');
    mark('gesendet');
    await page.getByText(expected).filter({visible:true}).first().waitFor({ timeout: 325000 });
    await sleep(650);
    chat = page.url();
    mark('antwort');
    await snap('antwort');
    console.log(key + ' Antwort: ' + (await page.locator('#chat-container').innerText({timeout:1000}).catch(() => page.locator('body').innerText())).slice(-1700));
    await sleep(7500);
    mark('antwortEnde');
    if (key === 'plan' || key === 'auftrag') {
      const anchor = key === 'plan' ? page.getByRole('link', { name: 'Planbild ansehen' }).last()
                                    : page.getByText('Von Lea · Stadtplanung', {exact: true}).filter({visible:true}).first();
      await anchor.hover();
      await sleep(550);
      if (key === 'plan') {
        const dest = new URL(await anchor.getAttribute('href'), base);
        if (dest.origin !== new URL(base).origin) throw Error('Unerwartetes Linkziel');
        await page.goto(dest.href);
        await page.locator('img').first().waitFor();
        await page.waitForFunction(() => [...document.images].some(im => im.naturalWidth > 400));
      } else {
        await anchor.click();
        await page.getByText(/Anliegen: Lageprüfung/).last().waitFor({ timeout: 18000 });
      }
      await sleep(550);
      mark('dokument');
      await snap('dokument');
      await sleep(7500);
      mark('dokumentEnde');
    }
  } catch (e) {
    await snap('diagnose').catch(() => {});
    console.error(key + ' UI: ' + (await page.locator('body').innerText().catch(() => '')).slice(-1500));
    throw e;
  } finally {
    await ctx.close();
    await video.saveAs(path.join(dir, 'openwebui.webm'));
    const raw = await video.path();
    if (path.resolve(raw) !== path.join(dir, 'openwebui.webm')) await video.delete();
    store({ role, question, chat, clock });
  }
}

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_EXE ? {executablePath:process.env.CHROME_EXE} : {})});
  try {
    const chosen = process.argv.slice(2);
    const states = {};
    for (const key of chosen) {
      const role = scenes[key][0];
      if (!states[role]) states[role] = await auth(browser, role);
      await capture(browser, key, states[role]);
    }
  } finally {
    await browser.close();
  }
})().catch(err => { console.error(err.message); process.exitCode = 1; });
