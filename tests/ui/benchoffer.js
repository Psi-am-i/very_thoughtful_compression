// The first-run benchmark OFFER: shown once when nothing has measured this
// machine, never when a real run or an existing benchmark already has, queued
// behind the other up-front sheets rather than stacked on them, and recorded on
// every route out so it cannot nag.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const SRC='/Users/simondavis/projects/very_thoughtful_compression/vtc/vtc_app_v3.html';
function boot(){
  const dom=new JSDOM(fs.readFileSync(SRC,'utf8'),
   {runScripts:'dangerously',pretendToBeVisual:true,url:'http://127.0.0.1/x.html',
    beforeParse(w){w.matchMedia=(q)=>({matches:/prefers-reduced-motion/.test(String(q)),addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
     w.HTMLMediaElement.prototype.play=()=>Promise.resolve();w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};}});
  return dom.window;
}
const out=[]; const ck=(n,g,e)=>out.push({ok:JSON.stringify(g)===JSON.stringify(e),n,g,e});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const on=(w,id)=>w.document.querySelector(id).classList.contains('on');

(async ()=>{
  // ── 1. the offer appears on the first folder, once
  let w=boot(); await sleep(1200);
  ck('no offer before a folder is chosen', on(w,'#bench-sheet'), false);
  w.pickFolder(0); await sleep(400);
  ck('choosing a folder offers the benchmark', on(w,'#bench-sheet'), true);
  const body=w.document.querySelector('#bench-offer-what').textContent;
  ck('the offer names the folder it would sample', /\/Volumes\/NAS\/Movies/.test(body), true);
  ck('...says the files are picked at random', /at random/.test(body), true);
  const how=w.document.querySelector('#bench-offer-how').textContent;
  ck('...states samples × paths × length', /2 × 5 × 30s/.test(how), true);
  ck('...and roughly how long', /roughly \d+ minutes here/.test(how), true);
  ck('...owning that the figure is itself a shipped guess', /shipped guesses this is meant to replace/.test(how), true);

  // "Not now" must be a real answer
  w.document.querySelector('#bench-offer-no').click(); await sleep(200);
  ck('"Not now" closes it', on(w,'#bench-sheet'), false);
  ck('...and records the asking', w.eval('ADV.benchAsked'), true);
  ck('...leaving the app usable', w.document.querySelector('#unit').classList.contains('on'), true);
  w.pickFolder(1); await sleep(400);
  ck('...and it never asks again', on(w,'#bench-sheet'), false);

  // ── 2. Escape is "Not now", and is recorded as such
  w=boot(); await sleep(1200);
  w.pickFolder(0); await sleep(400);
  ck('offered again on a fresh launch', on(w,'#bench-sheet'), true);
  w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'})); await sleep(200);
  ck('Escape dismisses it', on(w,'#bench-sheet'), false);
  ck('...and is recorded, so it cannot come back', w.eval('ADV.benchAsked'), true);

  // ── 3. Yes starts a run, in the section that owns it
  w=boot(); await sleep(1200);
  w.pickFolder(0); await sleep(400);
  w.document.querySelector('#bench-offer-yes').click(); await sleep(250);
  ck('"Measure this machine" closes the offer', on(w,'#bench-sheet'), false);
  ck('...opens Settings', on(w,'#adv-sheet'), true);
  ck('...at This machine',
     w.document.querySelector('#adv-pane .adv-sec[data-sec="machine"]').classList.contains('on'), true);
  ck('...and starts measuring', w.eval('BM.running'), true);

  // Stop keeps whatever finished, exactly as the engine does
  await sleep(1500);
  w.document.querySelector('#bench-stop').click(); await sleep(900);
  ck('Stop ends the run', w.eval('BM.running'), false);
  const kept=w.eval('(BM.state.meta.rows||[]).length');
  ck('...and keeps the paths that finished', kept>0 && kept<5, true);
  ck('...which is fewer than a whole benchmark', kept<5, true);

  // ── 4. the offer waits behind another sheet rather than stacking
  w=boot(); await sleep(1200);
  w.eval("openSheet('#compat-sheet')");
  w.pickFolder(0); await sleep(400);
  ck('it does not stack on the non-MP4 question', on(w,'#bench-sheet'), false);
  ck('...but it is queued', w.eval('!!window.__benchQueued'), true);
  w.document.querySelector('#compat-go').click(); await sleep(700);
  ck('...and takes its turn when that closes', on(w,'#bench-sheet'), true);

  // ── 5. nothing to offer when the machine is already measured
  w=boot(); await sleep(1200);
  w.eval("BM_MOCK={done:true,source:'your benchmark',meta:{at:'2026-09-03T10:00:00',folder:'/x',samples:['a.mkv'],seconds:30,skipped:[],rows:benchMockRows(benchPaths())}};");
  w.pickFolder(0); await sleep(500);
  ck('a measured machine is never offered a benchmark', on(w,'#bench-sheet'), false);

  // ── 6. a settings change must not eat the engine's own measurements
  w=boot(); await sleep(1200);
  const saved={ floor:1800, encodeRates:{'hw|h265|j1':4.2e7}, benchRates:{'sw|av1|j1':1.1e7},
                benchmark:{at:'2026-09-03T10:00:00', rows:[{codec:'h265'}]} };
  w.__vtcApplySavedAdv(saved);
  ck('a known setting is restored', w.eval('ADV.floor'), 1800);
  const payload=w.eval('JSON.stringify(advPayload())');
  const p=JSON.parse(payload);
  ck('the run-measured rates survive a push', p.encodeRates, saved.encodeRates);
  ck('...as do the benchmark rates', p.benchRates, saved.benchRates);
  ck('...and the benchmark itself', p.benchmark, saved.benchmark);
  ck('...alongside the edited setting', p.floor, 1800);

  process.stdout.write(JSON.stringify(out,null,1)); process.exit(0);
})();
