"""Structured completion menus. User choices are saved directly, without another LLM guess."""
import re
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from pydantic import Field
from .schema import Strict, Record, Kind, Query, STATUSES, KINDS
from .store import Conflict

class NeedsCompletion(ValueError):
    def __init__(self, plan):
        self.plan = plan
        super().__init__('Попълване на данни')

class CompletedEntry(Strict):
    entity: Kind
    target_id: str | None = None
    expected_version: int | None = None
    data: Record
    company_ref: str = ''
    contact_ref: str = ''
    company_name: str = Field(default='', max_length=240)
    contact_name: str = Field(default='', max_length=240)

class CompletionInput(Strict):
    revision: int
    entries: list[CompletedEntry] = Field(default_factory=list, max_length=12)
    query: Query | None = None

def date_hint(message, text):
    from .normalize import source_days
    anchor = datetime.fromisoformat(message['recorded_at']).astimezone(ZoneInfo(message['timezone']))
    try:
        days = source_days(text, anchor)
        if len(set(days)) == 1:
            return days[0].isoformat()
    except ValueError:
        pass
    weekdays = ('понеделник','вторник','сряда','четвъртък','петък','събота','неделя')
    hits = [i for i,day in enumerate(weekdays) if re.search(r'\b'+day+r'\b',text,re.I)]
    if len(hits) == 1:
        offset = (hits[0]-anchor.weekday()) % 7
        if not offset and re.search(r'следващ',text,re.I):
            offset = 7
        return (anchor.date()+timedelta(days=offset)).isoformat()
    return ''

def contact_hint(text):
    match = re.search(r'\b(?:с|със|контакт)\s+([^.,!?\n]+)',text,re.I)
    if not match:
        return ''
    words=[]
    for word in match.group(1).split():
        if word.lower() in ('да','за','от','на','в','и','утре','днес','вдругиден','ще','трябва') or any(c.isdigit() for c in word):
            break
        words.append(word)
        if len(words)==3:
            break
    return ' '.join(words).strip()

def infer_entity(text):
    if re.search(r'срещ|се видя|се видим',text,re.I):return 'meetings'
    if re.search(r'контакт',text,re.I):return 'contacts'
    if re.search(r'задач|напомн|трябва',text,re.I):return 'tasks'
    if re.search(r'оферт|сделк',text,re.I):return 'deals'
    return 'notes'

