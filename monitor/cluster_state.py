"""Read saved wire qualification evidence. This endpoint never contacts a peer."""
import json
from pathlib import Path


def pick(value, keys):
    if not isinstance(value,dict):return {}
    return {key:value[key] for key in keys.split() if key in value and isinstance(value[key],(str,int,float,bool,type(None)))}


def rows(value, keys):
    if not isinstance(value,list):return []
    return [pick(row,keys) for row in value[:100] if isinstance(row,dict)]


def strings(value):
    return [item for item in value[:30] if isinstance(item,str)] if isinstance(value,list) else []


def cluster_snapshot(state_dir, bundle_dir):
    local=Path(state_dir)/'wire'/'status.json'
    # The app ships only a schema-safe example. A development run may provide
    # a private report in state_dir/wire/status.json, but that file is never
    # copied into a public build.
    reference=Path(bundle_dir)/'cluster-evidence.example.json'
    # Keep older local development bundles readable; build-macos.py never
    # copies this legacy filename into a public app.
    legacy=Path(bundle_dir)/'cluster-evidence.json'
    if not reference.exists() and legacy.exists():
        reference=legacy
    source=local if local.exists() else reference
    result={'schema':1,'source':'local_session' if source==local else 'bundled_reference',
            'live':False,'engine_integrated':False,'gates':[],'s0':{},'s1':{},'node':{},'ssd_probe':{},
            'notice':'Saved development evidence. This page does not probe peers or enable remote inference.'}
    try:
        if source.stat().st_size>1024*1024:
            raise ValueError('Cluster evidence exceeds 1 MiB')
        data=json.loads(source.read_text(),parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite evidence value')))
        if not isinstance(data,dict) or data.get('schema')!=1:
            raise ValueError('Unsupported cluster evidence schema')
        # Explicit public fields: never expose secrets, manifests or arbitrary settings.
        result.update(pick(data,'captured_at'))
        result['node']=pick(data.get('node'),'name memory_gib os free_disk_gb inventory_source_sha256')
        result['network']=pick(data.get('network'),'connected_links independent_links planned_links transport nominal_gbps throughput_gbs latency_ms note')
        net=data.get('network_test') if isinstance(data.get('network_test'),dict) else {}
        result['network_test']=pick(net,'created_at method duration_seconds aggregate_method')
        result['network_test']['cases']=rows(net.get('cases'),'name label streams wall_gbs retransmits interface_check headline')
        expert=data.get('expert_test') if isinstance(data.get('expert_test'),dict) else {}
        result['expert_test']=pick(expert,'created_at method scope servers_stopped')
        result['expert_test']['cases']=rows(expert.get('cases'),'name verified requested wall_gbs identity_pass')
        pipeline=data.get('pipeline_test') if isinstance(data.get('pipeline_test'),dict) else {}
        result['pipeline_test']=pick(pipeline,'created_at method scope verified requested cache_implemented cache_limit_gib cache_peak_gib identity_pass servers_stopped confirmed_transfer_gbs confirmed_wall_gbs confirmed_gain_percent')
        result['pipeline_test']['profiles']=rows(pipeline.get('profiles'),'name repetitions transfer_gbs wall_gbs gain_percent cache_hit_rate cache_mib read_ahead headline')
        result['pipeline_test']['cases']=rows(pipeline.get('cases'),'name verified transfer_gbs wall_gbs cache_hit_rate cache_mib read_ahead status')
        inference=data.get('inference_test') if isinstance(data.get('inference_test'),dict) else {}
        result['inference_test']=pick(inference,'created_at status scope verdict local_tok_s remote_tok_s change_percent identity_pass remote_gets remote_bytes cache_hit_rate servers_stopped warmup_seconds experts cache_gib')
        result['inference_test']['cases']=rows(inference.get('cases'),'tag mode tokens decode_tok_s first_byte_s identity_pass remote_bytes')
        result['gates']=rows(data.get('gates'),'code title status detail')
        result['s1']=pick(data.get('s1'),'created_at stage scope identity requested verified unique_verified indexed bytes transfer_seconds wall_seconds transfer_gbs wall_gbs p50_ms p90_ms identity_pass failure performance_qualified host_buffer')
        sim=data.get('s0') if isinstance(data.get('s0'),dict) else {}
        result['s0']=pick(sim,'source source_sha256 tokens barriers created_at measured')
        result['s0']['scenarios']=rows(sim.get('scenarios'),'name links predicted_tok_s io_only_tok_s wire_gets node_hits')
        result['s0']['limitations']=strings(sim.get('limitations'))
        probe=data.get('ssd_probe') if isinstance(data.get('ssd_probe'),dict) else {}
        result['ssd_probe']=pick(probe,'scope created_at method')
        result['ssd_probe']['results']=rows(probe.get('results'),'streams application_gbs device_read_gbs device_to_application_ratio cold_qualified')
        result['limitations']=strings(data.get('limitations'))
    except (OSError,ValueError) as exc:
        result['error']=str(exc)
    return result
