// The bloated-modern review sheet must open on ALL of them, never on a number the
// app picked. A pre-selected budget reads as a recommendation, and the size of that
// job — hours per file — is the user's call: they are shown the work and reduce it
// from there. Pinned because a stored budget from a previous answer is exactly the
// thing that would quietly creep back in as a default.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const dom=new JSDOM(fs.readFileSync(require('path').join(__dirname,'..','..','vtc','vtc_app_v3.html'),'utf8'),
 {runScripts:'dangerously',pretendToBeVisual:true,url:'http://127.0.0.1/x.html',
  beforeParse(w){w.matchMedia=(q)=>({matches:/prefers-reduced-motion/.test(String(q)),addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
   w.HTMLMediaElement.prototype.play=()=>Promise.resolve();w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};}});
const w=dom.window, out=[];
const ck=(n,g,e)=>out.push({ok:JSON.stringify(g)===JSON.stringify(e),n,g,e});
setTimeout(async ()=>{
  const d=w.document;
  w.eval("ADV.reencodeModern=true; ADV.modernMax=25;");   // a stale budget from last time
  await w.openModern(true);
  ck('sheet is open', !d.querySelector('#modern-sheet').hidden, true);
  ck('opens on ALL, not the stored 25', w.eval('MR.sel'), 0);
  const on=[...d.querySelectorAll('#modern-sheet .mr-pick')].filter(b=>b.classList.contains('on'))
            .map(b=>b.textContent.trim());
  ck('exactly one preset lit, and it is All', on.length===1 && /^All/.test(on[0]||''), true);
  ck('…and it says All <n>', on[0], 'All ' + w.eval('MR.data.files'));
  process.stdout.write(JSON.stringify(out)); process.exit(0);
}, 1200);
