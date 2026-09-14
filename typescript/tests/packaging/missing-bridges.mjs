import assert from 'node:assert/strict';
import Module from 'node:module';

// Simulate missing optional peers at the tracer's resolution boundary, while
// allowing the application's own SDK imports to resolve normally.
const resolve = Module._resolveFilename;
const missing = new Set(['@mastra/observability']);
const attempts = new Set();
Module._resolveFilename = function (name, parent, ...args) {
  if (missing.has(name) && parent?.filename?.endsWith('/register.mjs')) {
    attempts.add(name);
    const error = new Error(`Cannot find module '${name}'`);
    error.code = 'MODULE_NOT_FOUND';
    throw error;
  }
  return resolve.call(this, name, parent, ...args);
};
await import('confident-trace/register');
const { init } = await import('confident-trace');
const runtime = init();
try {
  await import('@mastra/core/mastra');
  assert.equal(runtime.active, true);
  assert.deepEqual(attempts, missing);
  const status = runtime.getInstrumentationStatus();
  assert.equal(status.integrations.mastra, 'failed');
} finally {
  Module._resolveFilename = resolve;
  await runtime.shutdown();
}
console.log(
  'Missing optional bridges leave init and application imports running',
);
