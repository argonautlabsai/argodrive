"""Recorded streaming evidence. Never infer RAID or physical drives from fd count."""
import re
from pathlib import Path


def streaming_profile(text, engine_text=''):
    env_line = re.search(r'^DS4_ENV (.*)$', text, re.M)
    requested = dict(re.findall(r'(\w+)=(\S+)', env_line.group(1))) if env_line else {}
    if not env_line:
        requested = dict(re.findall(r'\b(K3_\w+|DELTAFIN_ROOT)=(\S+)', text.split('\n', 1)[0]))
    config_line = re.search(r'^CONFIG (.*)$', text, re.M)
    config = dict(re.findall(r'(\w+)=(\S+)', config_line.group(1))) if config_line else {}
    evidence_text = text + '\n' + engine_text
    fields = []
    def field(label, value, source, proof):
        fields.append({'label':label, 'value':str(value), 'source':source, 'evidence':proof})
    method, status = 'Not recorded', 'unknown'
    # An explicit engine declaration outranks an environment request.
    split = re.search(r'^.*expert pread striping across (\d+) fds,.*?\(primary weight (\d+), mode=([^\s)]+)\).*$', evidence_text, re.M)
    if split:
        raw_mode = split.group(3)
        method = {'split-read':'Replica split reads', 'split':'Replica split reads',
                  'round-robin':'Replica round robin', 'dual-home':'Dual-home reads'}.get(raw_mode, 'Replica reads: '+raw_mode)
        status = 'engine'
        field('Read allocation', method, 'engine', split.group(0))
        field('Model file descriptors', split.group(1), 'engine', split.group(0))
        field('Primary weight', split.group(2), 'engine', split.group(0))
    elif ar := re.search(r'^ds4: experimental Argodrive expert reader sources=(\d+); Engram unchanged$', evidence_text, re.M):
        method, status = 'Argodrive expert reads', 'engine'
        field('Read allocation', method, 'engine', ar.group(0))
        field('Expert reader sources', ar.group(1), 'engine', ar.group(0))
    elif requested.get('DS4_MODEL_REPLICAS'):
        method, status = 'Replicas configured', 'requested'
        field('Read allocation', 'Replicas configured; read mode unconfirmed', 'requested', 'DS4_MODEL_REPLICAS='+requested['DS4_MODEL_REPLICAS'])
    elif requested.get('K3_SPLIT_READ') not in (None, '0', 'off'):
        method, status = 'Expert split reads', 'requested'
        field('Read allocation', method, 'requested', 'K3_SPLIT_READ='+requested['K3_SPLIT_READ'])
    elif requested.get('K3_MIRROR_SCHED') not in (None, '0', 'off'):
        method, status = 'Mirror scheduling', 'requested'
        field('Read allocation', method, 'requested', 'K3_MIRROR_SCHED='+requested['K3_MIRROR_SCHED'])
    else:
        field('Read allocation', method, 'unknown', 'No supported streaming-mode declaration was recorded.')
    field('Filesystem RAID', 'Not recorded', 'unknown', 'Replica striping and file-descriptor counts do not establish RAID0 or the number of physical drives.')
    for label, key in [('Reader threads','DS4_METAL_STREAMING_EXPERT_PREAD_THREADS'),
                       ('Reader threads','K3_EXPERT_READ_THREADS'),
                       ('Prefetch threads','K3_EXPERT_PREFETCH_THREADS'),
                       ('ETA routing','K3_SPLIT_ETA'),('ETA rates','K3_SPLIT_ETA_GBPS'),
                       ('Plan balancing','K3_PLAN_BALANCE'),('Split mode','K3_SPLIT_READ'),
                       ('Primary weight','DS4_MODEL_PRIMARY_WEIGHT'),
                       ('Pieces per descriptor','DS4_MODEL_REPLICA_PIECES_FD'),
                       ('Read-ahead disabled','DS4_METAL_DISABLE_STREAMING_EXPERT_READAHEAD'),
                       ('Lookahead experts','DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH'),
                       ('LRU eviction','DS4_METAL_STREAM_EXPERT_EVICT_LRU'),
                       ('Precommit mode','DS4_METAL_GLM_STREAM_PRECOMMIT'),
                       ('RAM expert pool','K3_SUMMER_POOL'),('Pinned experts GB','K3_EXPERT_PIN_GB')]:
        if key in requested and not any(f['label']==label for f in fields):
            field(label, requested[key], 'requested', key+'='+requested[key])
    for label,key in [('Requested cache','cache'),('Resident full layers','full_layers'),('Cold mode','cold'),('Full-layer prefill','full_layer_prefill')]:
        if key in config: field(label,config[key],'requested','CONFIG '+key+'='+config[key])
    nocache = re.search(r'^.*F_NOCACHE set on (\d+) model fd\(s\).*$', evidence_text, re.M)
    if nocache: field('Page-cache policy', 'F_NOCACHE on '+nocache.group(1)+' descriptors','engine',nocache.group(0))
    elif 'DS4_MODEL_FD_NOCACHE' in requested: field('F_NOCACHE',requested['DS4_MODEL_FD_NOCACHE'],'requested','DS4_MODEL_FD_NOCACHE='+requested['DS4_MODEL_FD_NOCACHE'])
    for label,pattern in [
        ('Pieces per descriptor',r'^.*expert pread per-fd sub-pieces: ([\d,]+).*$'),
        ('Lookahead experts',r'^.*router-lookahead prefetch on: up to (\d+) experts.*$'),
        ('RAM expert cache GiB',r'^.*metal SSD streaming cache target .*? ([\d.]+) GiB dynamic cache.*$'),
        ('Reader threads',r'^\[config\] resolved:.*?\bexpert_read_threads=(\d+).*$')]:
        if m := re.search(pattern,evidence_text,re.M):
            fields[:] = [f for f in fields if f['label']!=label]
            field(label,m.group(1),'engine',m.group(0))
    # These declarations are observations, never inferred from requested flags.
    for label, pattern in [
        ('Engram reader workers', r'^ds4: Argodrive Engram readers=(\d+)$'),
        ('Primary expert cache policy', r'^ds4: Argodrive primary expert descriptor (F_NOCACHE=1; mmap/Engram descriptor unchanged)$')]:
        if m := re.search(pattern, evidence_text, re.M):
            field(label, m.group(1), 'engine', m.group(0))
    transfers = list(re.finditer(r'^ds4: Argodrive decode cache requested=(\d+) effective=(\d+) reserve_bytes=(\d+); total allowance unchanged$', evidence_text, re.M))
    if transfers:
        for f in fields:
            if f['label'] == 'RAM expert cache GiB': f['label'] = 'Initial RAM expert cache GiB'
        last = transfers[-1]
        field('Decode cache expert slots', last.group(2), 'engine', last.group(0))
        field('Decode prefill reserve bytes', last.group(3), 'engine', last.group(0))
        field('Cache phase transfers recorded', len(transfers), 'engine', 'Count of successful decode cache transfer declarations; not a current live allocation.')
    for m in re.finditer(r'^ds4: Argodrive source\[(\d+)\] bytes=(\d+)$', evidence_text, re.M):
        field('Source '+m.group(1)+' application bytes (whole arm)', m.group(2), 'engine', m.group(0))
    homes=[]
    if config.get('model'): homes.append({'role':'Primary model','path':config['model'],'weight':requested.get('DS4_MODEL_PRIMARY_WEIGHT'),'source':'requested'})
    confirmed = list(re.finditer(r'^.*model replica: (.*?) weight=(\d+)\s*$',evidence_text,re.M))
    if confirmed:
        homes += [{'role':'Replica','path':m.group(1),'weight':m.group(2),'source':'engine'} for m in confirmed]
    elif requested.get('DS4_MODEL_REPLICAS'):
        for item in requested['DS4_MODEL_REPLICAS'].split(','):
            parts=item.rsplit('*',1)
            homes.append({'role':'Replica','path':parts[0],'weight':parts[1] if len(parts)==2 else None,'source':'requested'})
    for key in ('DELTAFIN_ROOT','K3_EXPERT_DIR_B','K3_EXPERT_DIR_C','K3_EXPERT_HOT_DIR'):
        if key in requested: homes.append({'role':key,'path':requested[key],'weight':None,'source':'requested'})
    declarations = [l for l in evidence_text.splitlines() if l.startswith('[native]') or
                    (l.startswith('ds4:') and any(k in l for k in ('replica reads:', 'replica telemetry:', 'streaming cache target','SSD streaming mode enabled')))]
    return {'method':method,'status':status,'fields':fields,'homes':homes,
            'engine_declarations':declarations[:20], 'requested':requested,
            'note':'Saved arm evidence. Requested settings do not prove the engine applied them. File descriptors can refer to the same physical drive.'}


def engine_header(path):
    """Bounded read of the startup evidence beside a harness log."""
    for suffix in ('.err', '.engine.txt'):
        try:
            with Path(path).with_suffix(suffix).open('r',errors='replace') as f:
                return f.read(131072)
        except OSError:
            continue
    return ''



def arm_artifacts(path):
    p=Path(path)
    kinds={'.log':'Arm log','.err':'Engine declarations','.engine.txt':'Engine declarations','.task.json':'Task status','.csv':'Drive counters','.sys':'Memory samples',
           '.map':'Recorded device map','.txt':'Output text','.md5':'Output fingerprint',
           '.chunks':'Response timing','.capture.json':'Capture summary','.readtrace.csv':'Read trace',
           '.hotlist':'Expert profile','.expert.json':'Expert profile','.jsonl':'Router trace'}
    out=[]
    for ext,kind in kinds.items():
        f=p.with_suffix(ext)
        try:
            s=f.stat()
            if f.is_file():out.append({'name':f.name,'path':str(f),'kind':kind,'bytes':s.st_size})
        except OSError:pass
    return out
