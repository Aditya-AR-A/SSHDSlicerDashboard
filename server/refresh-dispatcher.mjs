// A bounded Node waitUntil task holds the connection to the Python collector.
// Saved job checkpoints survive either function being terminated. The next cron
// trigger resumes queued steps and reconciles expired workers.
export function createRefreshHandler({ waitUntil, getDeadline, timingSafeEqual,
                                       fetchImpl = fetch, env = process.env }) {
  function authorized(request) {
    const actual = Buffer.from(request.headers.authorization || '');
    const expected = Buffer.from(`Bearer ${env.CRON_SECRET || ''}`);
    return Boolean(env.CRON_SECRET) && actual.length === expected.length && timingSafeEqual(actual, expected);
  }

  function origin() {
    // Use deployment-owned configuration, never Host or a caller-supplied URL.
    if (!env.VERCEL_URL || !/^[a-z0-9.-]+\.vercel\.app$/i.test(env.VERCEL_URL)) {
      throw new Error('DeploymentOriginUnavailable');
    }
    return `https://${env.VERCEL_URL}`;
  }

  async function call(path, payload, timeout = 12000) {
    const headers = { Authorization: `Bearer ${env.CRON_SECRET}`, 'Content-Type': 'application/json' };
    if (env.VERCEL_AUTOMATION_BYPASS_SECRET) {
      headers['x-vercel-protection-bypass'] = env.VERCEL_AUTOMATION_BYPASS_SECRET;
    }
    const response = await fetchImpl(origin() + path, {
      method: 'POST', headers, body: payload ? JSON.stringify(payload) : undefined,
      signal: AbortSignal.timeout(timeout), redirect: 'error',
    });
    if (!response.ok) throw Object.assign(new Error(`CaptureHTTP${response.status}`), { status: response.status });
    return response.json();
  }

  async function dispatch(job) {
    if (job?.state !== 'queued') return;
    const payload = { job_id: job.id, step: job.step };
    let result;
    try {
      const deadline = getDeadline?.()?.getTime() ?? Date.now() + 285000;
      const timeout = Math.min(255000, deadline - Date.now() - 30000);
      if (timeout < 1000) throw new Error('DispatcherDeadline');
      result = await call('/api/data-jobs/run', payload, timeout);
    } catch (error) {
      if (error.status === 409) return;  // A duplicate checkpoint claim is not a failed capture.
      // Persist a failure while there is still time; if storage is unavailable,
      // the next trigger/health check detects the expired durable checkpoint.
      try { result = await call('/api/data-jobs/fail', payload); }
      catch { console.error('Capture failure checkpoint unavailable; awaiting reconciliation.'); return; }
    }
    if (result.busy || result.job?.state !== 'queued') return;
    try {
      await call('/api/data-refresh', { resume: result.job.id });
    } catch {
      // A handoff may have been accepted before its response was lost. Leave
      // the job queued; the next scheduled trigger safely kicks it again.
      console.error('Capture handoff unconfirmed; awaiting next scheduled trigger.');
    }
  }

  return async function handler(request, response) {
    response.setHeader('Cache-Control', 'no-store');
    if (!['GET', 'POST'].includes(request.method)) return response.status(405).json({ error: 'Method not allowed' });
    if (!authorized(request)) return response.status(401).json({ error: 'Unauthorized' });
    try {
      const query = new URL(request.url, 'https://local.invalid').searchParams;
      // Resuming must not create a fresh job when the old job finished between
      // dispatch and acceptance. Python validates the expected step as well.
      const resume = request.body?.resume;
      let job;
      if (resume) {
        const headers = { Authorization: `Bearer ${env.CRON_SECRET}` };
        if (env.VERCEL_AUTOMATION_BYPASS_SECRET) headers['x-vercel-protection-bypass'] = env.VERCEL_AUTOMATION_BYPASS_SECRET;
        const health = await fetchImpl(origin() + '/api/data-health', {
          headers, signal: AbortSignal.timeout(12000), redirect: 'error',
        });
        const data = await health.json();
        job = data.job;
        if (!job || job.id !== resume) return response.status(409).json({ error: 'Job no longer active' });
      } else {
        const sources = query.get('sources');
        const path = '/api/data-jobs/enqueue' + (sources === null ? '' : `?sources=${encodeURIComponent(sources)}`);
        ({ job } = await call(path));
      }
      waitUntil(dispatch(job));
      return response.status(202).json({ accepted: true, job_id: job.id, state: job.state,
                                        status_url: '/api/data-health' });
    } catch (error) {
      const status = error.status === 400 ? 400 : 503;
      return response.status(status).json({ error: status === 400 ? 'Invalid source selection' : 'Capture trigger unavailable' });
    }
  };
}
