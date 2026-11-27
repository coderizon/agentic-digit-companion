// Capture one real case in DIGIT Companion and open the matching ticket in KIX.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const web = process.env.DIGIT_URL || 'http://127.0.0.1:8789';
const kix = process.env.DIGIT_KIX_URL || 'http://127.0.0.1:8791';
const dir = path.resolve(process.env.DIGIT_VIDEO_DIR || 'run/kix-video');
const viewport = { width: 1440, height: 960 };
const question = 'Nach meinem Laptopwechsel meldet RaumPlan beim Anmelden RP-74. Ich habe mich bereits neu angemeldet.';

function logins() {
  const logins = process.env.DIGIT_DEMO_LOGINS;
  const fromPipe = process.env.DIGIT_RECORD_LOGIN_STDIN === '1' ? JSON.parse(fs.readFileSync(0, 'utf8')) : null;
  if (!logins && !fromPipe) throw Error('Die zwei geschützten Demoanmeldungen fehlen.');
  return f => fromPipe ? fromPipe[f] : JSON.parse(fs.readFileSync(path.join(logins, f), 'utf8'));
}

async function webAuthentication(page, employee) {
  await page.goto(web + '/auth', {timeout: 65000});
  await page.locator('input[name=email]').fill(employee.email);
  await page.locator('input[type=password]').fill(employee.password);
  await page.locator('input[type=password]').press('Enter');
  await page.locator('[contenteditable=true]').last().waitFor({timeout: 60000});
  return await page.context().storageState();
}

async function kixAuthentication(page, agent) {
  for (let attempt=0;attempt<100;attempt++) {
    try {
      const response=await page.request.get(kix,{timeout:7000});
      if(response.ok())break;
    } catch {}
    await page.waitForTimeout(6000);
  }
  await page.goto(kix, {timeout: 100000});
  const password = page.locator('input[type=password]:visible').first();
  await password.waitFor({timeout: 90000});
  await page.locator('input:visible:not([type=password]):not([type=checkbox])').first().fill(agent.login);
  await password.fill(agent.password);
  await password.press('Enter');
  await password.waitFor({state:'hidden',timeout:140000});
  await page.waitForFunction(() => document.body && !document.querySelector('input[type=password]') && document.body.innerText.trim().length>100, null, {timeout:140000});
  const kixSession = await page.evaluate(() => ({...sessionStorage}));
  return {state:await page.context().storageState(),session:kixSession};
}

