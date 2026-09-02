// FRAME SIZE: the walkthrough's answer has to reach the settings the engine and
// the tier previews read. The run itself maps answers.resize directly, but the
// preview worker only ever sees the settings dict — so committing the step must
// mirror the resolved cap into ADV.resizeHeight, and starting over must drop it
// again (or the previews would keep encoding at a frame size nobody asked for).
const fs=require('fs'); const {JSDOM}=require('jsdom');
const dom=new JSDOM(fs.readFileSync(require('path').join(__dirname,'..','..','vtc','vtc_app_v3.html'),'utf8'),
 {runScripts:'dangerously',pretendToBeVisual:true,url:'http://127.0.0.1/x.html',
  // Reduced motion, so fly() hands straight back to the commit callback instead
  // of running an animation this harness would have to wait out. The commit
  // handler under test is the same one either way.
  beforeParse(w){w.matchMedia=(q)=>({matches:/prefers-reduced-motion/.test(String(q)),addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
   w.HTMLMediaElement.prototype.play=()=>Promise.resolve();w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};}});
const w=dom.window, out=[];
const ck=(n,g,e)=>out.push({ok:JSON.stringify(g)===JSON.stringify(e),n,g,e});
setTimeout(()=>{
  const d=w.document, $=s=>d.querySelector(s);
  const M=w.eval('M'), answers=w.eval('answers');
  const RZ=M.findIndex(q=>q.id==='resize');
  ck('the walkthrough has a FRAME SIZE step', RZ>=0, true);

  // Commit an answer the way a person does: land on the step, arm an option,
  // press the button. Anything less would not prove the wiring.
  const commit=(oi)=>{
    w.eval(`step=${RZ}; armed=${oi};`); w.render();
    $('#commit').dispatchEvent(new w.MouseEvent('click',{bubbles:true}));
  };

  commit(3);                                   // "1080p"
  ck('committing 1080p records the answer', answers.resize, 3);
  ck('...and mirrors the cap for the previews', w.eval('ADV.resizeHeight'), 1080);

  commit(4);                                   // "720p"
  ck('changing the answer moves the mirror', w.eval('ADV.resizeHeight'), 720);

  commit(0);                                   // "Leave alone"
  ck('leave-alone means no cap at all', w.eval('ADV.resizeHeight'), 0);

  // Custom: the number in the box IS the cap, and it is clamped the same way the
  // engine clamps it — a hand-typed 40 must not ask for a 40-pixel-tall library.
  w.eval('ADV.resizeCustom=900;'); commit(5);
  ck('a custom height becomes the cap', w.eval('ADV.resizeHeight'), 900);
  w.eval('ADV.resizeCustom=40;'); commit(5);
  ck('...clamped to a sane minimum', w.eval('ADV.resizeHeight'), 120);
  w.eval('ADV.resizeCustom="";'); commit(5);
  ck('custom with nothing typed is no cap', w.eval('ADV.resizeHeight'), 0);

  // Start over wipes the answers; the mirror must go with them.
  w.eval('ADV.resizeCustom=""'); commit(3);
  ck('cap armed again before the reset', w.eval('ADV.resizeHeight'), 1080);
  w.eval('resetAll()');
  ck('starting over drops the cap too', w.eval('ADV.resizeHeight'), 0);
  ck('...and the answer with it', answers.resize, undefined);

  process.stdout.write(JSON.stringify(out)); process.exit(0);
}, 1200);
