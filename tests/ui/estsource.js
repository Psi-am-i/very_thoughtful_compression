// Where an hours figure came from, and how honestly it says so.
//
// The app can price a night of encoding from four different sources, and they are
// not equally solid: a real run of the library, a benchmark of this machine, a
// five-second preview clip, or nothing at all. Quoting "about 46 hours" off a
// five-second clip in the same breath as a measured one would make a guess look
// like a measurement, at exactly the moment someone decides whether to commit a
// night to it. So the wording changes with the source, and the weak one says so.
const fs=require('fs'); const {JSDOM}=require('jsdom');
const dom=new JSDOM(fs.readFileSync(require('path').join(__dirname,'..','..','vtc','vtc_app_v3.html'),'utf8'),
 {runScripts:'dangerously',pretendToBeVisual:true,url:'http://127.0.0.1/x.html',
  beforeParse(w){w.matchMedia=(q)=>({matches:/prefers-reduced-motion/.test(String(q)),addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
   w.HTMLMediaElement.prototype.play=()=>Promise.resolve();w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};}});
const w=dom.window, out=[];
const ck=(n,g,e)=>out.push({ok:JSON.stringify(g)===JSON.stringify(e),n,g,e});
setTimeout(async ()=>{
  const d=w.document;
  w.eval("ADV.reencodeModern=true;");
  const say = async (src, hours) => {
    await w.openModern(true);
    w.eval(`MR.data.rate_from=${JSON.stringify(src)}; MR.data.hours=${hours}; mrLead();`);
    return d.querySelector('#modern-sheet').textContent.replace(/\s+/g,' ');
  };

  let t = await say('your last run', 46.2);
  ck('a real run is named as such', /based on your last run/.test(t), true);
  ck('...and is not hedged as approximate', /approximate/i.test(t), false);

  t = await say('your benchmark', 46.2);
  ck('a benchmark says it benchmarked THIS MACHINE',
     /based on benchmarking this machine/.test(t), true);
  ck('...and "your benchmark" is not left as a bare label',
     /based on your benchmark/.test(t), false);
  ck('...and is not hedged either', /approximate/i.test(t), false);

  t = await say('a sample encode', 46.2);
  ck('a five-second clip is called approximate', /Treat that as approximate/.test(t), true);
  ck('...says the machine has not been benchmarked',
     /has not been benchmarked/.test(t), true);
  ck('...and points at where to fix that',
     /Settings . This machine/.test(t), true);
  ck('...and does NOT dress it up as "based on" a measurement',
     /based on a sample encode/.test(t), false);

  t = await say('', null);
  ck('with nothing measured it refuses to give a number',
     /can't put a number on it yet/.test(t), true);
  ck('...and still says the shape of the job', /hours per file, not minutes/.test(t), true);

  process.stdout.write(JSON.stringify(out)); process.exit(0);
}, 1200);
