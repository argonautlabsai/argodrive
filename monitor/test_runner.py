"""Explicit, single-arm local benchmark control. Never adopts another process."""
import atexit
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import stat
import sys
import threading
import time
import uuid

CONTEXTS = [1024, 2048, 4096, 8192, 16384, 32768]
TOKENS = [40, 60, 100, 128, 200, 512]
BUSY = {'preflight', 'running', 'stopping'}
CAMPAIGN_LOCK = Path('/tmp/argodrive-glm-campaign.lock')
HARNESS_FILES = ['tools/harness/glm-arm.sh', 'tools/harness/glm-capture.py',
                 'tools/harness/glm-summarize.py', 'tools/bin/k3-diskscope',
                 'tools/bin/k3-pressure', 'tools/argodrive/monitor/k3-memsample.sh']


def atomic(path, value):
    path = Path(path)
    # Only app-owned settings/status files are replaceable. Never follow aliases.
    if path.is_symlink(): raise ValueError('Refusing to replace a symbolic link: '+str(path))
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Refusing to replace a linked or non-regular file: '+str(path))
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with tmp.open('x') as f:
        f.write(json.dumps(value, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)



def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()


def verify_harness(profile):
    expected = profile.get('harness_sha256', {})
    if set(expected) != set(HARNESS_FILES):
        raise ValueError('Benchmark helpers need a verified local profile before testing.')
    for relative in HARNESS_FILES:
        if digest(Path(profile['project'])/relative) != expected[relative]:
            raise ValueError('Benchmark helper changed since profile verification: '+relative)


def disk_identity(path):
    p = Path(path)
    if not p.is_file(): raise ValueError('Model replica is missing: '+str(p))
    mount = p.parent
    while not os.path.ismount(mount) and mount != mount.parent: mount = mount.parent
    try:
        info = plistlib.loads(subprocess.check_output(['/usr/sbin/diskutil', 'info', '-plist', str(mount)], timeout=8))
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        raise ValueError('Cannot inspect the selected drive: '+str(mount)) from e
    stores = info.get('APFSPhysicalStores', [])
    if len(stores) != 1: raise ValueError('A single physical SSD per replica is required.')
    physical = re.sub(r's\d+$', '', stores[0]['APFSPhysicalStore'])
    if not re.fullmatch(r'disk\d+', physical): raise ValueError('Cannot identify physical SSD.')
    st = p.stat()
    return {'path': str(p.resolve()), 'volume_uuid': info.get('VolumeUUID') or info.get('DiskUUID'),
            'physical_device': physical, 'device_tree': info.get('DeviceTreePath'),
            'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'inode': st.st_ino}


def validate_request(body, profiles):
    if not isinstance(body, dict) or set(body) != {'model', 'drives', 'context', 'tokens'}:
        raise ValueError('Select a model, drives, context and output length.')
    profile = next((p for p in profiles if p['id'] == body['model']), None)
    if not profile or not profile.get('enabled'): raise ValueError('This model has no enabled launch profile.')
    drives = body['drives']
    if not isinstance(drives, list) or not drives or len(drives)>5 or any(not isinstance(d,str) for d in drives) or len(set(drives))!=len(drives):
        raise ValueError('Select one to five different drives.')
    if not set(drives) <= {d['id'] for d in profile['drives']}: raise ValueError('Unknown drive selection.')
    if type(body['context']) is not int or body['context'] not in CONTEXTS: raise ValueError('Unsupported context capacity.')
    if type(body['tokens']) is not int or body['tokens'] not in TOKENS: raise ValueError('Unsupported output length.')
    selected = [d for d in profile['drives'] if d['id'] in drives]
    return profile, selected


def guard_idle():
    p = subprocess.run(['ps', '-A', '-o', 'pid=,comm='], capture_output=True, text=True, timeout=8, check=True)
    if not p.stdout.strip(): raise ValueError('Cannot verify whether the engine is idle.')
    for line in p.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields)==2 and re.fullmatch(r'(ds4(?:[-.].*)?|deltafin)', Path(fields[1]).name):
            raise ValueError('Another inference process is active. Wait for it to finish.')


class TestRunner:
    def __init__(self, state_dir, command, inventory=None):
        self.directory = Path(state_dir)
        self.command = command
        # Optional live hardware inventory. It explains why a mounted SSD is
        # unavailable for a model without making an unverified replica usable.
        self.inventory = inventory
        self.lock = threading.RLock()
        self.process = None
        self.state = {'status':'idle', 'owned':False}

    def profiles(self):
        p = self.directory/'test-profiles.json'
        return json.loads(p.read_text()).get('profiles', []) if p.is_file() else []

    def options(self):
        profile_data = {}
        profile_file = self.directory/'test-profiles.json'
        if profile_file.is_file():
            try:
                profile_data = json.loads(profile_file.read_text())
            except (OSError, ValueError):
                profile_data = {}
        models=[]
        for p in profile_data.get('profiles', self.profiles()):
            drives=[]
            configured=set()
            for d in p.get('drives', []):
                configured.add(d['id'])
                ready=Path(d['path']).is_file() and bool(d.get('volume_uuid'))
                drives.append({'id':d['id'], 'label':d['label'], 'ready':ready, 'weight':d['weight'],
                               'reason':'' if ready else 'Model replica not mounted'})
            # Keep every detected physical drive visible in Test setup. A
            # drive absent from this model's profile is deliberately disabled,
            # with the reason shown beside it instead of silently disappearing.
            if self.inventory is not None:
                try: hardware=list(self.inventory() or [])
                except Exception: hardware=[]
                for d in hardware:
                    ident=d.get('id')
                    if ident and ident not in configured:
                        drives.append({'id':ident, 'label':d.get('label') or ident,
                                       'ready':False, 'weight':None,
                                       'reason':'No matching model replica registered'})
            ready=p.get('enabled',False) and Path(p.get('engine','')).is_file() and shutil.which('python3') is not None
            env=p.get('environment') or {}
            try: read_threads=int(env.get('DS4_METAL_STREAMING_EXPERT_PREAD_THREADS', 9 if p.get('kind') == 'ds41' else 48))
            except (TypeError, ValueError): read_threads=9 if p.get('kind') == 'ds41' else 48
            # ds4's DeepSeek reader pool accepts 1–18 workers and clamps
            # larger requests; show the effective value in Test setup.
            if p.get('kind') == 'ds41':
                read_threads=min(18, max(1, read_threads))
            models.append({'id':p['id'],'label':p['label'],'engine':p.get('engine_label','ds4'),
                           'kind':p.get('kind','glm'),'read_threads':read_threads,
                           'read_method':'expert split reads' if len(p.get('drives', []))>1 else 'single-source',
                           'read_ahead':'off' if env.get('DS4_METAL_DISABLE_STREAMING_EXPERT_READAHEAD') == '1' else 'configured',
                           'ready':bool(ready),'reason':p.get('reason','Launch profile unavailable') if not ready else '', 'drives':drives})
        for ident,label in [('deepseek41','DeepSeek V4.1 Flash'),('kimi3','Kimi K3')]:
            if not any(m['id']==ident for m in models):
                models.append({'id':ident,'label':label,'ready':False,'reason':'No qualified launch profile','drives':[]})
        # DeepSeek V4.1 is the current Argodrive focus and has the verified
        # three-drive profile. Keep the preference explicit, but fall back to
        # the first ready profile on machines that do not have it installed.
        preferred = profile_data.get('default_model', 'deepseek41')
        ready_ids = {m['id'] for m in models if m.get('ready')}
        default_model = preferred if preferred in ready_ids else next(iter(ready_ids), None)
        # Put the preferred model first as well as marking it as selected in
        # the UI. This keeps older clients (which select the first option)
        # aligned with the backend default.
        if default_model:
            models.sort(key=lambda m: (m['id'] != default_model, m['label']))
        return {'models':models,'contexts':CONTEXTS,'tokens':TOKENS,'default_context':4096,'default_tokens':100,
                'default_model':default_model}

    def status(self):
        with self.lock:
            result=dict(self.state)
            if self.state.get('folder'):
                try: result.update(json.loads((Path(self.state['folder'])/'test-status.json').read_text()))
                except (OSError, ValueError): pass
            if self.process is not None:
                rc=self.process.poll()
                if rc is not None and result['status'] in BUSY:
                    result.update(status='failed', error='Test worker exited before recording completion.', returncode=rc)
                result['owned']=rc is None
            if self.state.get('stop_requested') and result['status'] in BUSY: result['status']='stopping'
            self.state=result
            return dict(result)

    def start(self, body, runs):
        with self.lock:
            if self.process is not None and self.process.poll() is None: raise ValueError('A Monitor test is already running.')
            profile,selected=validate_request(body,self.profiles())
            if not Path(profile['engine']).is_file(): raise ValueError('Engine binary is missing.')
            identities=[disk_identity(d['path']) for d in selected]
            if len({d['physical_device'] for d in identities})!=len(identities): raise ValueError('Selected replicas must be on different physical SSDs.')
            if any(i['volume_uuid']!=d['volume_uuid'] for i,d in zip(identities,selected)):
                raise ValueError('Drive identity changed. Review the local launch profile before testing.')
            if len({i['size'] for i in identities})!=1: raise ValueError('Replica sizes differ.')
            try: guard_idle()
            except subprocess.SubprocessError as e: raise ValueError('Cannot verify the process guard.') from e
            runs=Path(runs).resolve()
            if not runs.is_dir(): raise ValueError('Choose an existing run library folder first.')
            ident=uuid.uuid4().hex[:12]
            folder=runs/('monitor-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+ident)
            folder.mkdir()
            request={'id':ident,'profile':profile,'selection':body,'drives':selected,'identities':identities,'folder':str(folder)}
            atomic(folder/'test-request.json',request)
            state={'id':ident,'status':'preflight','folder':str(folder),'owned':True,'model':profile['label'],
                   'drives':[d['label'] for d in selected], 'context':body['context'],'tokens':body['tokens'], 'publication_ready':False}
            atomic(folder/'test-status.json',state)
            self.state=state
            try:
                with (folder/'test-worker.out').open('w') as log:
                    self.process=subprocess.Popen(self.command+[str(folder/'test-request.json')],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            except OSError as e:
                self.state.update(status='failed',owned=False,error=str(e));atomic(folder/'test-status.json',self.state);raise
            return self.status()

    def stop(self, ident):
        with self.lock:
            if ident != self.state.get('id'): raise ValueError('This is not the test owned by this Monitor.')
            if self.process is not None and self.process.poll() is None:
                self.process.send_signal(signal.SIGTERM)
                self.state.update(status='stopping',stop_requested=True)
                threading.Thread(target=self._wait_stop, args=(self.process,), daemon=True).start()
            return self.status()

    def _wait_stop(self, process):
        # Worker traps TERM and cleans its own engine/sampler group before exiting.
        try: process.wait(timeout=20)
        except subprocess.TimeoutExpired: pass  # Never infer ownership of an arbitrary PID from disk.

    def shutdown(self):
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            self._wait_stop(self.process)


def stop_group(process):
    if process is None: return
    try: os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()


def run_ds41_profile(request, out, state):
    """Run a verified DeepSeek V4.1 profile through the native ds4 harness.

    The regular Monitor test path is intentionally GLM-harness-specific. Keep
    the DeepSeek path separate so a GLM script can never be pointed at a
    DeepSeek model by accident.
    """
    import ds41_benchmark
    profile = request['profile']
    selection = request['selection']
    drives = request['drives']
    if len(drives) > 3:
        raise ValueError('The DeepSeek V4.1 profile supports at most three replicas.')
    prompt_tokens = int(profile.get('prompt_tokens', 512))
    # TestRunner creates the Monitor run directory before starting the worker.
    # ds41_benchmark deliberately refuses to reuse an existing evidence root,
    # so keep the benchmark artefacts in a fresh child directory while the
    # parent remains the app-owned lifecycle/status folder.
    evidence = out/'evidence'
    plan = ds41_benchmark.plan(
        profile['engine'], drives[0]['path'], profile['prompt'], evidence,
        prompt_tokens=prompt_tokens, output_tokens=int(selection['tokens']))
    plan['context_allocation'] = int(selection['context'])
    plan['method'] = 'expert split reads' if len(drives) > 1 else 'single-source'
    plan['replica_streaming'] = len(drives) > 1
    replicas = [d['path'] for d in drives[1:]]
    state.update(status='running', arm='monitor-deepseek41-'+str(selection['tokens']))
    atomic(out/'test-status.json', state)
    result = ds41_benchmark.run(
        plan,
        timeout=int(profile.get('timeout', 600)),
        replicas=replicas,
        verification_receipt=profile.get('verification_receipt'),
        primary_weight=int(drives[0].get('weight', 2)),
        sampler_binary=profile.get('sampler'),
        devices=profile.get('devices') or {},
        experimental_env=profile.get('environment') or {},
        max_swap_growth_mb=int(profile.get('max_swap_growth_mb', 512)),
        lock_held=True)
    state.update(status='complete' if result.get('status') == 'complete' else 'failed',
                 summary=result.get('result', {}),
                 output_sha256=result.get('result', {}).get('output_sha256'),
                 errors=[] if result.get('status') == 'complete' else [result.get('error', 'DeepSeek run failed')])
    atomic(out/'test-status.json', state)


def worker_main(request_path):
    request=json.loads(Path(request_path).read_text()); out=Path(request['folder'])
    state=json.loads((out/'test-status.json').read_text()); process=None
    def cancel(*_): raise KeyboardInterrupt('Stopped from Monitor')
    signal.signal(signal.SIGTERM,cancel);signal.signal(signal.SIGINT,cancel)
    try:
        with CAMPAIGN_LOCK.open('a+') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise ValueError('Another Argodrive campaign owns the benchmark lock.')
            guard_idle()
            p=request['profile']; selection=request['selection'];drives=request['drives']
            if digest(p['engine'])!=p['engine_sha256']: raise ValueError('Engine binary changed since profile verification.')
            if [disk_identity(d['path']) for d in drives]!=request['identities']: raise ValueError('Drive or model changed after Start.')
            if p.get('kind') == 'ds41':
                run_ds41_profile(request, out, state)
                return 0
            verify_harness(p)
            runtime=out/'runtime';engine=runtime/'engine';engine.mkdir(parents=True)
            shutil.copy2(p['engine'],engine/'ds4');shutil.copytree(Path(p['engine']).parent/'metal',engine/'metal')
            project=runtime/'harness'
            for relative in HARNESS_FILES:
                src=Path(p['project'])/relative;dst=project/relative;dst.parent.mkdir(parents=True,exist_ok=True)
                before=digest(src);shutil.copy2(src,dst)
                if before!=p['harness_sha256'][relative] or before!=digest(dst) or before!=digest(src): raise ValueError('Harness changed during snapshot.')
            harness=project/'tools/harness/glm-arm.sh'
            source=harness.read_text();old='$(cd "$(dirname "$GLM_DS4")" && git rev-parse --short HEAD 2>/dev/null)'
            if old not in source: raise ValueError('Harness engine identity format changed.')
            harness.write_text(source.replace(old,'binary-sha256=$GLM_ENGINE_SHA256'))
            # Validate selected volume UUIDs and model file identities inside the frozen harness.
            mapper=project/'tools/drives/k3-drive-map.py';mapper.parent.mkdir(parents=True,exist_ok=True)
            entry=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('k3-live.py'))]
            mapper.write_text('import subprocess\nsubprocess.run('+repr(entry+['--test-drive-check',str(Path(request_path).resolve())])+',check=True)\n')
            weights=[d['weight'] for d in drives];pieces=[2 if d['id']=='internal' else 1 for d in drives]
            env={k:v for k,v in os.environ.items() if not k.startswith(('DS4_','GLM_'))}
            env.update(p['environment'])
            for k in list(env):
                if k.startswith('DS4_ARGODRIVE_') or k in ('DS4_MODEL_REPLICAS','DS4_MODEL_REPLICA_INFLIGHT'):env.pop(k)
            env.update(DS4_MODEL_PRIMARY_WEIGHT=str(weights[0]),DS4_MODEL_REPLICA_PIECES_FD=','.join(map(str,pieces)),
                       DS4_MODEL_FD_NOCACHE='1')
            if len(drives)>1:env['DS4_MODEL_REPLICAS']=','.join(d['path']+'*'+str(d['weight']) for d in drives[1:])
            env.update(GLM_DS4=str(engine/'ds4'),GLM_MODEL=drives[0]['path'],GLM_CTX=str(selection['context']),
                       GLM_CACHE='70GB' if selection['context']<=4096 else 'auto',GLM_RAW='1',GLM_TEMP='0',GLM_COLD='1',
                       GLM_MTP='0',GLM_NOSEED='1',GLM_READAHEAD='0',GLM_EVICT_LRU='1',GLM_FULL_LAYER_PREFILL='0',
                       GLM_PRESSURE_GB='0',GLM_SCOPE_MS='100',GLM_TRACE='0',GLM_LAYER_STATS='1',GLM_ALLOW_WARM='0',
                       GLM_ALLOW_SWAP='1',GLM_SKIP_MAP='0',GLM_ENGINE_SHA256=p['engine_sha256'])
            tag='monitor-'+p['id']+'-'+str(selection['tokens'])
            argv=['sh',str(harness),str(out),tag,str(selection['tokens']),p['prompt']]
            atomic(out/'plan.json',{'selection':selection,'drives':drives,'weights':weights,'pieces':pieces,'prompt':p['prompt'],
                                  'environment':{k:v for k,v in env.items() if k.startswith(('GLM_','DS4_'))},'argv':argv,'publication_ready':False})
            manifest={str(f.relative_to(runtime)):digest(f) for f in runtime.rglob('*') if f.is_file()}
            atomic(out/'runtime-hashes.json',manifest)
            guard_idle()
            with (out/(tag+'.runner.out')).open('w') as log:
                process=subprocess.Popen(argv,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                state.update(status='running',arm=tag);atomic(out/'test-status.json',state)
                try: rc=process.wait(timeout=600)
                finally: stop_group(process);process=None
            err=(out/(tag+'.err')).read_text(errors='replace')
            counts=re.findall(r'generation counts: generated=(\d+) requested=(\d+) decode_seconds=([\d.eE+-]+)',err)
            failures=[]
            if rc:failures.append('Harness exit '+str(rc))
            if not counts or any(int(v)!=selection['tokens'] for v in counts[-1][:2]):failures.append('Actual token count did not match request.')
            summaries=[json.loads(l) for l in (out/(tag+'.log')).read_text().splitlines() if l.startswith('{"tag"')]
            summary=summaries[-1] if summaries else {}
            if len(drives)>1:
                expected=f'expert pread striping across {len(drives)} fds, {sum(weights)} weighted slots (primary weight {weights[0]}, mode=split-read)'
                if expected not in err:failures.append('Engine did not confirm requested drive striping.')
                for d in drives[1:]:
                    if f"ds4: model replica: {d['path']} weight={d['weight']}\n" not in err:failures.append('Replica source not confirmed: '+d['label'])
            if not re.search(r'F_NOCACHE set on '+str(len(drives))+r' model fd',err):failures.append('Uncached reads were not confirmed.')
            if summary.get('valid')!='ok' or summary.get('swap_growth_mb',2)>1:failures.append('Harness validity or swap-growth guard failed.')
            if [disk_identity(d['path']) for d in drives]!=request['identities']:failures.append('Drive or model changed during test.')
            if any(digest(runtime/name)!=h for name,h in manifest.items()):failures.append('Frozen runtime changed.')
            seconds=float(counts[-1][2]) if counts else 0
            if not math.isfinite(seconds) or seconds<=0:failures.append('Invalid engine timing.');seconds=0
            sha=digest(out/(tag+'.txt'))
            reference=p.get('references',{}).get(str(selection['tokens'])) if selection['context']==4096 else None
            match=None
            if reference and Path(reference).is_file():
                match=sha==digest(reference)
                if not match:failures.append('Output differs from fixed-prompt reference.')
            state.update(status='failed' if failures else 'complete',errors=failures,summary=summary,
                         decode_tok_s=selection['tokens']/seconds if seconds>0 else None,output_sha256=sha,output_matches_reference=match)
    except KeyboardInterrupt:
        state.update(status='stopped',error='Stopped from Monitor; partial run retained.')
    except BaseException as e:
        state.update(status='failed',error=str(e))
    finally:
        # Ignore repeated TERM while finishing ownership cleanup.
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        stop_group(process)
        state.update(owned=False,finished_at=datetime.now(timezone.utc).isoformat())
        atomic(out/'test-status.json',state)
    return 0 if state['status']=='complete' else 1
