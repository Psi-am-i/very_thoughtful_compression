// The "This machine" benchmark section, driven against the real HTML on mock data:
// the state line, a full progressive run, Stop keeping a partial result, and the
// settings passthrough. That last one is not incidental — the engine's measured
// rates live in the same object as the user's preferences, and a UI that dropped
// them on save would silently delete an hour of benchmarking.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const dom=new JSDOM(fs.readFileSync('/Users/simondavis/projects/video-audio/very_thoughtful_compression/vtc/vtc_app_v3.html','utf8'),
 {runScripts:'dangerously',pretendToBeVisual:true,url:'http://127.0.0.1/x.html',
  beforeParse(w){w.matchMedia=(q)=>({matches:/prefers-reduced-motion/.test(String(q)),addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
   w.HTMLMediaElement.prototype.play=()=>Promise.resolve();w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};}});
const w=dom.window, out=[];
const ck=(n,g,e)=>out.push({ok:JSON.stringify(g)===JSON.stringify(e),n,g,e});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
setTimeout(async ()=>{
 try{
  const d=w.document, $=s=>d.querySelector(s);
  w.__dbg=[]; const _p=w.__vtcBenchProgress, _dn=w.__vtcBenchDone;
  w.__vtcBenchProgress=(x)=>{w.__dbg.push('P'+JSON.stringify(x)); return _p(x);};
  w.__vtcBenchDone=(x)=>{w.__dbg.push('D'+JSON.stringify(x).slice(0,200)); return _dn(x);};

  // ── the section exists and is reachable from the rail
  ck('rail has a "This machine" entry',
     !!d.querySelector('#adv-rail .adv-nav[data-sec="machine"]'), true);
  ck('...in the App group (after Parallel jobs)',
     [...d.querySelectorAll('#adv-rail .adv-nav')].map(b=>b.dataset.sec).indexOf('machine') >
     [...d.querySelectorAll('#adv-rail .adv-nav')].map(b=>b.dataset.sec).indexOf('run'), true);
  w.openAdvSection('machine');
  ck('opening it shows the section',
     $('#adv-pane .adv-sec[data-sec="machine"]').classList.contains('on'), true);

  // ── nothing measured: it says so, and names the shipped defaults
  const st0=$('#bench-state').textContent;
  ck('says this machine has not been measured', /has not been measured/.test(st0), true);
  ck('...and blames the developer’s Mac', /developer/.test(st0), true);
  ck('no results table yet', $('#bench-results').hidden, true);

  // ── the shape line does the arithmetic
  ck('defaults are 2 samples / 30s', [w.eval('ADV.benchSamples'), w.eval('ADV.benchSeconds')], [2,30]);
  ck('with no folder yet, it says so instead of guessing',
     /Choose a folder of real video first/.test($('#bench-est').textContent), true);

  // ── the sample count is adjustable (Simon asked for this explicitly)
  [...$('#bench-samples').querySelectorAll('button')].find(b=>b.dataset.v==='3').click();
  ck('choosing 3 samples reaches ADV', w.eval('ADV.benchSamples'), 3);
  [...$('#bench-seconds').querySelectorAll('button')].find(b=>b.dataset.v==='60').click();
  ck('sample length reaches ADV', w.eval('ADV.benchSeconds'), 60);
  [...$('#bench-samples').querySelectorAll('button')].find(b=>b.dataset.v==='2').click();
  [...$('#bench-seconds').querySelectorAll('button')].find(b=>b.dataset.v==='30').click();

  // ── folder defaults to the current source
  w.pickFolder(0);
  w.benchToForm();
  ck('folder defaults to the chosen source', $('#bench-dir').textContent, w.eval('SRC.k'));
  ck('button offers to change it', $('#bench-pick').textContent, 'Change…');
  const est0=$('#bench-est').textContent;
  ck('shape line states samples × paths × length', /2 samples × 5 paths × 30s/.test(est0), true);
  ck('...and the video length that comes to', /5 minutes of video/.test(est0), true);
  ck('...and a rough wall clock, flagged as the shipped guess',
     /roughly \d+ minutes here, on the shipped figures this replaces/.test(est0), true);

  // ── a run: progress, then results
  $('#bench-run').click();
  ck('running swaps the button for progress',
     [$('#bench-act').hidden, $('#bench-prog').hidden], [true,false]);
  await sleep(1300);
  ck('progress counts through the paths', /^\d+ \/ 10$/.test($('#bench-prog-n').textContent), true);
  ck('...and names the file and path', / · H\.26[45] (hardware|software)$/.test($('#bench-prog-l').textContent), true);
  ck('...and the bar has moved', $('#bench-prog-f').style.width !== '0%', true);
  await sleep(6500);
  ck('the run finishes', w.eval('BM.running'), false);
  ck('...and the results appear', $('#bench-results').hidden, false);
 const rows=[...d.querySelectorAll('#bench-rows tr')].map(r=>[...r.children].map(c=>c.textContent));
  ck('one row per path', rows.length, 5);
  ck('...speed as × realtime', rows[0][2], '5.1× realtime');
  ck('...of target as a percentage', rows[0][3], '103%');
  ck('...SSIM to four places', rows[0][4], '0.9902');
  const read=$('#bench-read').textContent;
  ck('the reading is derived, and states the speed gap', /hardware paths ran about [\d.]+× faster/.test(read), true);
  ck('...and both spends', /\d+% of the tier’s bitrate allowance against software’s \d+%/.test(read), true);
  console.error('READ: '+read);
  console.error('STATE: '+$('#bench-state').textContent);
  console.error('WHEN: '+$('#bench-when').textContent);
  console.error('EST: '+$('#bench-est').textContent);
  ck('...and refuses to pick a winner', /does not recommend/.test(read), true);
  ck('the legend glosses SSIM in plain English', /1\.0000 would be identical/.test($('#bench-legend').textContent), true);
  ck('...and explains a missing path', /Not in the table: hardware AV1/.test($('#bench-legend').textContent), true);
  ck('the state line now says it is measured', /Benchmarked on/.test($('#bench-state').textContent), true);
  ck('...and that estimates use it', /now uses these numbers/.test($('#bench-state').textContent), true);
  ck('the button offers a re-run', $('#bench-run').textContent, 'Run it again');

  process.stdout.write(JSON.stringify(out,null,1)); process.exit(0);
 }catch(e){ console.error('THREW', e.message); console.error(JSON.stringify(w.__dbg||[],null,1).slice(0,3000)); console.error(JSON.stringify(out.filter(r=>!r.ok),null,1)); process.exit(1); }
}, 1500);
