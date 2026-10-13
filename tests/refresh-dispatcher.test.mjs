import test from 'node:test';
import assert from 'node:assert/strict';
import { timingSafeEqual } from 'node:crypto';
import { createRefreshHandler } from '../server/refresh-dispatcher.mjs';

function harness(fetchImpl, env = { CRON_SECRET: 'secret', VERCEL_URL: 'dashboard.vercel.app' }) {
  const pending = [];
  const handler = createRefreshHandler({ timingSafeEqual, env, fetchImpl,
    waitUntil: promise => pending.push(promise), getDeadline: () => new Date(Date.now() + 300000) });
  const response = { statusCode: 200, headers: {}, setHeader(k, v) { this.headers[k] = v; },
    status(code) { this.statusCode = code; return this; }, json(body) { this.body = body; return this; } };
  const request = { method: 'POST', url: '/api/data-refresh', headers: { authorization: 'Bearer secret' } };
  return { handler, pending, request, response };
}
const queued = { id: 'job-1', step: 0, state: 'queued' };
function json(body, status = 200) { return new Response(JSON.stringify(body), { status }); }

test('trigger acknowledges durable enqueue before a slow worker completes', async () => {
  let finish, started = false;
  const blocked = new Promise(resolve => { finish = resolve; });
  const h = harness(async (url) => {
    if (url.endsWith('/enqueue')) return json({ job: queued });
    started = true;
    await blocked;
    return json({ job: { ...queued, state: 'completed' } });
  });
  await h.handler(h.request, h.response);
  assert.equal(h.response.statusCode, 202);
  assert.equal(h.response.body.accepted, true);
  assert.equal(h.pending.length, 1);
  assert.equal(started, true);
  finish();
  await Promise.all(h.pending);
});

test('only authenticated GET/POST triggers can schedule work', async () => {
  let calls = 0;
  const h = harness(async () => { calls++; });
  h.request.headers.authorization = 'Bearer wrong';
  await h.handler(h.request, h.response);
  assert.equal(h.response.statusCode, 401);
  h.request.method = 'DELETE';
  await h.handler(h.request, h.response);
  assert.equal(h.response.statusCode, 405);
  assert.equal(calls, 0);
  assert.equal(h.pending.length, 0);
});

test('storage failure is never acknowledged as accepted', async () => {
  const h = harness(async () => json({ error: 'database' }, 503));
  await h.handler(h.request, h.response);
  assert.equal(h.response.statusCode, 503);
  assert.equal(h.pending.length, 0);
});

test('untrusted Host cannot redirect the secret; all calls have deadlines and reject redirects', async () => {
  const calls = [];
  const h = harness(async (url, options) => {
    calls.push({ url, options });
    return json({ job: url.includes('/enqueue') ? queued : { ...queued, state: 'completed' } });
  });
  h.request.headers.host = 'attacker.invalid';
  h.request.url += '?sources=daily,assignable_pool';
  await h.handler(h.request, h.response);
  await Promise.all(h.pending);
  assert.ok(calls[0].url.endsWith('?sources=daily%2Cassignable_pool'));
  for (const { url, options } of calls) {
    assert.ok(url.startsWith('https://dashboard.vercel.app/'));
    assert.equal(options.redirect, 'error');
    assert.ok(options.signal instanceof AbortSignal);
  }
});

test('worker timeout persists failure and dispatches remaining sources', async () => {
  const paths = [];
  const h = harness(async (url, options) => {
    const path = new URL(url).pathname;
    paths.push(path);
    if (path.endsWith('/enqueue')) return json({ job: queued });
    if (path.endsWith('/run')) throw new DOMException('aborted', 'TimeoutError');
    if (path.endsWith('/fail')) {
      assert.deepEqual(JSON.parse(options.body), { job_id: 'job-1', step: 0 });
      return json({ job: { ...queued, step: 1 } });
    }
    return json({ accepted: true }, 202);
  });
  await h.handler(h.request, h.response);
  await Promise.all(h.pending);
  assert.deepEqual(paths, ['/api/data-jobs/enqueue', '/api/data-jobs/run', '/api/data-jobs/fail', '/api/data-refresh']);
});

test('duplicate or busy worker does not cause a dispatch loop', async () => {
  let calls = 0;
  const h = harness(async () => {
    calls++;
    return json(calls === 1 ? { job: queued } : { busy: true, job: queued });
  });
  await h.handler(h.request, h.response);
  await Promise.all(h.pending);
  assert.equal(calls, 2);
});

test('handoff validates the old job instead of enqueuing an infinite new job', async () => {
  const h = harness(async (url) => {
    assert.ok(url.endsWith('/api/data-health'));
    return json({ job: { ...queued, id: 'new-job' } }, 503);
  });
  h.request.body = { resume: 'old-job' };
  await h.handler(h.request, h.response);
  assert.equal(h.response.statusCode, 409);
  assert.equal(h.pending.length, 0);
});

test('deployment origin missing fails closed', async () => {
  const h = harness(async () => assert.fail('No network call expected'), { CRON_SECRET: 'secret' });
  await h.handler(h.request, h.response);
  assert.equal(h.response.statusCode, 503);
});

test('a competing checkpoint lease does not falsely fail the active worker', async () => {
  const paths = [];
  const h = harness(async url => {
    paths.push(new URL(url).pathname);
    return url.endsWith('/enqueue') ? json({ job: queued }) : json({ error: 'busy' }, 409);
  });
  await h.handler(h.request, h.response);
  await Promise.all(h.pending);
  assert.deepEqual(paths, ['/api/data-jobs/enqueue', '/api/data-jobs/run']);
});

test('invalid source selection returns 400 without scheduling work', async () => {
  const h = harness(async () => json({ error: 'invalid selection' }, 400));
  h.request.url += '?sources=unknown';
  await h.handler(h.request, h.response);
  assert.equal(h.response.statusCode, 400);
  assert.equal(h.pending.length, 0);
});
