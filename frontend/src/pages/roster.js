import {DOMAIN} from '../constants/domain.js';
import {store,load} from '../stores/research.js';
import {roster} from '../api/research.js';
import {studySelect} from '../components/common/study-select.js';
import {table} from '../components/common/table.js';
import {bindForm,formObject} from '../hooks/forms.js';
import {esc} from '../utils/format.js';

let data={locked:false,lock:null,entries:[],summary:[]};

function summaryLine(s){
  return `${esc(s.shift)}：${s.seats}席（AB ${s.ab} / BA ${s.ba}），已领 ${s.claimed}，完整 ${s.complete}，缺测 ${s.missing}`;
}

function lockSection(writable){
  if(data.locked){
    const lock=data.lock;
    return `<section class="result-intro"><strong>名册已锁定</strong><p>seed <code>${esc(lock.seed)}</code> · ${esc(lock.locked_at)} · ${esc(lock.locked_by)}</p><p>${data.summary.map(summaryLine).join('；')}</p></section>`;
  }
  if(!writable)return '<p class="method-note">观察员只能阅读名册与分派结果。</p>';
  return `<form id="lock-form" class="worksheet"><label>固定 seed（留空则自动生成）<input name="seed" maxlength="64" placeholder="如 proof-202611"></label><label>班次与纸样（每行一个班次：班次名=编号,编号,...；每班必须偶数）<textarea name="shifts" required placeholder="早班=P001,P002,P003,P004&#10;晚班=P005,P006,P007,P008"></textarea></label><p>锁定后名册席位、分派顺序与 seed 一并固定，可作为复算依据。</p><button type="submit">锁定名册 →</button></form>`;
}

function entryTable(writable){
  const rows=data.entries.map(e=>[
    e.shift,e.slot_key,e.assigned_order,e.claimed_by||'—',e.actual_order||'—',e.reading1??'—',e.reading2??'—',
    {html:writable&&!e.actual_order?`<button class="text" data-entry="${e.id}">领单 / 登记</button>`:''},
  ]);
  return table(['班次','纸样','分派顺序','领单人','实际顺序','读数1','读数2','动作'],rows);
}

function readingsForm(writable){
  if(!writable)return '';
  return `<form id="read-form" class="inline-form" hidden><input type="hidden" name="entry"><label>读数1<input name="reading1" type="number" step="any" min="0" required></label><label>读数2<input name="reading2" type="number" step="any" min="0" required></label><label>实际顺序<select name="actual_order"><option value="AB">AB（原→新）</option><option value="BA">BA（新→原）</option></select></label><button type="submit">登记读数</button></form>`;
}

export async function render(){
  const writable=localStorage.getItem('research-role')!=='viewer';
  data=store.studyId?await roster.get(store.studyId):{locked:false,lock:null,entries:[],summary:[]};
  const detail=data.locked?`<h2>席位分派 <small>先领单再登记读数；实际顺序与分派不符将被拒绝</small></h2>${entryTable(writable)}${readingsForm(writable)}`:'';
  return `<div class="eyebrow">02 / PROOF ROSTER</div><h1>名册与顺序分派</h1><p class="lead">测量前锁定纸样名册，每班等额随机分派 AB/BA 两种工艺顺序。名册锁定后不重抽、不补抽；并发领单看到同一分派。</p><div class="method-note">${DOMAIN.note}</div><div class="toolbar">${studySelect()}</div>${lockSection(writable)}${detail}`;
}

export function bind(refresh){
  const sel=document.getElementById('study-select');
  if(sel)sel.onchange=()=>{store.studyId=+sel.value;refresh();};
  bindForm('lock-form',async()=>{
    const form=document.getElementById('lock-form');
    const {seed,shifts}=formObject(form);
    const parsed=shifts.split('\n').map(x=>x.trim()).filter(Boolean).map(line=>{
      const [name,...rest]=line.split('=');
      return {name:name.trim(),slots:rest.join('=').split(',').map(s=>s.trim()).filter(Boolean)};
    });
    await roster.lock(store.studyId,{seed:seed.trim()||null,shifts:parsed});
    await load();
    refresh();
  });
  document.querySelectorAll('[data-entry]').forEach(el=>el.onclick=async()=>{
    const entry=data.entries.find(e=>e.id===+el.dataset.entry);
    await roster.claim(store.studyId,entry.id);
    const form=document.getElementById('read-form');
    form.hidden=false;
    form.elements.entry.value=entry.id;
    form.elements.actual_order.value=entry.assigned_order;
    form.scrollIntoView({behavior:'smooth',block:'center'});
  });
  const form=document.getElementById('read-form');
  if(form)form.onsubmit=async e=>{
    e.preventDefault();
    const x=formObject(form);
    try{
      await roster.readings(store.studyId,+x.entry,{reading1:+x.reading1,reading2:+x.reading2,actual_order:x.actual_order});
      refresh();
    }catch(err){document.getElementById('notice').textContent=err.message;}
  };
}
