"""Conservative date grounding: use explicit source text, never a guessed LLM timezone."""
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from .schema import Plan
from .completion import NeedsCompletion

DAY_WORDS={'днес':0,'утре':1,'вдругиден':2,'вчера':-1}
MONTHS=['януари','февруари','март','април','май','юни','юли','август','септември','октомври','ноември','декември']

def source_days(text, anchor):
    found=[]
    for word,delta in DAY_WORDS.items():
        for match in re.finditer(r'\b'+word+r'\b',text,re.I):
            found.append((match.start(),anchor.date()+timedelta(days=delta)))
    for match in re.finditer(r'\b(\d{4})-(\d{2})-(\d{2})\b',text):
        found.append((match.start(),date(*map(int,match.groups()))))
    for match in re.finditer(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b',text):
        d,m,y=map(int,match.groups());found.append((match.start(),date(y,m,d)))
    for match in re.finditer(r'\b(\d{1,2})\s+('+'|'.join(MONTHS)+r')(?:\s+(\d{4}))?\b',text,re.I):
        d,month,y=match.groups();found.append((match.start(),date(int(y or anchor.year),MONTHS.index(month.lower())+1,int(d))))
    return [d for _,d in sorted(found)]

def source_times(text):
    matches=[]
    for match in re.finditer(r'\b(в|от|до|към)\s*(\d{1,2})(?::(\d{2}))?(?:\s*(?:ч\.?|часа))?(?![\d:]|\.\d)',text,re.I):
        prefix,h,m=match.groups();h=int(h);m=int(m or 0)
        if h>23 or m>59:raise ValueError('Уточнете часа в съобщението.')
        matches.append((prefix.lower(),time(h,m)))
    if not matches:
        for match in re.finditer(r'\b(\d{1,2}):(\d{2})\b',text):
            h,m=map(int,match.groups())
            matches.append(('в',time(h,m)))
    if re.search(r'\bдо\s+обяд\b',text,re.I):matches.append(('до',time(12,0)))
    return matches

def local_stamp(day,clock,zone):
    naive=datetime.combine(day,clock)
    first=naive.replace(tzinfo=zone,fold=0)
    second=naive.replace(tzinfo=zone,fold=1)
    if first.utcoffset()!=second.utcoffset():
        raise ValueError('Часът попада в смяна на лятното часово време. Уточнете точния момент.')
    if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=naive:
        raise ValueError('Този местен час не съществува. Уточнете часа.')
    return first.isoformat()

def ground_plan(plan,message,records):
    zone=ZoneInfo(message['timezone'])
    anchor=datetime.fromisoformat(message['recorded_at']).astimezone(zone)
    text=message['text']
    if plan.intent=='query':
        days=source_days(text,anchor)
        if len(set(days))==1:
            plan.query.date_from=plan.query.date_to=days[0].isoformat()
        elif len(days)==2 and re.search(r'\bот\b.*\bдо\b',text,re.I):
            plan.query.date_from=min(days).isoformat();plan.query.date_to=max(days).isoformat()
        elif re.search(r'\b(тази|следващата)\s+седмица\b',text,re.I):
            monday=anchor.date()-timedelta(days=anchor.weekday())
            if 'следващата' in text.lower():monday+=timedelta(days=7)
            plan.query.date_from=monday.isoformat();plan.query.date_to=(monday+timedelta(days=6)).isoformat()
        elif plan.query.date_from or plan.query.date_to:
            raise NeedsCompletion(plan)
        return plan
    for op in plan.operations:
        if op.entity not in ('meetings','tasks'):continue
        evidence=op.evidence
        days=source_days(evidence,anchor)
        if not days:
            all_days=source_days(text,anchor)
            if len(set(all_days))==1:days=all_days
        clocks=source_times(evidence)
        if op.entity=='meetings':
            starts=[clock for prefix,clock in clocks if prefix!='до']
            ends=[clock for prefix,clock in clocks if prefix=='до']
            if op.start_at or op.action=='create':
                if len(set(days))!=1 or len(starts)!=1:
                    raise NeedsCompletion(plan)
                op.start_at=local_stamp(days[0],starts[0],zone)
            if ends and len(set(days))==1:
                op.end_at=local_stamp(days[0],ends[0],zone)
            else:
                duration=re.search(r'\bза\s+(\d+)\s*(минути|часа|час)\b',evidence,re.I)
                if duration and op.start_at:
                    value,unit=duration.groups();minutes=int(value)*(1 if unit.lower()=='минути' else 60)
                    op.end_at=(datetime.fromisoformat(op.start_at)+timedelta(minutes=minutes)).isoformat()
                else:
                    op.end_at=None
            if not op.contact_name:
                names={r['title'] for r in records if r['entity']=='contacts' and r['status']!='archived'}
                names.update(p.title for p in plan.operations if p.entity=='contacts')
                matches=[name for name in names if name in evidence]
                if len(matches)==1:op.contact_name=matches[0]
        elif op.due_at:
            if len(set(days))==1 and len(clocks)==1:
                op.due_at=local_stamp(days[0],clocks[0][1],zone)
            else:
                raise NeedsCompletion(plan)
    return plan
