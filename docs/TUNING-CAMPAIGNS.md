# Tuning campaigns

Streaming → Tuning campaigns reads the saved results of the development GLM campaign runners. It shows the current stage and arm, completed-arm counts, settings retained or rejected by matched comparisons, and final 128/512-token qualification. The five-token target is checked at both lengths. An incomplete screen cannot turn the target badge green.

The view is read-only. It does not start inference, inspect processes, probe SSDs, change the champion or apply a settings file. The Run monitor continues to display SSD bars from the existing harness sampler. The campaign view refreshes every five seconds while visible; interactive controls keep keyboard focus. A stale recorded running state is labelled when its saved activity stops advancing.

Choose the GLM `arms` parent in Settings. Campaign folders contain `autotune.json`; their stage folders are siblings with names ending in `--01-layout-screen`, `--06-qualification`, or the corresponding refinement stage. The reader discovers up to ten recent campaigns and limits JSON sizes. Symlinks cannot redirect artifact reads outside the selected source folder.

The campaign overview uses recorded actual counts and precise engine timers, and checks their agreement with the saved rate, pair identities, output hashes and the historical reference. It requires a completed controller with three valid pairs at each length and one recorded engine binary identity before showing recorded qualification as passed. It does not repeat the raw-log audit or prove model-file identity. For that separate read-only audit, use `scripts/glm-evidence.py`.

The bars show engine-native generation rate, not the Run monitor's response-chunk rate. In the source-reviewed GLM loop used on 12 September, the first output comes from prefill logits; the evidence report separately gives derived `(N−1)/generation time`. The app does not generalize that convention to unknown engines.

“Kept for qualification” means a setting passed its screen, not that it was applied to a production engine. Export campaign report downloads JSON with the recorded comparison and candidate settings. It is not an executable launcher. Publication still needs model/quantization disclosure, checksum evidence, realistic prompts and review of the exported artifacts.

The module is included in the Mac build manifest, but adding it to source does not update an already downloaded beta. Restart a source preview or rebuild the app to load the new `/campaigns` reader and `/campaign-view.js` module. An older running backend displays an explicit unavailable message; its existing charts continue to work.

Validation: six backend tests cover incomplete/changed evidence, non-finite values, source changes, bounded reads and path containment; five UI tests cover target gating, selection, escaping and export semantics. A browser check is also required before delivering the rebuilt preview. No synthetic test fixture is benchmark evidence.