(async()=>{
  fs.mkdirSync(dir,{recursive:true});
  const browser = await chromium.launch({headless:true});
  let ctx, vid, page;
  const time = {}, result = {};
  const files=logins();
  try {
    const loginCtx=await browser.newContext({locale:'de-DE',viewport});
    const info=await webAuthentication(await loginCtx.newPage(),files('planung.credentials.json'));
    await loginCtx.close();
    ctx=await browser.newContext({storageState:info,locale:'de-DE',viewport,colorScheme:'light',recordVideo:{dir,size:viewport}});
    page=await ctx.newPage();
    vid=page.video();
    const t0=Date.now(); const mark=name=>{time[name]=(Date.now()-t0)/1000;};
    const pause=ms=>page.waitForTimeout(ms);
    const snap=key=>page.screenshot({path:path.join(dir,key+'.png')});
    const editor=page.locator('[contenteditable=true]').last();
    const say=async(text,marker,expected)=>{
      await editor.click(); mark(marker+'Start');
      await editor.pressSequentially(text,{delay:32}); await pause(1150);
      await editor.press('Enter');mark(marker+'Send');
      await page.getByText(expected).filter({visible:true}).last().waitFor({timeout:160000});
      await pause(650);mark(marker+'Answer');await snap(marker);await pause(5800);mark(marker+'End');
    };
    await page.goto(web+'/?models=digit-companion-fachbereiche');
    await editor.waitFor({timeout:50000});
    const dismiss=page.getByRole('dialog').getByRole('button',{name:/got it|verstanden|schließen|okay/i}).last();
    if(await dismiss.isVisible()) await dismiss.click();
    await pause(650);
    await say('RaumPlan zeigt RP-12. Was soll ich tun?','handbuch',/Sitzung abgelaufen/);
    await say(question,'entwurf',/Soll ich diesen Entwurf an den IT-Service senden/);
    await say('Ja, bitte melden.','freigabe',/Ticketnummer \d+/);
    result.number=(await page.getByText(/Ticketnummer \d+/).filter({visible:true}).last().innerText()).match(/Ticketnummer (\d+)/)[1];
    result.chat=page.url();
    const anchor=page.getByRole('link',{name:'Ticket in KIX öffnen'}).last();
    const target=new URL(await anchor.getAttribute('href'),web);
    if(target.origin!==new URL(kix).origin || !/^\/tickets\/\d+$/.test(target.pathname))throw Error('Unerwarteter Ticketlink');
    result.ticket=target.href;
    await anchor.hover();await pause(900);mark('linkEnd');
    fs.writeFileSync(path.join(dir,'chat.json'),JSON.stringify({time,result},null,2));
    console.log('Chat aufgenommen:',result.number);
    const kctx=await browser.newContext({viewport,locale:'de-DE'});
    const login=await kixAuthentication(await kctx.newPage(),files('kix.login.json'));
    await kctx.close();
    await ctx.addCookies(login.state.cookies);
    await ctx.addInitScript(({origin,local,session})=>{
      if(location.origin!==origin)return;
      for(const {name,value} of local)localStorage.setItem(name,value);
      for(const [name,value] of Object.entries(session))sessionStorage.setItem(name,value);
    },{origin:new URL(kix).origin,local:login.state.origins.find(o=>o.origin===new URL(kix).origin)?.localStorage||[],session:login.session});
    // A single native recording page keeps the handover visible when WebUI opens links in a new tab.
    await page.goto(target.href,{timeout:120000});
    await page.getByText(/RaumPlan.*RP-74/).filter({visible:true}).first().waitFor({timeout:160000});
    await page.getByText(new RegExp(result.number)).filter({visible:true}).first().waitFor({timeout:90000});
    await pause(900);mark('kixDetail');await snap('kix-detail');await pause(5400);
    const issue=page.getByText(/Nach meinem Laptopwechsel meldet RaumPlan/).filter({visible:true});
    if(!await issue.count()){
      const tab=page.getByText(/^(Kommunikation|Communication|Artikel|Articles)$/i).filter({visible:true}).first();
      if(await tab.isVisible())await tab.click();
      if(!await issue.count()){
        const choices=page.getByText(/RaumPlan.*RP-74/).filter({visible:true});
        if(await choices.count()>1)await choices.last().click();
      }
    }
    if(!await issue.count()){
      for(const frame of page.frames()){
        if(await frame.getByText(/Nach meinem Laptopwechsel meldet RaumPlan/).isVisible().catch(()=>false)){result.article=true;break;}
      }
    } else result.article=true;
    if(!result.article)throw Error('Der Wortlaut ist in der KIX-Ansicht noch nicht sichtbar.');
    await pause(600);mark('kixArtikel');await snap('kix-artikel');await pause(7700);mark('end');
    console.log('Ticket',result.number,'aufgenommen',result.ticket);
  } catch(err){
    if(page){
      await page.screenshot({path:path.join(dir,'diagnose.png')}).catch(()=>{});
      console.log('Diagnose:',(await page.locator('body').innerText().catch(()=>'' )).slice(-4800));
    }
    throw err;
  } finally {
    if(ctx)await ctx.close();
    if(vid){
      await vid.saveAs(path.join(dir,'ablauf.webm'));
      await vid.delete();
    }
    fs.writeFileSync(path.join(dir,'schnitt.json'),JSON.stringify({time,result},null,2));
    await browser.close();
  }
})().catch(err=>{console.error(err.message);process.exitCode=1;});
