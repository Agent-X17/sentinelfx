// Optional integration harness; install jsdom separately. Not a visual browser test.
const {JSDOM, VirtualConsole} = require('jsdom');
const assert = require('node:assert/strict');
(async()=>{
  const errors=[], vc=new VirtualConsole();
  vc.on('jsdomError',e=>errors.push(e.message));
  const dom=await JSDOM.fromURL(process.argv[2]||'http://127.0.0.1:8877/',{
    resources:'usable',runScripts:'dangerously',virtualConsole:vc,
    beforeParse(w){w.fetch=(url,options)=>fetch(new URL(url,w.location.href),options);}
  });
  try {
    const w=dom.window,d=w.document;
    async function until(check){
      for(let i=0;i<100;i++){if(check())return;await new Promise(r=>setTimeout(r,50));}
      throw Error('Timed out waiting for dashboard state');
    }
    await until(()=>d.querySelectorAll('#nav a').length===8);
    for(const link of [...d.querySelectorAll('#nav a')]){
      link.click();
      await until(()=>d.querySelector('#nav a[aria-current="page"]')?.getAttribute('href')===link.getAttribute('href'));
      assert(d.querySelector('h1'));
      assert(d.querySelector('#main').textContent.includes('Connected broker decisions are blocked'));
      console.log('PASS navigation',link.textContent.trim());
    }
    w.location.hash='#dashboard';
    await until(()=>d.querySelector('button[data-scenario="safe"]'));
    d.querySelector('button[data-scenario="safe"]').click();
    await until(()=>d.querySelector('button[data-action="close"]'));
    assert(d.querySelector('#main').textContent.includes('APPROVED_SIMULATED_TRADE'));
    d.querySelector('button[data-action="close"][data-outcome="stop"]').click();
    await until(()=>!d.querySelector('button[data-action="close"]'));
    assert.deepEqual(errors,[]);
    console.log('PASS simulated open/close; no DOM script errors');
  } finally { dom.window.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
