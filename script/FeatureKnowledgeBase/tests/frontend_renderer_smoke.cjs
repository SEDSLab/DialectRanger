// Actual renderer execution with a minimal DOM, no browser or network.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const base = path.resolve(process.argv[2] || path.join(root, 'output/pre_unseen_final/release_viewer'));
class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.value = ''; this._text = ''; this.classList = {add(){},remove(){}}; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  append(...v) { this.children.push(...v); }
  add(v) { this.append(v); }
  replaceChildren(...v) { this.children = v; this._text = ''; }
  addEventListener(name, fn) { this[name] = fn; }
}
const wait = () => new Promise(resolve => setImmediate(resolve));
async function inspect(entry, data, record) {
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node(id)); return nodes.get(id); };
  get('dataset').value = 'final';
  const url = 'http://localhost/?db=' + entry.id + '&feature=' + encodeURIComponent(record.feature_id);
  const ctx = {URL, URLSearchParams, Set, Map, console, location:{href:url,search:new URL(url).search},history:{replaceState(){}},
    document:{getElementById:get,createElement:tag=>new Node(tag)},
    Option:function(label,value){const n=new Node('option');n.textContent=label;n.value=value;return n;},
    fetch:async u=>({ok:true,text:async()=>JSON.stringify(u==='data/catalog.json'?{databases:[entry]}:data)})};
  vm.runInNewContext(fs.readFileSync(path.join(root,'frontend/app.js'),'utf8'),ctx);
  await wait(); await wait();
  assert.ok(!get('notice').textContent.includes('加载失败'), get('notice').textContent);
  const sections = get('detail').children.filter(n => n.tagName==='section');
  const section = prefix => sections.find(n => n.children[0].textContent.startsWith(prefix));
  const values = (prefix,tag) => section(prefix).children.filter(n=>n.className==='claim').map(n=>n.children.find(c=>c.tagName===tag && c.className!=='muted').textContent);
  assert.deepEqual(values('Description','p').filter(t=>!t.startsWith('官方表格原文')), (record.semantics.description||[]).map(c=>c.value));
  const syntax=(record.semantics.syntax||[]).filter(c=>c.evidence.evidence_role==='FORMAL_SYNTAX');
  assert.deepEqual(values('Formal Syntax','pre'),syntax.map(c=>c.value));
  assert.deepEqual(values('Official Examples','pre'),(record.semantics.examples||[]).map(c=>c.value));
  const reviews=(record.structural.attributes||{}).field_reviews||[];
  assert.equal(Boolean(section('Field Review')), Boolean(reviews.length));
  get('search').value='__not_a_real_feature__';get('search').input();
  assert.ok(get('count').textContent.includes('0 /'));
}
(async()=>{
  const catalog=JSON.parse(fs.readFileSync(path.join(base,'data/catalog.json'),'utf8'));
  let checked=0;
  for(const entry of catalog.databases){
    const data=JSON.parse(fs.readFileSync(path.join(base,'data',entry.data),'utf8'));
    const selected=data.final.filter(r=>(r.structural.attributes.field_reviews||[]).length || ['ABS','ACOS','CONCAT','JSON_SET','IS NOT','TIMESTAMP','INTEGER','*','/','||'].includes(r.name));
    for(const record of selected){ await inspect(entry,data,record); checked++; }
  }
  console.log(JSON.stringify({renderer_cases:checked,description_order:true,formal_syntax_roles:true,examples_preserved:true,field_review_visible:true,network_calls:0}));
})().catch(e=>{console.error(e);process.exitCode=1;});
