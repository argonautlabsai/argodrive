import {mountSSDTuner} from './ssd-tuner-view.js';

export function benchmarkView(){
  return `<div class="benchmark-hero">
    <div><span class="eyebrow">HARDWARE-AWARE TESTING</span><h2>Find the best way to stream your model</h2><p>Argodrive measures each SSD, shared hubs and concurrency, then proposes a reproducible streaming profile for your engine.</p></div>
    <div class="benchmark-hero-badge"><strong>Read only</strong><span>No RAID, writes or file changes</span></div>
  </div>
  <div class="benchmark-steps" aria-label="Benchmark steps">
    <div class="benchmark-step"><span>01</span><div><strong>Measure</strong><p>Drive throughput, latency and shared links</p></div></div>
    <div class="benchmark-step"><span>02</span><div><strong>Recommend</strong><p>Weights, queue depth and reader candidates</p></div></div>
    <div class="benchmark-step"><span>03</span><div><strong>Verify</strong><p>Optional short model run before promotion</p></div></div>
  </div>
  <div id="benchmark-tuner"></div>
  <div class="two-col benchmark-info-grid">
    <section class="panel benchmark-estimate-card"><div class="panel-head"><div><span class="eyebrow">MODEL CHECK</span><h2>Token speed estimate</h2><p>Measured only after a verification run.</p></div><span class="badge">Not measured</span></div><div class="panel-body"><div class="benchmark-estimate-value">— <small>tok/s</small></div><p class="info-note">Drive speed alone cannot predict generation speed reliably. After calibration, run a short model check to record a byte-identical tok/s result for the selected model and profile.</p><a class="button" href="#streaming">Set up model verification</a></div></section>
    <section class="panel"><div class="panel-head"><div><h2>What the estimate means</h2><p>Storage calibration is a predictor, not a token benchmark.</p></div></div><div class="panel-body"><p class="info-note">The test measures the storage side of the workload. Exact generation speed also depends on GPU kernels, Engram, cache residency and the selected model.</p><ul class="benchmark-list"><li>Drive-only results show GB/s, latency and a candidate split.</li><li>Use the model verification step for measured tok/s.</li><li>Promote settings only after matching 128- and 512-token runs.</li></ul></div></section>
    <section class="panel"><div class="panel-head"><div><h2>Next step</h2><p>Turn a candidate into evidence.</p></div></div><div class="panel-body"><p class="info-note">After calibration, open Streaming → Models & readiness to check the model and then run a short DS4 verification arm. The benchmark never applies settings automatically.</p><div class="button-group"><a class="button" href="#streaming">Open streaming setup</a><a class="button subtle" href="#runs">Review saved runs</a></div></div></section>
  </div>`;
}

export function mountBenchmark(container,host){
  container.innerHTML=benchmarkView();
  const target=container.querySelector('#benchmark-tuner');
  mountSSDTuner(target,host);
  const draft=host.settings?.engine_draft;
  if(!draft)return;
  const form=target.querySelector('#ssd-tuner-form');
  if(!form)return;
  const paths=(draft.sources||[]).map(s=>s.path).filter(Boolean);
  if(paths.length)form.elements.paths.value=paths.join('\n');
  if(draft.modelFamily&&form.elements.model_family)form.elements.model_family.value=draft.modelFamily==='deepseek41'?'deepseek41':draft.modelFamily;
  if(draft.build&&form.elements.engine_build)form.elements.engine_build.value=draft.build==='argonaut'?'argonaut':'upstream';
}
