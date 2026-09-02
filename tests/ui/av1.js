// Ad-hoc driver (scratchpad, NOT part of the repo's test suite): does the real
// HTML behave the way the three deliverables claim?
const fs=require('fs'); const path=require('path');
const {JSDOM}=require(path.join('/Users/simondavis/projects/very_thoughtful_compression/tests/ui','node_modules','jsdom'));
const html=fs.readFileSync('/Users/simondavis/projects/very_thoughtful_compression/vtc/vtc_app_v3.html','utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',pretendToBeVisual:true,url:'http://127.0.0.1/x.html',
  beforeParse(w){w.matchMedia=(q)=>({matches:/prefers-reduced-motion/.test(String(q)),addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
   w.HTMLMediaElement.prototype.play=()=>Promise.resolve();w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};}});
const w=dom.window, out=[];
const ck=(n,g,e)=>out.push({ok:JSON.stringify(g)===JSON.stringify(e),n,g,e});
const click=el=>el.dispatchEvent(new w.MouseEvent('click',{bubbles:true}));

setTimeout(()=>{
  const d=w.document, $=s=>d.querySelector(s);
  const M=w.eval('M'), answers=w.eval('answers');
  const CI=M.findIndex(q=>q.id==='codec'), EI=M.findIndex(q=>q.id==='encoder');
  const codec=M[CI], enc=M[EI];

  // ── 1 · standalone mock: AV1 is a real, selectable option ──
  ck('AV1 is not disabled', !!codec.opts[2].disabled, false);
  ck('AV1 tag is honest', codec.opts[2].tag, 'Most efficient · least compatible');
  ck('AV1 best on Space, worst on Compat', [codec.opts[2].m[0], codec.opts[2].m[1]], [9,3]);
  ck('AV1 blurb names the decode hardware', /M3 Mac/.test(codec.opts[2].t), true);
  ck('...and says it runs in software here', /runs in software/.test(codec.opts[2].t), true);
  ck('VVC is still disabled', !!codec.opts[3].disabled, true);

  // ── 2 · the encoder question follows the CHOSEN codec ──
  w.eval('pickFolder(-1)');
  w.eval(`step=${CI}; armed=1;`); w.render();      // H.265
  click($('#commit'));
  ck('H.265: hardware is offered', !!enc.opts[0].disabled, false);
  ck('...named after the real encoder', enc.opts[0].tag, 'hevc videotoolbox · default');
  ck('...and software still says libx26x', enc.opts[1].tag, 'libx265 / libx264');
  // now AV1
  w.eval(`step=${CI}; armed=2;`); w.render();
  click($('#commit'));
  ck('AV1 is committed', answers.codec, 2);
  ck('AV1: hardware is NOT offered', !!enc.opts[0].disabled, true);
  ck('...and says why, without reading as a fault', /no Mac has an AV1 encoder/.test(enc.sub), true);
  ck('...software names SVT-AV1', enc.opts[1].tag, 'SVT-AV1');
  ck('...and describes VBR, not capped CRF', /VBR/.test(enc.opts[1].t), true);
  // a saved "hardware" answer cannot survive a codec with no hardware
  answers.encoder = 0; w.eval('syncForMachine()');
  ck('a stale Hardware answer is corrected', answers.encoder, 1);

  // ── 3 · the preview caveat ──
  w.render();
  ck('preview note shows for AV1', $('#pv-av1').hidden, false);

  // ── 4 · the sheet ──
  setTimeout(()=>{
    ck('committing AV1 opens the sheet', $('#av1-sheet').classList.contains('on'), true);
    ck('the sheet says what it costs to make', /software/.test($('#av1-speed').textContent), true);
    click($('#av1-back'));
    ck('"Use H.265 instead" changes the answer', answers.codec, 1);
    ck('...closes the sheet', $('#av1-sheet').classList.contains('on'), false);
    ck('...restores the hardware option', !!enc.opts[0].disabled, false);
    ck('...and hides the preview note', $('#pv-av1').hidden, true);

    // re-commit AV1, then Continue
    w.eval(`step=${CI}; armed=2;`); w.render(); click($('#commit'));
    setTimeout(()=>{
      ck('re-choosing AV1 asks again', $('#av1-sheet').classList.contains('on'), true);
      click($('#av1-go'));
      ck('Continue keeps AV1', answers.codec, 2);
      // committing the SAME answer again must not nag
      w.eval(`step=${CI}; armed=2;`); w.render(); click($('#commit'));
      setTimeout(()=>{
        ck('re-committing the same answer does not nag', $('#av1-sheet').classList.contains('on'), false);

        // ── 5 · a machine with hardware AV1 (PC) ──
        w.__vtcHW = {h264:'h264_nvenc', h265:'hevc_nvenc', av1:'av1_nvenc', av1_software:true, available:true};
        ck('hardware AV1 is named in the tag', codec.opts[2].tag, 'Most efficient · hardware AV1 found');
        ck('...and the encoder question offers it', enc.opts[0].tag, 'av1 nvenc · default');
        ck('...the sheet drops the "far slower" line', /hardware AV1 encoder \(av1 nvenc\)/.test(w.eval('av1SpeedLine()')), true);

        // ── 6 · a machine that cannot make AV1 at all ──
        w.__vtcHW = {h264:'h264_videotoolbox', h265:'hevc_videotoolbox', av1:null, av1_software:false, available:true};
        ck('AV1 impossible here → disabled', !!codec.opts[2].disabled, true);
        ck('...with the reason in the tag', codec.opts[2].tag, 'not available on this machine');
        ck('...and it cannot stay the answer', answers.codec, 1);

        // ── 7 · no H.26x hardware at all ──
        w.__vtcHW = {h264:null, h265:null, av1:null, av1_software:true, available:false};
        answers.codec = 1; w.eval('syncForMachine()');
        ck('no hardware for H.265 → hardware disabled', !!enc.opts[0].disabled, true);
        ck('...and the copy names the codec', /encodes H\.265 in hardware/.test(enc.sub), true);

        process.stdout.write(JSON.stringify(out)); process.exit(0);
      }, 700);
    }, 700);
  }, 700);
}, 1400);
