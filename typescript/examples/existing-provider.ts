import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { createSpanProcessor } from '@/otel';

const exporter = new InMemorySpanExporter();
const provider = new NodeTracerProvider({
  // Pass exportAllSpans: true to also export spans without Confident/GenAI data.
  spanProcessors: [createSpanProcessor({ exporter })],
});
provider.register();
const tracer = provider.getTracer('application');
tracer.startActiveSpan('request', (request) => {
  tracer
    .startSpan('chat', { attributes: { 'gen_ai.operation.name': 'chat' } })
    .end();
  tracer.startSpan('cache lookup').end();
  request.end();
});
await provider.forceFlush();
// ['chat', 'request']: the plain lookup is dropped; the request is kept as an ancestor.
console.log(exporter.getFinishedSpans().map((span) => span.name));
// The application, not confident-trace.shutdown(), owns this lifecycle.
await provider.shutdown();
