import test from 'node:test';
import assert from 'node:assert/strict';
import {benchmarkView} from '../monitor/benchmark-view.js';

test('benchmark view presents a safe three-step workflow',()=>{
  const html=benchmarkView();
  assert.match(html,/Find the best way to stream your model/);
  assert.match(html,/Read only/);
  assert.match(html,/Measure/);
  assert.match(html,/Recommend/);
  assert.match(html,/Verify/);
  assert.match(html,/No RAID, writes or file changes/);
});

test('benchmark view does not overpromise token speed from storage alone',()=>{
  const html=benchmarkView();
  assert.match(html,/Token speed estimate/);
  assert.match(html,/Not measured/);
  assert.match(html,/cannot predict generation speed reliably/);
  assert.doesNotMatch(html,/guaranteed|promise(?:s|d)?\s+tok\/s/i);
});
