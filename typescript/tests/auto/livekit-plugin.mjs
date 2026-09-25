import * as confident from 'confident-trace';
import * as base from '@opentelemetry/sdk-trace-base';
import * as node from '@opentelemetry/sdk-trace-node';
import * as agents from '@livekit/agents';
import * as openai from '@livekit/agents-plugin-openai';

globalThis.livekitTestModules = {
  'confident-trace': confident,
  '@opentelemetry/sdk-trace-base': base,
  '@opentelemetry/sdk-trace-node': node,
  '@livekit/agents': agents,
  '@livekit/agents-plugin-openai': openai,
};
process.env.LIVEKIT_MODULE_MODE = 'esm';
await import('./livekit-plugin.cjs');
