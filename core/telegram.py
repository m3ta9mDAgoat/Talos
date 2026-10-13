"""Immutable archive revisions + event-time replay. Does not connect to Telegram or send messages.
Legacy Talos v4.7.8 watcher/session/alerts stay untouched. Research imports are append-only.
"""
from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone

def normalize_archive(events):
    if not isinstance(events,list) or len(events)>3000:raise ValueError('Archive must be a list of <=3000 events')
    out=[];seen=set()
    for row in events:
        if not isinstance(row,dict):raise ValueError('Invalid Telegram event object')
        chat=str(row.get('chat_id',''))
        mid=str(row.get('message_id',''))
        if not chat or not mid or len(chat)>100 or len(mid)>80:raise ValueError('chat_id and message_id required')
        ts=str(row.get('timestamp') or row.get('date') or '')
        try:
            dt=datetime.fromisoformat(ts.replace('Z','+00:00'))
            if dt.tzinfo is None:raise ValueError('Timezone required')
            dt=dt.astimezone(timezone.utc)
        except Exception as e:raise ValueError('Event timestamp requires timezone ISO 8601') from e
        kind=str(row.get('event_type','new')).lower()
        if kind not in ('new','edit','delete'):raise ValueError('Unsupported Telegram revision event')
        revision=str(row.get('revision','0'))
        key=(chat,mid,revision,kind)
        if key in seen:raise ValueError(f'Duplicate revision {key}')
        seen.add(key)
        text=str(row.get('text',''))[:20000]
        if kind=='delete':text=''
        out.append({'chat_id':chat,'message_id':mid,'revision':revision,
                    'timestamp':dt.isoformat(),'event_type':kind,'text':text,
                    'source':'USER_IMPORTED_NOT_VERIFIED_BY_TELEGRAM'})
    out.sort(key=lambda r:(r['timestamp'],r['chat_id'],r['message_id'],r['revision']))
    return out

def replay(events,cutoff=None):
    if cutoff:
        dt=datetime.fromisoformat(cutoff.replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError('cutoff timezone required')
        cutoff=dt.astimezone(timezone.utc).isoformat()
    state={};numbers={'new':0,'edit':0,'delete':0}
    for e in normalize_archive(events):
        if cutoff and e['timestamp']>cutoff:break
        key=(e['chat_id'],e['message_id'])
        numbers[e['event_type']]+=1
        if e['event_type']=='delete':state.pop(key,None)
        else:state[key]=e
    return {'status':'COMPLETED','events_applied':sum(numbers.values()),'new':numbers['new'],
            'edits':numbers['edit'],'deletes':numbers['delete'],'active_messages':len(state),
            'point_in_time_cutoff':cutoff,'source_authenticated':False,
            'notifications_sent':False,'legacy_archive_modified':False,
            'limitation':'Archive import does not authenticate upstream Telegram edits/deletes'}

def signal_timeline(events,bars,symbol):
    """Point-in-time, conservative BUY/SELL text benchmark (NOT general NLP strategy).
    A dated command is acted upon only at the next available daily session open.
    Editing a message creates a new event; no revision is projected backward.
    Deleting a message never rewinds an already executed historical position.
    """
    import re
    events=normalize_archive(events)
    current=0;index=0;targets=[];accepted=0;discarded=0
    for bar in bars:
        # Daily close is known at the end of its UTC date; any message recorded
        # on this date will not execute until the NEXT bar (never same day).
        while index<len(events) and events[index]['timestamp'][:10]<=bar['date']:
            e=events[index];index+=1
            if e['event_type']=='delete':continue
            m=re.search(r'\b(BUY|SELL)\s+([A-Z0-9_./:=^-]+)\b',e['text'].upper())
            if not m or m.group(2)!=symbol.upper():discarded+=1;continue
            current=1 if m.group(1)=='BUY' else 0;accepted+=1
        targets.append(current)
    return targets,{'accepted_explicit_commands':accepted,'unmapped_events':discarded,
                    'ignored_future_events':len(events)-index,'source_authenticated':False,
                    'timing_rule':'Signal first available after UTC event date; order next supplied session OPEN'}
