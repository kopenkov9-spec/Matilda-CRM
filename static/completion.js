// A form edits a preserved draft, then submits explicit user choices without re-running Phi.
let completionDraft=null;
let completionMessage=null;
const completionFields=['title','body','company_id','contact_id','email','phone','start_at','end_at','due_at','status','amount','currency'];

async function completeMessage(id){
  completionDraft=await api('/messages/'+id+'/completion');
  completionMessage=state.messages.find(m=>m.id===id);
  for(const entry of completionDraft.entries){
    const date=entry.data.start_at||entry.data.due_at;
    entry.formDate=date?localInput(date).split('T')[0]:entry.date_hint||'';
    entry.formTime=date?localInput(date).split('T')[1]:entry.time_hint||'';
  }
  renderCompletion();
}

function completionSelect(name,label,value,options,required=false){
  return `<div class="completion-field ${required&&!value?'missing':''}"><label>${esc(label)}${required?' <span class="required-mark">*</span>':''}</label><select name="${name}" aria-label="${esc(label)}" ${required?'required':''}>${options.map(([v,t])=>`<option value="${esc(v)}" ${String(v)===String(value??'')?'selected':''}>${esc(t)}</option>`).join('')}</select></div>`;
}
function completionInput(name,label,value,type='text',required=false){
  return `<div class="completion-field ${required&&!value?'missing':''}"><label>${esc(label)}${required?' <span class="required-mark">*</span>':''}</label><input name="${name}" aria-label="${esc(label)}" type="${type}" value="${esc(value??'')}" ${required?'required':''} ${type==='number'?'min="0" step="0.01"':''} ${type==='text'?'maxlength="240"':''}></div>`;
}
function relationMenu(entry,index,prefix,kind){
  const opts=[['','Без връзка'],['new',prefix==='contact'?'+ Нов контакт':'+ Нова фирма']];
  for(const r of active(kind))opts.push([r.id,r.title+(r.email?' · '+r.email:r.phone?' · '+r.phone:'')]);
  completionDraft.entries.forEach((other,i)=>{if(i!==index&&other.entity===kind)opts.push(['draft:'+i,other.data.title+' · от тази форма']);});
  if(entry[prefix+'_name']&&!entry[prefix+'_ref'])opts[0]=['','Изберете от менюто'];
  const required=!!entry[prefix+'_name'];
  return completionSelect(`${index}:${prefix}_ref`,prefix==='contact'?'Контакт':'Фирма',entry[prefix+'_ref'],opts,required)+
    `<div data-new-parent="${index}:${prefix}" ${entry[prefix+'_ref']==='new'?'':'hidden'}>${completionInput(`${index}:${prefix}_name`,prefix==='contact'?'Име на новия контакт':'Име на новата фирма',entry[prefix+'_name'],'text',entry[prefix+'_ref']==='new')}</div>`;
}

