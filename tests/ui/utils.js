// Utilities mode driven against the real HTML, on the mock engine — which now
// speaks the same four callbacks the Python bridge does, so what this proves is
// the behaviour the real engine gets too.
//
// The checks that matter most here are the ones about NOT doing something:
//   ⚠️ a file whose sample index has desynchronised can never be armed for a
//      remux, and when the engine refuses one the report has to say it was left
//      alone ON PURPOSE — remuxing it destroys the frames a repair can recover;
//   ⛔ a DRM file is intact and must never reach the trash flow;
//   • only a file that is genuinely past saving may.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const dom=new JSDOM(fs.readFileSync('/Users/simondavis/projects/very_thoughtful_compression/vtc/vtc_app_v3.html','utf8'),
 {runScripts:'dangerously',pretendToBeVisual:true,url:'http://127.0.0.1/x.html',
  beforeParse(w){w.matchMedia=(q)=>({matches:/prefers-reduced-motion/.test(String(q)),addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
   w.HTMLMediaElement.prototype.play=()=>Promise.resolve();w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};}});
const w=dom.window, out=[];
const ck=(n,g,e)=>out.push({ok:JSON.stringify(g)===JSON.stringify(e),n,g,e});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
setTimeout(async ()=>{
 try{
  const d=w.document, $=s=>d.querySelector(s);
  const rows=()=>[...d.querySelectorAll('#u-list .u-row')];
  const acts=r=>[...r.querySelectorAll('.u-act')].map(b=>b.textContent);

  // ── the mode, and the folder question that comes before either tool
  w.setMode('utils');
  ck('Utilities is its own view', $('#unit').classList.contains('utils'), true);
  ck('the landing asks which folder', !!$('#u-src'), true);
  ck('...and says none is chosen yet', $('#u-src-v').textContent, 'No folder chosen yet');
  $('#u-src-pick').click();
  await sleep(60);
  ck('choosing one fills it in', $('#u-src-v').textContent, '/Volumes/RAID/TV');
  ck('...and the button now offers a change', $('#u-src-pick').textContent, 'Change…');

  // ── the faststart scan: progressive, counted, stoppable
  d.querySelector('#u-land .u-tool[data-tool="faststart"]').click();
  await sleep(500);
  ck('a scan is running', w.eval('U.scanning'), true);
  ck('...and it counts', /Scanned \d+ of \d+/.test($('#u-list .u-scan-n').textContent), true);
  ck('...names the file it is on', $('#u-list .u-scan-c').textContent.length>0, true);
  ck('...and the footer button stops it', $('#u-run').textContent, 'Stop');
  ck('...saying a scan changes nothing', /A scan changes nothing/.test($('#u-foot-n').textContent), true);
  await sleep(2600);
  ck('the scan lands', w.eval('U.scanning'), false);
  ck('...with a row per file', rows().length, w.eval('U.rows.length'));
  ck('...and the header names the counts and the folder',
     /^\d+ files scanned · \d+ needs? attention · …\/RAID\/TV$/
       .test($('#u-res-sub').textContent), true);

  // ⚠️ the remux guard — the one thing in this interface that must not be missed
  const guard = rows().find(r=>r.classList.contains('guard'));
  ck('the desynchronised file is flagged, not queued', !!guard, true);
  ck('...chipped as an index desync', guard.querySelector('.u-chip').textContent, 'index desync');
  ck('...and it is NOT offered a remux', acts(guard).indexOf('Remux'), -1);
  ck('...it is sent to File-health instead', acts(guard), ['Fix in File-health']);
  ck('...nothing arms it', w.eval('U.rows.find(r=>r.kind==="nal").action')||null, null);
  const gtext = guard.querySelector('.u-row-w').textContent;
  ck('...and the row says why, in the row', /would copy the wrong bytes out/.test(gtext), true);
  ck('...naming what it would cost', /2,400 of them on a real file/.test(gtext), true);
  ck('...and where it can be repaired', /rebuilds the index instead, losslessly/.test(gtext), true);

  // an ordinary faststart row is armed and remuxable
  const ok1 = rows().find(r=>r.classList.contains('needs') && !r.classList.contains('guard'));
  ck('an ordinary file is offered a remux', acts(ok1), ['Remux']);
  ck('...pre-armed', !!ok1.querySelector('.u-act.on'), true);
  ck('the footer counts what is armed', /to remux/.test($('#u-foot-n').textContent), true);

  // ── File-health: every fault kind the engine can return
  w.eval('uBack()');
  d.querySelector('#u-land .u-tool[data-tool="health"]').click();
  await sleep(3200);
  ck('the health scan lands', w.eval('U.scanning'), false);
  const byKind = k => rows().find((r,i)=>w.eval('U.rows')[i].kind===k);
  ck('a truncated download is chipped as such',
     byKind('container').querySelector('.u-chip').textContent, 'truncated');
  ck('...in the past-saving tone', byKind('container').querySelector('.u-chip').className, 'u-chip gone');
  ck('an unreadable file too', byKind('unreadable').querySelector('.u-chip').textContent, 'unreadable');
  ck('an encrypted file is named', byKind('drm').querySelector('.u-chip').textContent, 'encrypted');
  ck('a codec MP4 cannot hold is named', byKind('codec').querySelector('.u-chip').textContent, 'codec');

  // ── a row with no remedy is never armed for one
  ck('DRM is offered no action at all', acts(byKind('drm')), []);
  ck('...and says the file is not broken',
     /it plays perfectly/.test(byKind('drm').querySelector('.u-row-x').textContent), true);
  ck('a truncated file is offered no action', acts(byKind('container')), []);
  ck('...but is pointed at the report’s trash',
     /somewhere recoverable/.test(byKind('container').querySelector('.u-row-x').textContent), true);
  ck('the footer says how many are past saving',
     /past saving — the report offers them to the Trash/.test($('#u-foot-n').textContent), true);
  ck('nothing with fix "none" is armed',
     w.eval('U.rows.filter(r=>r.fix==="none"&&r.action).length'), 0);

  // ── the deep repair is opt-in, and gates the rows only it can reach
  const nal = () => rows().find((r,i)=>w.eval('U.rows')[i].kind==='nal');
  ck('a bitstream fault has no action until the deep repair is allowed', acts(nal()), []);
  ck('...and says so', /turn on “Allow the deep repair”/.test(nal().querySelector('.u-row-x').textContent), true);
  $('#u-rechk').checked=true; $('#u-rechk').onchange();
  ck('allowing it offers the repair', acts(nal()), ['Repair']);
  ck('...it is a ladder, not a re-encode, in the label',
     /rebuilds the sample index LOSSLESSLY/.test($('#u-reopt').getAttribute('title')), true);
  ck('...and the checkbox is usable in Scan mode', $('#u-rechk').disabled, false);

  // ── the decode-depth control maps to the scan parameter, and is honest about it
  ck('the scan depth is header-only by default', w.eval('U.decode'), 0);
  ck('...with a whole-file option', [...$('#u-deep').querySelectorAll('button')].map(b=>b.dataset.v),
     ['0','20','-1']);
  ck('...and states what a bounded read cannot find',
     /cannot find|only find damage inside the window it read/.test($('#u-deep').getAttribute('title')), true);

  // ── running the fix, through the shared progress + report sheets
  const chosen = w.eval('U.rows.filter(r=>r.action&&r.action!=="health").length');
  ck('something is armed to run', chosen>0, true);
  $('#u-run').click();
  await sleep(120);
  ck('the shared progress sheet opens', $('#progress-sheet').classList.contains('on'), true);
  ck('...its Stop is relabelled for a fix', $('#pg-stop').textContent, 'Stop after this file');
  ck('...and "Stop now" is out of the way, having nothing to discard',
     $('#pg-abort').style.display, 'none');
  await sleep(chosen*160+1400);
  ck('the report opens when it is done', $('#report-sheet').classList.contains('on'), true);
  ck('...and the progress sheet has gone', $('#progress-sheet').classList.contains('on'), false);
  ck('...with the abort button handed back', $('#pg-abort').style.display, '');

  // ⚠️ a refusal is reported as a refusal, not as a failure
  const R = w.eval('RUN.rows');
  const refused = R.find(r=>/left alone on purpose/.test(r.detail||''));
  ck('the refused remux is in the report', !!refused, true);
  ck('...as a warning, not an error', refused.sev, 'warn');
  ck('...saying what the remux would have destroyed',
     /would have destroyed the frames a repair can still recover/.test(refused.detail), true);
  ck('...and where to repair it', /Run File-health on this one/.test(refused.detail), true);
  ck('...and it reads "not remuxed", not "failed"', refused.d, 'not remuxed');

  // a lossless rebuild says it was lossless
  const rebuilt = R.find(r=>/index rebuilt losslessly/.test(r.detail||''));
  ck('a lossless index rebuild says so', !!rebuilt, true);
  ck('...and does not pretend to be a size change', rebuilt.d, 'index rebuilt');

  // ── ⛔ the trash flow: only files past saving, never DRM
  const trashable = R.filter(r=>r.t==='fail' && r.path && !r.noTrash);
  ck('some rows are offered to the trash', trashable.length>0, true);
  ck('...and every one of them is past saving',
     trashable.every(r=>/past saving/.test(r.d)), true);
  ck('⛔ no encrypted file is ever offered to the trash',
     R.filter(r=>/encrypted \(DRM\)/.test(r.detail||'')).every(r=>r.noTrash===true), true);
  ck('...and DRM is filed as left alone, not as a problem',
     R.find(r=>/encrypted \(DRM\)/.test(r.detail||'')).t, 'skip');
  ck('a file that merely needs a remux is not offered to the trash either',
     R.filter(r=>r.d==='needs a look').every(r=>r.noTrash===true), true);

  // ── stopping a scan keeps what it found
  w.eval('shutSheet("#report-sheet")');
  w.eval('uBack()');
  d.querySelector('#u-land .u-tool[data-tool="faststart"]').click();
  await sleep(600);
  $('#u-run').click();                      // Stop
  await sleep(400);
  ck('a stopped scan keeps what it scanned', w.eval('U.rows.length')>0, true);
  ck('...and says it stopped early', /stopped early/.test($('#u-res-sub').textContent), true);
  ck('...and is not still scanning', w.eval('U.scanning'), false);

  process.stdout.write(JSON.stringify(out,null,1)); process.exit(0);
 }catch(e){ console.error('THREW', e.message, e.stack);
   console.error(JSON.stringify(out.filter(r=>!r.ok),null,1)); process.exit(1); }
}, 1500);
