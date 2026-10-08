import type * as PublicApi from '@/index';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inspect } from 'node:util';
import { context, trace, propagation } from '@opentelemetry/api';
import type { Attributes } from '@opentelemetry/api';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { InitOptions } from '@/config/types';

const WAV = Buffer.concat([
  Buffer.from('RIFF$\0\0\0WAVEfmt '),
  Buffer.alloc(64),
]);
const PNG = Buffer.from(
  '89504e470d0a1a0a0000000d4948445200000001000000010806000000',
  'hex',
);
const MARKER = /\[CONFIDENT:(?:IMAGE|PDF|AUDIO):([0-9a-f]{32})\]/g;
const SPAN_INPUT = 'confident.span.input';
const SPAN_OUTPUT = 'confident.span.output';
const SPAN_AUDIO = 'confident.span.audio';
const TRACE_AUDIO = 'confident.trace.audio';
const ATTACHMENTS = 'confident.span.attachments';

let api!: typeof PublicApi;
let exporter: InMemorySpanExporter;
// A runtime is initialised once per module load, so each policy gets a fresh one.
async function start(options: InitOptions = {}): Promise<void> {
  await (api as typeof PublicApi | undefined)?.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  api = await import('@/index');
  exporter = new InMemorySpanExporter();
  api.init({ exporter, ...options });
}
beforeEach(async () => {
  vi.stubEnv('OTEL_SDK_DISABLED', 'false');
  await start();
});
afterEach(async () => {
  await api.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});

async function exported(): Promise<Attributes> {
  await api.flush();
  const [span] = exporter.getFinishedSpans();
  return span!.attributes;
}
function attachments(row: Attributes): Record<string, Record<string, string>> {
  return JSON.parse(String(row[ATTACHMENTS]));
}
function markerIds(value: unknown): string[] {
  return [...String(value).matchAll(MARKER)].map((m) => m[1]!);
}
function media(data: Buffer, mimeType?: string) {
  return api.Media.fromBytes(data, mimeType)!;
}

it('writes a media value as a marker with its bytes attached', async () => {
  const audio = media(WAV, 'audio/wav');
  api.span({ name: 'call' }, () => api.updateSpan({ output: audio }))();
  const row = await exported();
  expect(JSON.parse(String(row[SPAN_OUTPUT]))).toBe(String(audio));
  const attachment = attachments(row)[audio.id]!;
  expect(attachment.mimeType).toBe('audio/wav');
  expect(Buffer.from(attachment.dataBase64!, 'base64')).toEqual(WAV);
  expect(String(row[SPAN_OUTPUT])).not.toContain(attachment.dataBase64);
});

it('attaches media formatted into a string', async () => {
  const image = media(PNG, 'image/png');
  api.span({ name: 'describe' }, () =>
    api.updateTrace({ input: `What is in this picture? ${image}` }),
  )();
  const row = await exported();
  expect(JSON.parse(String(row['confident.trace.input']))).toBe(
    `What is in this picture? [CONFIDENT:IMAGE:${image.id}]`,
  );
  expect(attachments(row)[image.id]!.mimeType).toBe('image/png');
});

it('shares one attribute between trace and span fields', async () => {
  const question = media(WAV, 'audio/wav');
  const pdf = media(PNG, 'application/pdf');
  api.span({ name: 'call' }, () => {
    api.updateTrace({ input: question });
    api.updateSpan({ metadata: { doc: pdf } });
  })();
  expect(Object.keys(attachments(await exported())).sort()).toEqual(
    [question.id, pdf.id].sort(),
  );
});

it('drops only what a rewritten field named', async () => {
  const kept = media(WAV, 'audio/wav');
  api.span({ name: 'call' }, () => {
    api.updateSpan({ input: kept, output: media(PNG, 'image/png') });
    api.updateSpan({ output: 'no media now' });
  })();
  expect(Object.keys(attachments(await exported()))).toEqual([kept.id]);
});

it('spends the budget once for media named twice', async () => {
  await start({ maxMediaBytes: WAV.length });
  const audio = media(WAV, 'audio/wav');
  api.span({ name: 'call' }, () =>
    api.updateSpan({ input: audio, output: `replying to ${audio}` }),
  )();
  const row = await exported();
  expect(markerIds(row[SPAN_INPUT])).toEqual([audio.id]);
  expect(markerIds(row[SPAN_OUTPUT])).toEqual([audio.id]);
  expect(Object.keys(attachments(row))).toEqual([audio.id]);
});

it('writes media over the item limit as a note', async () => {
  await start({ maxMediaBytes: WAV.length - 1 });
  api.span({ name: 'call' }, () =>
    api.updateSpan({ output: media(WAV, 'audio/wav') }),
  )();
  const row = await exported();
  expect(JSON.parse(String(row[SPAN_OUTPUT]))).toBe(
    '<inline_data: audio/wav, not captured>',
  );
  expect(row[ATTACHMENTS]).toBeUndefined();
});