function renderCompletion(){
  const draft=completionDraft;
  let fields='';
  if(draft.mode==='query'){
    const q=draft.query;
    fields=`<div class="form-grid">${completionSelect('query:entity','Какво да покажа',q.entity||'',[['','Всички записи'],...Object.keys(state.system.statuses||{}).map(k=>[k,labels[k]])])}${completionInput('query:search','Име / ключова дума',q.search)}${completionInput('query:date_from','От дата',q.date_from,'date')}${completionInput('query:date_to','До дата',q.date_to,'date')}${completionSelect('query:status','Статус',q.status||'',[['','Всички'],...(state.system.statuses?.[q.entity]||[]).map(s=>[s,statusLabels[s]])])}</div>`;
  }else{
    fields=draft.entries.map((entry,i)=>{
      const d=entry.data,kind=entry.entity;
      const targetOptions=[[entry.action==='update'&&!entry.target_id?'':'new',entry.action==='update'&&!entry.target_id?'Изберете запис':'Нов запис'],...active(kind).map(r=>[r.id,'Редакция: '+r.title])];
      if(entry.action==='update'&&!entry.target_id)targetOptions.push(['new','Нов запис']);
      let primary=completionSelect(`${i}:entity`,'Вид запис',kind,Object.keys(state.system.statuses||{}).map(k=>[k,labels[k]]),true)+
        completionSelect(`${i}:target_id`,'Действие',entry.target_id||(entry.action==='update'?'':'new'),targetOptions,true)+
        `<div class="full">${completionInput(`${i}:title`,'Заглавие / име',d.title,'text',true)}</div>`;
      if(kind!=='companies')primary+=relationMenu(entry,i,'company','companies');
      if(!['companies','contacts'].includes(kind))primary+=relationMenu(entry,i,'contact','contacts');
      if(['meetings','tasks'].includes(kind)){
        primary+=completionInput(`${i}:formDate`,kind==='meetings'?'Дата на срещата':'Дата на срока',entry.formDate,'date',kind==='meetings')+
          completionInput(`${i}:formTime`,kind==='meetings'?'Начален час · София':'Час на срока · София',entry.formTime,'time',kind==='meetings'||!!entry.formDate);
      }
      let extra=completionSelect(`${i}:status`,'Статус',d.status||state.system.statuses[kind][0],state.system.statuses[kind].map(s=>[s,statusLabels[s]]));
      if(['contacts','companies'].includes(kind))extra+=completionInput(`${i}:email`,'Имейл',d.email,'email')+completionInput(`${i}:phone`,'Телефон',d.phone,'tel');
      if(kind==='meetings')extra+=completionInput(`${i}:end_at`,'Краен час (по избор)',localInput(d.end_at),'datetime-local');
      if(kind==='deals')extra+=completionInput(`${i}:amount`,'Стойност (по избор)',d.amount,'number')+completionInput(`${i}:currency`,'Валута (EUR, BGN…)',d.currency);
      return `<section class="completion-entry"><header><h3>${i+1}. ${esc(labels[kind])}</h3>${draft.entries.length>1?`<button type="button" class="icon-btn" data-remove-entry="${i}" aria-label="Премахни този предложен запис">${icon('close')}</button>`:''}</header><div class="form-grid">${primary}</div><details class="completion-extra"><summary>Допълнителни данни</summary><div class="form-grid">${extra}<div class="full"><label>Бележка / описание</label><textarea name="${i}:body" aria-label="Бележка / описание" maxlength="6000">${esc(d.body||'')}</textarea></div></div></details></section>`;
    }).join('');
  }
  openModal(draft.mode==='query'?'Изберете данните за справката':'Попълнете данните',`<div class="completion-intro">${draft.mode==='query'?'Изберете какво да покажем от CRM.':'Разпознатото е попълнено. Добавете липсващите полета и запазете.'}</div><details class="completion-source"><summary>Оригинално съобщение</summary><p>${esc(completionMessage.text)}</p></details><form id="completion-form">${fields}<div id="completion-error" role="alert"></div><div class="modal-footer">${draft.mode==='write'&&draft.entries.length<12?'<button type="button" class="btn secondary" id="completion-add">+ Още запис</button>':''}<button class="btn" type="submit">${icon(draft.mode==='query'?'search':'check')}${draft.mode==='query'?'Покажи резултатите':'Запази данните'}</button></div></form>`);
  $('#modal').classList.add('completion-modal');
  const form=$('#completion-form');
  form.addEventListener('change',event=>{
    const [idx,key]=(event.target.name||'').split(':');
    captureCompletion();
    if(idx==='query'&&key==='entity'){draft.query.status=null;renderCompletion();return;}
    if(idx==='query')return;
    const entry=draft.entries[Number(idx)];if(!entry)return;
    if(key==='entity'){entry.data.status=null;entry.target_id=null;entry.expected_version=null;entry.action='create';renderCompletion();}
    else if(key==='target_id'){
      const r=state.records.find(r=>r.id===event.target.value&&r.entity===entry.entity);
      entry.target_id=r?.id||null;entry.expected_version=r?.version||null;entry.action=r?'update':'create';
      if(r){const base={};for(const k of completionFields)base[k]=r[k];for(const [k,v]of Object.entries(entry.data))if(v!==null&&v!=='')base[k]=v;entry.data=base;}
      renderCompletion();
    }else if(key==='company_ref'||key==='contact_ref'){
      const prefix=key.split('_')[0];const box=form.querySelector(`[data-new-parent="${idx}:${prefix}"]`);box.hidden=event.target.value!=='new';$('input',box).required=event.target.value==='new';
    }else if(key==='formDate'){
      form.elements[`${idx}:formTime`].required=entry.entity==='meetings'||!!event.target.value;
    }
  });
  form.querySelectorAll('[data-remove-entry]').forEach(button=>button.onclick=()=>{
    captureCompletion();const index=Number(button.dataset.removeEntry);draft.entries.splice(index,1);
    for(const entry of draft.entries)for(const prefix of ['company','contact']){const ref=entry[prefix+'_ref'];if(ref?.startsWith('draft:')){const n=Number(ref.split(':')[1]);entry[prefix+'_ref']=n===index?'':n>index?'draft:'+(n-1):ref;}}
    renderCompletion();
  });
  if($('#completion-add'))$('#completion-add').onclick=()=>{captureCompletion();draft.entries.push({entity:'notes',action:'create',data:{title:'',body:''},company_ref:'',contact_ref:'',company_name:'',contact_name:'',formDate:'',formTime:''});renderCompletion();};
  form.onsubmit=async event=>{
    event.preventDefault();const submit=$('button[type=submit]',form);submit.disabled=true;
    try{
      captureCompletion();
      const body={revision:draft.revision};
      if(draft.mode==='query')body.query=draft.query;
      else body.entries=draft.entries.map(entry=>{
        const data={};for(const k of completionFields)data[k]=entry.data[k]??(['title','body'].includes(k)?'':null);
        if(['meetings','tasks'].includes(entry.entity)){
          if(entry.formDate&&!entry.formTime)throw new Error('Изберете час от полето за часа.');
          if(entry.formTime&&!entry.formDate)throw new Error('Изберете дата от календара.');
          data[entry.entity==='meetings'?'start_at':'due_at']=entry.formDate?sofiaISO(entry.formDate+'T'+entry.formTime):null;
        }
        return {entity:entry.entity,target_id:entry.target_id||null,expected_version:entry.expected_version||null,data,
          company_ref:entry.company_ref||'',contact_ref:entry.contact_ref||'',company_name:entry.company_name||'',contact_name:entry.contact_name||''};
      });
      await api('/messages/'+completionMessage.id+'/complete',{method:'POST',body});
      $('#modal').close();await refresh();toast(draft.mode==='query'?'Справката е готова.':'Данните са записани.');
    }catch(error){$('#completion-error').innerHTML=`<div class="error">${esc(error.message)}</div>`;}
    finally{submit.disabled=false;}
  };
}

function captureCompletion(){
  const form=$('#completion-form');if(!form)return;
  for(const [name,value] of new FormData(form)){
    const [idx,key]=name.split(':');
    if(idx==='query'){completionDraft.query[key]=value||(key==='search'?'':null);continue;}
    const entry=completionDraft.entries[Number(idx)];if(!entry)continue;
    if(['entity','company_ref','contact_ref','company_name','contact_name','formDate','formTime'].includes(key))entry[key]=value;
    else if(key==='target_id'){entry.target_id=value&&value!=='new'?value:null;}
    else if(key==='amount')entry.data[key]=value===''?null:Number(value);
    else if(key==='end_at')entry.data[key]=value?sofiaISO(value):null;
    else entry.data[key]=value||(['title','body'].includes(key)?'':null);
  }
}
$('#modal').addEventListener('close',()=>$('#modal').classList.remove('completion-modal'));
