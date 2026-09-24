/* global Response, require */
/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS acceptance fixture. */
// Exercise the plugin's own OpenAI 6 client; never manually instrument a client.
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { dirname, join } = require('node:path');
const { pathToFileURL } = require('node:url');
(async () => {
  const load =
    process.env.LIVEKIT_MODULE_MODE === 'esm'
      ? async (name) => globalThis.livekitTestModules[name]
      : async (name) => require(name);
  const { init } = await load('confident-trace');
  const { InMemorySpanExporter, SimpleSpanProcessor } = await load(
    '@opentelemetry/sdk-trace-base',
  );
  const { NodeTracerProvider } = await load('@opentelemetry/sdk-trace-node');
  const agents = await load('@livekit/agents');
  const { LLM } = await load('@livekit/agents-plugin-openai');
  const pluginRequire = createRequire(
    require.resolve('@livekit/agents-plugin-openai'),
  );
  assert.match(
    JSON.parse(
      require('node:fs').readFileSync(
        join(dirname(pluginRequire.resolve('openai')), 'package.json'),
        'utf8',
      ),
    ).version,
    /^6\./,
  );
  const separate = process.env.LIVEKIT_PROVIDER === 'separate';
  const elsewhere = new InMemorySpanExporter();
  const other = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(elsewhere)],
  });
  if (separate) agents.telemetry.setTracerProvider(other);
  const sink = new InMemorySpanExporter();
  const rt = init({ exporter: sink, instrumentations: ['livekit', 'openai'] });
  agents.initializeLogger({ pretty: false, level: 'error' });
  let fail = false;
  let tool = false;
  globalThis.fetch = async () => {
    if (fail)
      return Response.json(
        { error: { message: 'offline failure', type: 'server_error' } },
        { status: 500 },
      );
    const base = {
      id: 'chat-test',
      object: 'chat.completion.chunk',
      created: 0,
      model: 'gpt-4.1-mini',
    };
    const chunks = [
      {
        ...base,
        choices: [
          {
            index: 0,
            delta: tool
              ? {
                  role: 'assistant',
                  tool_calls: [
                    {
                      index: 0,
                      id: 'call_1',
                      type: 'function',
                      function: {
                        name: 'weather',
                        arguments: '{"city":"Paris"}',
                      },
                    },
                  ],
                }
              : { role: 'assistant', content: 'Hello' },
            finish_reason: null,
          },
        ],
      },
      {
        ...base,
        choices: [
          { index: 0, delta: {}, finish_reason: tool ? 'tool_calls' : 'stop' },
        ],
      },
      {
        ...base,
        choices: [],
        usage: { prompt_tokens: 7, completion_tokens: 3, total_tokens: 10 },
      },
    ];
    return new Response(
      chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join('') +
        'data: [DONE]\n\n',
      { headers: { 'content-type': 'text/event-stream' } },
    );
  };
  const model = new LLM({ apiKey: 'offline', model: 'gpt-4.1-mini' });
  const chatCtx = agents.llm.ChatContext.empty();
  chatCtx.addMessage({ role: 'user', content: 'Hi' });
  let modelError;
  model.on('error', (ev) => {
    modelError = ev.error;
  });
  const run = async () => {
    modelError = undefined;
    const stream = model.chat({
      chatCtx,
      connOptions: { maxRetry: 0, retryIntervalMs: 0, timeoutMs: 1000 },
    });
    try {
      for await (const chunk of stream) void chunk;
    } finally {
      stream.close();
    }
    if (modelError) throw modelError;
  };
  await run();
  // Real lifecycle: callbacks may finish spans concurrently; no manual flush afterward.
  const file = join(
    dirname(require.resolve('@livekit/agents')),
    'job_lifecycle' +
      (process.env.LIVEKIT_MODULE_MODE === 'esm' ? '.js' : '.cjs'),
  );
  const lifecycle =
    process.env.LIVEKIT_MODULE_MODE === 'esm'
      ? await import(pathToFileURL(file))
      : require(file);
  const logger = { error() {}, warn() {}, debug() {} };
  await lifecycle.runShutdownCallbacks(
    [
      async () => {
        throw new Error('cleanup failure');
      },
      async () => {
        await new Promise((r) => setTimeout(r, 10));
        rt.getTracer().startSpan('late_cleanup').end();
      },
    ],
    logger,
  );
  await lifecycle.flushJobLogs(logger);
  const spans = sink.getFinishedSpans();
  assert.ok(spans.some((s) => s.name === 'late_cleanup'));
  const models = spans.filter(
    (s) => s.attributes['gen_ai.operation.name'] === 'chat',
  );
  assert.equal(models.length, 1);
  assert.equal(
    models[0].instrumentationScope.name,
    separate ? 'confident-trace' : 'livekit-agents',
  );
  assert.equal(models[0].attributes['gen_ai.usage.input_tokens'], 7);
  assert.equal(models[0].attributes['gen_ai.usage.output_tokens'], 3);
  if (separate) {
    assert.equal(agents.telemetry.tracer.getProvider(), other);
    assert.ok(
      elsewhere
        .getFinishedSpans()
        .some((s) => s.attributes['gen_ai.operation.name'] === 'chat'),
    );
  }
  tool = true;
  await run();
  await lifecycle.flushJobLogs(logger);
  assert.equal(
    sink
      .getFinishedSpans()
      .filter((s) => s.attributes['gen_ai.operation.name'] === 'chat').length,
    2,
  );
  assert.ok(
    sink
      .getFinishedSpans()
      .some((s) => JSON.stringify(s.attributes).includes('weather')),
  );
  fail = true;
  await assert.rejects(run());
  await lifecycle.flushJobLogs(logger);
  assert.ok(sink.getFinishedSpans().some((s) => s.status.code === 2));
  await rt.shutdown();
  await other.shutdown();
  console.log(
    'LiveKit real plugin, provider ownership and late cleanup passed',
  );
  // Match LiveKit worker termination: exit without letting the batch timer save us.
  process.exit(0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