it('attaches a remote reference as its url', async () => {
  const audio = api.Media.fromUri('https://example.com/call.mp3')!;
  api.span({ name: 'call' }, () => api.updateSpan({ output: audio }))();
  expect(attachments(await exported())[audio.id]).toEqual({
    url: 'https://example.com/call.mp3',
    mimeType: 'audio/mpeg',
  });
});

it('reads an audio file when the field is written', async () => {
  const path = join(mkdtempSync(join(tmpdir(), 'ct-')), 'turn.wav');
  writeFileSync(path, WAV);
  const audio = api.Media.fromFile(path)!;
  expect(audio.mimeType).toBe('audio/wav');
  api.span({ name: 'call' }, () => api.updateSpan({ output: audio }))();
  const attachment = attachments(await exported())[audio.id]!;
  expect(Buffer.from(attachment.dataBase64!, 'base64')).toEqual(WAV);
});

it.each(['video/mp4', 'text/csv', undefined])(
  'writes %s media as a note',
  async (mimeType) => {
    const value = media(WAV, mimeType);
    expect(String(value)).not.toMatch(MARKER);
    api.span({ name: 'call' }, () => api.updateSpan({ output: value }))();
    const row = await exported();
    expect(JSON.parse(String(row[SPAN_OUTPUT]))).toBe(value.note());
    expect(row[ATTACHMENTS]).toBeUndefined();
  },
);

it('leaves marker text naming unknown media alone', async () => {
  const text = `[CONFIDENT:IMAGE:${'0'.repeat(32)}]`;
  api.span({ name: 'call' }, () => api.updateSpan({ output: text }))();
  const row = await exported();
  expect(JSON.parse(String(row[SPAN_OUTPUT]))).toBe(text);
  expect(row[ATTACHMENTS]).toBeUndefined();
});

it('sends neither marker nor bytes with capture disabled', async () => {
  await start({ captureContent: false });
  api.span({ name: 'call' }, () =>
    api.updateSpan({
      output: media(WAV, 'audio/wav'),
      audio: media(WAV, 'audio/wav'),
    }),
  )();
  const row = await exported();
  expect(row[SPAN_OUTPUT]).toBeUndefined();
  expect(row[SPAN_AUDIO]).toBeUndefined();
  expect(row[ATTACHMENTS]).toBeUndefined();
});

it('keeps audio beside text input and output', async () => {
  const audio = media(WAV, 'audio/wav');
  api.span({ name: 'turn' }, () =>
    api.updateSpan({ input: "what's my balance?", output: "It's $42.", audio }),
  )();
  const row = await exported();
  expect(JSON.parse(String(row[SPAN_INPUT]))).toBe("what's my balance?");
  expect(row[SPAN_AUDIO]).toBe(`[CONFIDENT:AUDIO:${audio.id}]`);
  expect(attachments(row)[audio.id]!.mimeType).toBe('audio/wav');
});

it('carries trace and span audio in the same attachments', async () => {
  const call = media(WAV, 'audio/ogg');
  const turn = media(WAV, 'audio/wav');
  api.span({ name: 'call', audio: turn }, () =>
    api.updateTrace({ audio: call }),
  )();
  const row = await exported();
  expect(row[TRACE_AUDIO]).toBe(`[CONFIDENT:AUDIO:${call.id}]`);
  expect(row[SPAN_AUDIO]).toBe(`[CONFIDENT:AUDIO:${turn.id}]`);
  expect(Object.keys(attachments(row)).sort()).toEqual(
    [call.id, turn.id].sort(),
  );
});

it('drops replaced audio', async () => {
  const second = media(WAV, 'audio/wav');
  api.span({ name: 'turn' }, () => {
    api.updateSpan({ audio: media(WAV, 'audio/wav') });
    api.updateSpan({ audio: second });
  })();
  const row = await exported();
  expect(row[SPAN_AUDIO]).toBe(`[CONFIDENT:AUDIO:${second.id}]`);
  expect(Object.keys(attachments(row))).toEqual([second.id]);
});

it('leaves audio over the budget unwritten', async () => {
  await start({ maxMediaBytes: WAV.length - 1 });
  api.span({ name: 'turn' }, () =>
    api.updateSpan({ audio: media(WAV, 'audio/wav') }),
  )();
  const row = await exported();
  expect(row[SPAN_AUDIO]).toBeUndefined();
  expect(row[ATTACHMENTS]).toBeUndefined();
});

it.each([
  ['raw bytes', () => WAV],
  ['a path', () => 'turn.wav'],
  ['an image', () => media(PNG, 'image/png')],
  ['untyped media', () => media(WAV)],
  ['a remote reference', () => api.Media.fromUri('https://example.com/a.mp3')],
])('rejects %s as audio', (_label, value) => {
  expect(() =>
    api.updateSpan({ audio: value() as unknown as PublicApi.Media }),
  ).toThrow(TypeError);
});

it('keeps the payload out of inspected logs', () => {
  const audio = media(WAV, 'audio/wav');
  expect(inspect(audio)).toBe(
    'Media(mimeType=audio/wav, source=inline, bytes=?)',
  );
});