def make_draft(message, records):
    from .normalize import source_times
    text=message['text']
    plan=message.get('proposal') or {}
    if message['mode']=='query':
        query=dict(plan.get('query') or {})
        query.setdefault('entity',infer_entity(text))
        query.setdefault('search','')
        day=date_hint(message,text)
        if day:
            query['date_from']=query['date_to']=day
        return {'revision':message['revision'],'mode':'query','query':query,'timezone':message['timezone'],'entries':[]}
    operations=plan.get('operations') or []
    fallback=not operations
    if fallback:
        kind=infer_entity(text)
        name=contact_hint(text) if kind=='meetings' else ''
        operations=[{'entity':kind,'title':('Среща с '+name if name else text)[:240],
                     'body':text,'contact_name':name,'evidence':text}]
    entries=[]
    for op in operations:
        entity=op['entity']
        previous=next((r for r in records if r['entity']==entity and r['id']==op.get('target_id')),None)
        data={k:previous[k] for k in Record.model_fields} if previous else {'title':op['title'],'body':op.get('body','')}
        for key in Record.model_fields:
            if key in op and op[key] is not None and (key!='body' or op[key]):
                data[key]=op[key]
        evidence=op.get('evidence','')
        if evidence not in text:
            evidence=text
        day=date_hint(message,evidence) or date_hint(message,text)
        try:
            clocks=source_times(evidence)
        except ValueError:
            clocks=[]
        start_clocks=[v for prefix,v in clocks if prefix!='до']
        due_clocks=[v for _,v in clocks]
        clock=start_clocks[0] if entity=='meetings' and len(start_clocks)==1 else due_clocks[0] if entity=='tasks' and len(due_clocks)==1 else None
        # Do not prefill an invented model time or end time after failed extraction.
        if entity in ('meetings','tasks') and not previous:
            data['start_at']=None;data['due_at']=None;data['end_at']=None
            if day and clock:
                dt=datetime.combine(datetime.fromisoformat(day).date(),clock,ZoneInfo(message['timezone'])).isoformat()
                data['start_at' if entity=='meetings' else 'due_at']=dt
        entry={'entity':entity,'target_id':previous['id'] if previous else None,
               'expected_version':op.get('expected_version') if previous else None,
               'action':op.get('action','create'),'data':data,'date_hint':day,
               'time_hint':clock.strftime('%H:%M') if clock else '',
               'company_name':op.get('company_name') or '', 'contact_name':op.get('contact_name') or '',
               'company_ref':data.get('company_id') or '', 'contact_ref':data.get('contact_id') or ''}
        if entity=='meetings' and not entry['contact_name']:
            entry['contact_name']=contact_hint(evidence)
        entries.append(entry)
    for entry in entries:
        for table,prefix in [('companies','company'),('contacts','contact')]:
            name=entry[prefix+'_name']
            if not name or entry[prefix+'_ref']:
                continue
            matches=[r for r in records if r['entity']==table and r['title'].casefold()==name.casefold() and r['status']!='archived']
            draft_matches=[i for i,p in enumerate(entries) if p['entity']==table and p['data']['title'].casefold()==name.casefold()]
            entry[prefix+'_ref']=matches[0]['id'] if len(matches)==1 else 'draft:'+str(draft_matches[0]) if len(draft_matches)==1 else 'new' if not matches else ''
    return {'revision':message['revision'],'mode':'write','entries':entries,'timezone':message['timezone']}

def save_entries(store,c,message,body,device_id):
    if not body.entries:
        raise ValueError('Добавете поне един запис във формата.')
    results=[];saved={};new_parents={}
    def parent(table,ref,name,company_id=None):
        if not ref:
            return None
        if ref.startswith('draft:'):
            try:index=int(ref.split(':',1)[1])
            except ValueError:raise ValueError('Невалидна връзка между записите.')
            record=saved.get(index)
            if not record or record['entity']!=table:
                raise ValueError('Изберете валидна връзка между записите.')
            return record['id']
        if ref=='new':
            name=name.strip()
            if not name:
                raise ValueError('Попълнете името на новия контакт или фирма.')
            key=(table,name.casefold())
            if key in new_parents:return new_parents[key]
            if any(r['title'].casefold()==name.casefold() and r['status']!='archived' for r in store.records(c,table)):
                raise Conflict(f'„{name}“ вече съществува. Изберете записа от падащото меню.')
            record=store.save(c,table,{'title':name,'company_id':company_id if table=='contacts' else None},device_id,message_id=message['id'])
            results.append(record);new_parents[key]=record['id']
            return record['id']
        record=store.record(c,table,ref)
        if record['status']=='archived':raise Conflict('Избраният запис е архивиран. Изберете друг.')
        return record['id']
    for index,entry in sorted(enumerate(body.entries),key=lambda pair:KINDS.index(pair[1].entity)):
        data=entry.data.model_dump()
        if entry.entity!='companies':data['company_id']=parent('companies',entry.company_ref,entry.company_name)
        if entry.entity not in ('companies','contacts'):
            data['contact_id']=parent('contacts',entry.contact_ref,entry.contact_name,data['company_id'])
            if data['contact_id'] and not data['company_id']:
                data['company_id']=store.record(c,'contacts',data['contact_id'])['company_id']
        record=store.save(c,entry.entity,data,device_id,entry.target_id,entry.expected_version,message['id'])
        results.append(record);saved[index]=record
    return results
