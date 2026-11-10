const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname, 'index.html'), 'utf8');
const videoPath=require('node:path').join(__dirname,'fungisync-overview.mp4');
const source = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(x => x.includes("const PAGES"));
assert.ok(source, 'application script present');
const store = new Map();
const sessions = new Map();
function boot() {
  const nodes = new Map(), listeners = {};
  function node(selector) {
    if (!nodes.has(selector)) { const classes=new Set();nodes.set(selector, {
      id:selector.startsWith('#')?selector.slice(1):'',value: selector === '#role-select' ? 'Coordinator' : '', textContent: '', innerHTML: '',
      dataset: {}, style: {}, classList: {toggle(name,force){if(force===undefined)force=!classes.has(name);force?classes.add(name):classes.delete(name);return force},add(name){classes.add(name)},remove(name){classes.delete(name)},contains(name){return classes.has(name)}},
      addEventListener(type, fn) { this.listeners ||= {}; this.listeners[type] = fn; },
      querySelector: node, querySelectorAll: () => [],
      setAttribute() {}, focus() {}, setSelectionRange() {}, click() {},
    }); }
    return nodes.get(selector);
  }
  const document = {querySelector:node,querySelectorAll:()=>[],documentElement:{},createTreeWalker:()=>({nextNode:()=>null}),addEventListener(type,fn){listeners[type]=fn}};
  class FormDataMock{constructor(target){this.target=target}[Symbol.iterator](){return Object.entries(this.target.fields||{})[Symbol.iterator]()}}
  const context = {document,window:{},NodeFilter:{SHOW_TEXT:4},localStorage:{getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},sessionStorage:{getItem:k=>sessions.get(k)??null,setItem:(k,v)=>sessions.set(k,v),removeItem:k=>sessions.delete(k)},console,Date,Map,Set,Math,Number,String,Object,Array,JSON,FormData:FormDataMock,Blob:class{},URL:{createObjectURL(){return 'blob:'},revokeObjectURL(){}},setTimeout,clearTimeout};
  vm.runInNewContext(source,context,{timeout:3000});
  return {nodes,listeners,click(dataset){listeners.click({target:{closest:()=>({dataset})}})},content(){return node('#app').innerHTML},node};
}
let app=boot();
assert.match(app.node('#auth-root').innerHTML,/From Harvest/); assert.match(app.node('#auth-root').innerHTML,/Supply-to-demand network/); assert.match(app.node('#auth-root').innerHTML,/fungisync-hero.png/);
assert.match(app.node('#auth-root').innerHTML,/id="landing-video" autoplay loop muted playsinline preload="metadata"/);assert.match(app.node('#auth-root').innerHTML,/fungisync-overview\.mp4/);assert.match(app.node('#auth-root').innerHTML,/Intelligence at Every Step/);assert.match(app.node('#auth-root').innerHTML,/Local demonstration data · not live API metrics/);assert.match(app.node('#auth-root').innerHTML,/data-page="replanning"/);assert.match(app.node('#auth-root').innerHTML,/Make every harvest count/);
app.click({page:'growers'});assert.match(app.node('#auth-root').innerHTML,/Welcome back/);app.click({action:'demo-signin'});assert.match(app.content(),/SUPPLY REGISTRY/);
app.click({action:'sign-out'});app.click({action:'show-home'});
app.click({action:'open-login'}); assert.match(app.node('#auth-root').innerHTML,/Welcome back/); assert.match(app.node('#auth-root').innerHTML,/Continue in demo mode/);
app.node('#login-password').value='sample-not-real'; app.listeners.submit({target:{id:'login-form'},preventDefault(){}}); assert.equal(app.node('#login-password').value,''); assert.match(app.node('#auth-message').textContent,/not configured/);
app.click({action:'demo-signin'}); assert.match(app.content(),/60 kg/);
assert.doesNotMatch(app.content(),/COORDINATOR CONTROL CENTRE|A single, traceable view of supply/,'overview has no redundant image-backed intro banner');
assert.match(html,/\.shell\.page-overview \.workspace:before\{[^}]*!important/,'overview workspace uses a plain background behind the video hero');
assert.match(html,/\.overview-hero\.video-fallback\{background:#0b2119 url\('fungisync-hero\.png'\)/,'poster artwork appears as background only when video fails');
assert.match(app.content(),/<details class="walk-tour">/);assert.match(html,/\.shell:not\(\.page-overview\):not\(\.page-replanning\) \.card/);assert.match(html,/chart\.umd\.min\.js" defer/);assert.match(html,/lucide\.min\.js" type="text\/javascript" defer/);
assert.doesNotMatch(html,/id="motion-toggle"/);assert.match(html,/option value="ta">தமிழ்/);assert.match(html,/@media\(prefers-reduced-motion:reduce\)/);assert.match(source,/reducedMotion/);assert.match(source,/Good evening/);assert.match(source,/शुभ संध्या/);assert.match(source,/மாலை வணக்கம்/);
assert.ok(fs.existsSync(videoPath));assert.ok(fs.statSync(videoPath).size>1000000);assert.match(source,/<video class="overview-video" id="overview-video" autoplay loop muted playsinline preload="metadata" poster="fungisync-hero\.png"/);assert.match(source,/fungisync-overview\.mp4/);
assert.match(html,/option value="en">English/);assert.match(html,/option value="hi">हिन्दी/);assert.doesNotMatch(html,/option value="es"/);
app.node('#language-select').value='ta';app.node('#language-select').onchange({target:{value:'ta'}});assert.equal(store.get('fungisync-language'),'ta');
assert.equal(app.node('.shell').classList.contains('ambient-enabled'),true);assert.equal(app.node('.shell').classList.contains('motion-paused'),false);
app.click({page:'growers'});app.click({action:'add-grower'});app.node('#modal-root form').listeners.submit({target:{fields:{name:'Test Cooperative',location:'Moss Valley',contact:'farmer@example.test',status:'Active'}},preventDefault(){}});assert.match(app.content(),/Test Cooperative/);
app.click({action:'add-batch'});app.node('#modal-root form').listeners.submit({target:{fields:{grower:'GR-001',species:'Oyster',expected:'10',confirmed:'12',saleable:'11',date:'2026-11-10',grade:'A',notes:''}},preventDefault(){}});assert.match(app.node('#form-error').textContent,/cannot exceed expected/);
for (const page of ['growers','buyers','matching','allocations','dispatch','replanning','agents','reports','settings','overview']) {
  app.click({page}); assert.ok(app.content().length>100,`${page} renders`);
  assert.equal(app.node('.shell').classList.contains('ambient-enabled'),page==='replanning'||page==='overview',`${page} ambient mode`);
}
app.click({page:'matching'}); app.click({action:'generate-matches'});
assert.match(app.content(),/BAT-001/);
app.click({page:'allocations'}); app.click({action:'generate-proposal'});
assert.match(app.content(),/ALC-001/); assert.match(app.content(),/60 kg/);
app.click({action:'approve-proposal'}); app.node('#confirm-action').onclick();
assert.match(app.content(),/Approved/);
app.click({page:'dispatch'}); app.click({action:'plan-dispatch'});
assert.match(app.content(),/DSP-001/); assert.match(app.content(),/Farmer A/); assert.match(app.content(),/Buyer A/);
assert.ok(store.has('fungisync.local.v1'));
app.click({page:'buyers'}); app.click({action:'cancel-order:ORD-002'}); app.node('#confirm-action').onclick();
app.click({page:'replanning'}); app.click({action:'analyse-impact'}); app.click({action:'generate-revision'});
assert.match(app.content(),/40 kg/); assert.match(app.content(),/ORD-001/); assert.match(app.content(),/ALC-002/);
app.click({action:'revised-dispatch'}); app.node('#confirm-action').onclick();
let interim=JSON.parse(store.get('fungisync.local.v1')); assert.equal(interim.data.dispatchPlan.id,'DSP-002'); assert.equal(interim.data.dispatchPlan.allocation,'ALC-002');
app.click({page:'dispatch'}); assert.match(app.content(),/DSP-002/); assert.match(app.content(),/ALC-002/); assert.doesNotMatch(app.content(),/Buyer B/);
app.click({page:'replanning'}); app.click({action:'approve-revision'}); app.node('#confirm-action').onclick();
const saved=JSON.parse(store.get('fungisync.local.v1'));
assert.equal(saved.data.orders.find(x=>x.id==='ORD-002').status,'Cancelled');
assert.equal(saved.data.revision.status,'Approved');
assert.equal(saved.data.dispatchPlan.status,'Proposed');
app=boot(); app.click({page:'replanning'});
assert.match(app.content(),/Buyer order cancelled/); assert.match(app.content(),/ALC-002/);
app.click({action:'sign-out'}); assert.match(app.node('#auth-root').innerHTML,/Welcome back/); app.click({action:'show-home'}); assert.match(app.node('#auth-root').innerHTML,/Supply-to-demand network/); app.click({action:'open-login'}); app.click({action:'demo-signin'});
const overCapacity=JSON.parse(store.get('fungisync.local.v1')); overCapacity.data.dispatch=false; overCapacity.data.dispatchPlan=null; overCapacity.data.vehicles[0].capacity=30; store.set('fungisync.local.v1',JSON.stringify(overCapacity)); app=boot(); app.click({page:'dispatch'}); app.click({action:'plan-dispatch'}); assert.match(app.node('#toast-root').innerHTML,/exceeds VEH-001 capacity/); assert.equal(JSON.parse(store.get('fungisync.local.v1')).data.dispatch,false);
console.log('PASS: cinematic landing, login screen, demo entry, and sign out');
console.log('PASS: automatic background motion, reduced-motion handling, and English, Hindi, and Tamil language options');
console.log('PASS: local looping Overview video asset, poster fallback, and autoplay attributes');
console.log('PASS: all 10 workspaces render; grower creation and harvest quantity validation');
console.log('PASS: matching, allocation validation/approval, dispatch stops, and over-capacity guard');
console.log('PASS: cancellation, impact analysis, dynamic revision, comparison state, and revised dispatch');
console.log('PASS: local persistence survives application reinitialization');

