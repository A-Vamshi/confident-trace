import assert from 'node:assert/strict';
import { init } from 'confident-trace';

// Each process owns one provider: shutdown is terminal in Node.
const runtime = process.argv.includes('grpc')
  ? init({ protocol: 'grpc', endpoint: 'http://localhost:4317' })
  : init();
assert.equal(runtime.active, true);
assert.equal(await runtime.shutdown(), true);
console.log('Base-only Node init and exporter startup passed');
