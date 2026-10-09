// Standalone headless fallback when the in-app browser cannot connect.
// Runs only against scripts/dashboard_chart_preview.py's synthetic localhost fixture.
import {spawn} from 'node:child_process';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const root = resolve('data/reports/chart-qa');
await mkdir(root, {recursive: true});
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
    '--headless=new', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
    `--user-data-dir=${root}/browser-profile`, 'about:blank'], {windowsHide: true});
const endpoint = await new Promise((accept, reject) => {
    let buffer = '';
    chrome.stderr.on('data', value => {
        buffer += value;
        const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) accept(match[1]);
    });
    chrome.on('error', reject);
    setTimeout(() => reject(new Error('Browser startup timeout')), 15000).unref();
});
const ws = new WebSocket(endpoint);
await new Promise((accept, reject) => { ws.onopen = accept; ws.onerror = reject; });
let sequence = 0;
const pending = new Map();
const errors = [];
ws.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) {
        const handler = pending.get(message.id);
        if (!handler) return;
        pending.delete(message.id);
        message.error ? handler.reject(new Error(JSON.stringify(message.error))) : handler.accept(message.result);
    } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
    else if (message.method === 'Network.responseReceived' && message.params.response.status >= 400)
        errors.push({url: message.params.response.url, status: message.params.response.status});
};
function send(method, params = {}, sessionId) {
    return new Promise((accept, reject) => {
        const id = ++sequence;
        pending.set(id, {accept, reject});
        ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
}
const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
const {sessionId} = await send('Target.attachToTarget', {targetId, flatten: true});
const call = (method, params) => send(method, params, sessionId);
await call('Runtime.enable');
await call('Page.enable');
await call('Network.enable');
async function evaluate(expression) {
    const result = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
}
async function until(expression, timeout = 20000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        if (await evaluate(expression)) return;
        await new Promise(accept => setTimeout(accept, 150));
    }
    throw new Error('Timed out: ' + expression);
}
const visible = `el => !!(el.offsetWidth && el.offsetHeight && el.getClientRects().length)`;
const results = [];
try {
    for (const mode of (['lifecycle', 'labels', 'latency', 'user-latency'].includes(process.argv[2]) ? [] : (process.argv[2] || 'normal,many,empty,one,zero,large,failure').split(','))) {
        await fetch('http://127.0.0.1:8059/qa/scenario', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode, seconds: 3600, auditor_seconds: 0, admin_seconds: 0, delay: 0})});
        for (const width of (mode === 'normal' || mode === 'many' ? [390, 768, 1366, 1920] : [390])) {
            await call('Emulation.setDeviceMetricsOverride', {width, height: 950, deviceScaleFactor: 1, mobile: false});
            for (const [route, selector] of [['/', '#pending-chart'], ['/reports/daily', '#daily-work-chart'], ['/reports/user', '#user-work-trend']]) {
                await call('Page.navigate', {url: 'http://127.0.0.1:8059' + route});
                await until(`document.querySelector('${selector} .js-plotly-plot')?.data && !Array.from(document.querySelectorAll('[data-dash-is-loading="true"]')).some(${visible})`);
                await new Promise(accept => setTimeout(accept, 500));
                const layout = await evaluate(`(() => {
                    const visible = ${visible};
                    const cards = Array.from(document.querySelectorAll('.chart-frame')).filter(visible);
                    return {documentWidth: document.documentElement.scrollWidth, viewport: innerWidth,
                        charts: cards.map(card => {const rect = card.getBoundingClientRect();
                            const graph = card.querySelector('.dash-graph');
                            const plot = card.querySelector('.js-plotly-plot');
                            const title = plot?.querySelector('.g-gtitle')?.getBoundingClientRect();
                            const surface = plot?.getBoundingClientRect();
                            const totals = Array.from(plot?.querySelectorAll('.annotation-text') || [])
                                .filter(el => /h$/.test(el.textContent)).map(el => {
                                    const box = el.getBoundingClientRect();
                                    return {text: el.textContent, clipped: box.left < surface.left - 1 ||
                                            box.right > surface.right + 1 || box.top < surface.top - 1 || box.bottom > surface.bottom + 1};
                                });
                            return {id: graph.id, left: rect.left, right: rect.right, height: rect.height,
                                    traces: plot?.data?.length || 0, annotations: (plot?.layout?.annotations || []).map(a => a.text),
                                    svgWidth: plot?._fullLayout?.width, svgHeight: plot?._fullLayout?.height,
                                    graphHeight: graph.getBoundingClientRect().height,
                                    titleClipped: !!(title?.height && title.top < surface.top - 1), totals,
                                    legendEntries: Array.from(plot?.querySelectorAll('.legendtext') || []).map(el => el.textContent)};}),
                        errors: document.querySelectorAll('.dash-error-card').length};})()`);
                const label = `${mode}-${width}-${route.replaceAll('/', '_') || 'main'}`;
                const screenshot = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true});
                await writeFile(`${root}/${label}.png`, Buffer.from(screenshot.data, 'base64'));
                results.push({mode, width, route, ...layout});
                console.log(JSON.stringify({mode, width, route, charts: layout.charts.length,
                    overflow: layout.charts.filter(card => card.left < -1 || card.right > width + 1).map(card => card.id), errors: layout.errors}));
            }
        }
    }
    if (process.argv[2] === 'latency') {
        await fetch('http://127.0.0.1:8059/qa/scenario', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mode: 'normal', seconds: 3600, delay: .3, dashboard_delay: 10, workflow_delay: 10})});
        await call('Emulation.setDeviceMetricsOverride', {width: 1366, height: 950, deviceScaleFactor: 1, mobile: false});
        const began = Date.now();
        await call('Page.navigate', {url: 'http://127.0.0.1:8059/'});
        await until(`document.querySelector('#pending-chart .js-plotly-plot')?.data?.length && document.querySelector('#approval-trend-chart .js-plotly-plot')?.data?.length`);
        const loadedMs = Date.now() - began;
        const mainLoading = await evaluate(`document.querySelector('#individual-chart').getAttribute('data-dash-is-loading') === 'true'`);
        if (loadedMs >= 6000 || !mainLoading) throw new Error('Independent plots waited for the ten-second main/workflow callbacks');
        results.push({initialPlotsMs: loadedMs, mainStillLoading: mainLoading});
        await until(`document.querySelector('#individual-chart').getAttribute('data-dash-is-loading') !== 'true'`);
        await evaluate(`document.querySelector('#refresh-btn').click()`);
        await until(`document.querySelector('#individual-chart').getAttribute('data-dash-is-loading') === 'true'`);
        await evaluate(`document.querySelector('#universal-legend').scrollIntoView({block: 'center', behavior: 'instant'})`);
        const pointer = await evaluate(`(() => {const r = document.querySelector('#universal-legend .legendtoggle').getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2};})()`);
        const selectionBegan = Date.now();
        await call('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...pointer});
        await call('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...pointer});
        await until(`document.querySelector('#pending-chart .js-plotly-plot')?.data?.[0]?.x?.length === 1`, 2500);
        results.push({selectionMs: Date.now() - selectionBegan,
            mainStillLoading: await evaluate(`document.querySelector('#individual-chart').getAttribute('data-dash-is-loading') === 'true'`)});
        await fetch('http://127.0.0.1:8059/qa/scenario', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({delay: 0, dashboard_delay: 0, workflow_delay: 0})});
        console.log(JSON.stringify(results));
    }
    if (process.argv[2] === 'user-latency') {
        const scenario = values => fetch('http://127.0.0.1:8059/qa/scenario', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values)});
        await scenario({mode: 'normal', user_report_delay: 10, user_completion_delay: 0});
        await call('Emulation.setDeviceMetricsOverride', {width: 1366, height: 950, deviceScaleFactor: 1, mobile: false});
        const began = Date.now();
        await call('Page.navigate', {url: 'http://127.0.0.1:8059/reports/user'});
        await until(`document.querySelector('#user-report-person')?.textContent.includes('Priya')`);
        const namesMs = Date.now() - began;
        if (namesMs >= 5000 || await evaluate(`!!document.querySelector('#user-work-trend .js-plotly-plot')?.data`))
            throw new Error('Initial roster waited for slow report loading');
        results.push({initialNamesMs: namesMs, reportStillLoading: true});
        await until(`document.querySelector('#user-work-trend .js-plotly-plot')?.data?.length`);
        await scenario({user_report_delay: .2, user_completion_delay: 10});
        await call('Page.navigate', {url: 'about:blank'});
        const chartBegan = Date.now();
        await call('Page.navigate', {url: 'http://127.0.0.1:8059/reports/user'});
        await until(`document.querySelectorAll('#user-report-content .js-plotly-plot').length === 3 && document.querySelector('#user-work-trend .js-plotly-plot')?.data?.length`);
        const chartsMs = Date.now() - chartBegan;
        const completionPending = await evaluate(`document.querySelector('#user-report-completed-card')?.textContent.includes('Loading completion')`);
        if (chartsMs >= 6000 || !completionPending) throw new Error('User charts waited for completion');
        results.push({initialChartsMs: chartsMs, completionStillLoading: completionPending});
        const select = await evaluate(`(() => {const el = document.querySelector('#user-report-person'); const r = el.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2};})()`);
        await call('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...select});
        await call('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...select});
        await until(`Array.from(document.querySelectorAll('.VirtualizedSelectOption, [role="option"]')).some(el => el.textContent.trim() === 'Riya')`);
        const option = await evaluate(`(() => {const el = Array.from(document.querySelectorAll('.VirtualizedSelectOption, [role="option"]')).find(el => el.textContent.trim() === 'Riya'); const r = el.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2};})()`);
        const switchBegan = Date.now();
        await call('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...option});
        await call('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...option});
        await until(`document.querySelector('#user-report-content h2')?.textContent === 'Riya'`, 3000);
        results.push({switchPersonMs: Date.now() - switchBegan});
        await evaluate(`document.querySelector('#user-work-trend .js-plotly-plot').dataset.qaRetained = 'yes'`);
        await until(`document.querySelector('#user-report-completed-card')?.textContent.includes('2 completed tasks')`, 15000);
        if (!await evaluate(`document.querySelector('#user-work-trend .js-plotly-plot')?.dataset.qaRetained === 'yes'`))
            throw new Error('Completion update remounted work charts');
        results.push({completionUpdatedWithoutChartRemount: true});
        await scenario({user_report_delay: 0, user_completion_delay: 0});
        console.log(JSON.stringify(results));
    }
    if (process.argv[2] === 'labels') {
        await fetch('http://127.0.0.1:8059/qa/scenario', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mode: 'normal', seconds: 3600, auditor_seconds: 7200, admin_seconds: 1800, delay: 0})});
        for (const width of [390, 1366]) {
            await call('Emulation.setDeviceMetricsOverride', {width, height: 950, deviceScaleFactor: 1, mobile: false});
            await call('Page.navigate', {url: 'http://127.0.0.1:8059/'});
            const p = `document.querySelector('#pending-chart .js-plotly-plot')`;
            await until(`${p}?.layout?.annotations?.some(a => a.name === 'bar-hour-total' && a.text === '3.50h')`);
            const inspect = `(() => {const p = ${p}; return {labels: p.layout.annotations.filter(a => a.name === 'bar-hour-total').map(a => a.text), range: p.layout.yaxis.range};})()`;
            const initial = await evaluate(inspect);
            await evaluate(`(async () => {const p = ${p}; const index = p.data.findIndex(t => t.name === 'Pending Auditor'); await Plotly.restyle(p, {visible: 'legendonly'}, [index]); return true;})()`);
            await until(`${p}.layout.annotations.filter(a => a.name === 'bar-hour-total').every(a => a.text === '1.50h')`);
            const hidden = await evaluate(inspect);
            await evaluate(`(async () => {const p = ${p}; const index = p.data.findIndex(t => t.name === 'Pending Auditor'); await Plotly.restyle(p, {visible: true}, [index]); return true;})()`);
            await until(`${p}.layout.annotations.filter(a => a.name === 'bar-hour-total').every(a => a.text === '3.50h')`);
            if (Math.abs(initial.range[1] - 3.85) > 1e-9 || Math.abs(hidden.range[1] - 1.65) > 1e-9)
                throw new Error('Totals/headroom did not follow visible bar stages');
            results.push({width, initial, hidden, restored: await evaluate(inspect)});
        }
        await fetch('http://127.0.0.1:8059/qa/scenario', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mode: 'normal', seconds: 3.6e13, auditor_seconds: 0, admin_seconds: 0, delay: 0})});
        await call('Page.navigate', {url: 'http://127.0.0.1:8059/'});
        const largePlot = `document.querySelector('#pending-chart .js-plotly-plot')`;
        await until(`${largePlot}?.layout?.annotations?.some(a => a.name === 'bar-hour-total' && a.text === '1e+10h')`);
        await evaluate(`(async () => {const p = ${largePlot}; const index = p.data.findIndex(t => t.name === 'Pending Auditor'); await Plotly.restyle(p, {visible: 'legendonly'}, [index]); return true;})()`);
        await until(`${largePlot}.layout.annotations.some(a => a.name === 'bar-hour-total' && a.text === '1e+10h')`);
        results.push({largeAfterLegendChange: await evaluate(`${largePlot}.layout.annotations.filter(a => a.name === 'bar-hour-total').map(a => a.text)`)});
        console.log(JSON.stringify(results));
    }
    if (process.argv[2] === 'lifecycle') {
        await call('Emulation.setDeviceMetricsOverride', {width: 1366, height: 950, deviceScaleFactor: 1, mobile: false});
        const scenario = async values => fetch('http://127.0.0.1:8059/qa/scenario', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values)});
        const pendingPlot = `document.querySelector('#pending-chart .js-plotly-plot')`;
        const current = `${pendingPlot}?._fullData?.[0]?.y?.[0]`;
        await scenario({mode: 'normal', seconds: 3600, delay: 0});
        await call('Page.navigate', {url: 'http://127.0.0.1:8059/'});
        await until(`${current} === 1`);
        await evaluate(`document.querySelector('#pending-chart').scrollIntoView({block: 'center', behavior: 'instant'})`);
        await new Promise(accept => setTimeout(accept, 400));
        await scenario({seconds: 1800, delay: 5});
        await evaluate(`document.querySelector('#refresh-btn').click()`);
        await until(`document.querySelector('#pending-chart').getAttribute('data-dash-is-loading') === 'true'`);
        const slow = await evaluate(`({hours: ${current}, pointerEvents: getComputedStyle(document.querySelector('#pending-chart')).pointerEvents, label: getComputedStyle(document.querySelector('#pending-chart').closest('.chart-frame'), '::after').content})`);
        if (slow.hours !== 1 || slow.pointerEvents === 'none') throw new Error('Refresh disabled the previous chart');
        const legendPointer = await evaluate(`(() => {const r = ${pendingPlot}.querySelector('.legendtoggle').getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2};})()`);
        await call('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...legendPointer});
        await call('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...legendPointer});
        await until(`${pendingPlot}.data[0].visible === 'legendonly'`, 1000);
        results.push({slow, legendInteractiveDuringRefresh: true});
        await until(`${current} === .5 && document.querySelector('#pending-chart').getAttribute('data-dash-is-loading') !== 'true'`);
        await scenario({mode: 'failure', delay: 0});
        await evaluate(`document.querySelector('#refresh-btn').click()`);
        await until(`${pendingPlot}?.data?.length === 0 && ${pendingPlot}?.layout?.annotations?.[0]?.text.includes('unavailable')`);
        results.push({failure: 'unavailable; previous values removed'});
        await scenario({mode: 'normal', seconds: 900});
        await evaluate(`document.querySelector('#refresh-btn').click()`);
        await until(`${current} === .25`);
        await evaluate(`document.querySelector('#nav-daily-report').click()`);
        await until(`location.pathname === '/reports/daily' && document.querySelector('#daily-work-chart .js-plotly-plot')?.data`);
        await scenario({seconds: 60});
        await evaluate(`document.querySelector('#nav-dashboard').click()`);
        await until(`Math.abs(${current} - 1/60) < 1e-9`);
        results.push({navigation: 'fresh values on return'});
        await scenario({seconds: 120});
        await evaluate(`document.querySelector('#workflow-bell').click()`);
        await until(`Math.abs(${current} - 1/30) < 1e-9`);
        results.push({workflow: 'review values refetched after workflow update'});
        await evaluate(`document.querySelector('.modal .btn-close')?.click()`);
        await until(`!Array.from(document.querySelectorAll('.modal, .modal-backdrop')).some(${visible})`);
        await evaluate(`${pendingPlot}.scrollIntoView({block: 'center', behavior: 'instant'})`);
        await new Promise(accept => setTimeout(accept, 400));
        const pointer = await evaluate(`(() => {
            const p = ${pendingPlot};
            const r = p.getBoundingClientRect(), data = p._fullData[0], layout = p._fullLayout;
            return {x: r.left + layout.xaxis._offset + layout.xaxis.d2p(data.x[0]),
                    y: r.top + layout.yaxis._offset + layout.yaxis.d2p(data.y[0] / 2)};
        })()`);
        await call('Input.dispatchMouseEvent', {type: 'mouseMoved', ...pointer});
        await until(`document.querySelector('.chart-tooltip:not([hidden])')`);
        results.push({tooltip: await evaluate(`(() => {const t = document.querySelector('.chart-tooltip'); const r = t.getBoundingClientRect(); return {text: t.textContent, withinViewport: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight};})()`)});
        const hiddenTables = await evaluate(`document.querySelectorAll('.chart-data-access, .chart-frame details, .chart-frame table').length`);
        if (hiddenTables) throw new Error('Chart data tables are still mounted');
        results.push({hiddenTables});
        // Dense horizontal bars must remain fully visible and let vertical wheel
        // input scroll the page even when the pointer is over the plot itself.
        await scenario({mode: 'many', seconds: 3600, delay: 0});
        await evaluate(`document.querySelector('#refresh-btn').click()`);
        await until(`new Set((document.querySelector('#assigned-chart .js-plotly-plot')?.data || []).filter(t => t.type === 'bar').flatMap(t => t.y || [])).size === 35`);
        await new Promise(accept => setTimeout(accept, 400));
        await evaluate(`document.querySelector('#assigned-chart').scrollIntoView({block: 'start', behavior: 'instant'})`);
        const dense = await evaluate(`(() => {
            const graph = document.querySelector('#assigned-chart'), viewport = graph.closest('.chart-viewport');
            const r = graph.getBoundingClientRect();
            return {graphHeight: r.height, viewportHeight: viewport.clientHeight, scrollY,
                    x: r.left + r.width / 2, y: Math.min(innerHeight - 100, Math.max(100, r.top + 180))};
        })()`);
        if (dense.graphHeight <= 660 || dense.viewportHeight < dense.graphHeight - 1)
            throw new Error('Dense plot has a vertical scroll trap');
        await call('Input.dispatchMouseEvent', {type: 'mouseWheel', x: dense.x, y: dense.y, deltaX: 0, deltaY: 350});
        await until(`scrollY > ${dense.scrollY + 100}`);
        const down = await evaluate('scrollY');
        await call('Input.dispatchMouseEvent', {type: 'mouseWheel', x: dense.x, y: dense.y, deltaX: 0, deltaY: -350});
        await until(`scrollY < ${down - 100}`);
        results.push({dense, wheelScrollDown: down, wheelScrollUp: await evaluate('scrollY')});
        console.log(JSON.stringify(results));
    }
    const reportName = process.argv[2] === 'lifecycle' ? 'lifecycle' : process.argv[2] ? `audit-${process.argv[2].replaceAll(',', '-')}` : 'audit';
    await writeFile(`${root}/${reportName}.json`, JSON.stringify({results, errors}, null, 2));
    const failedLayouts = results.filter(row => row.charts && (row.errors || row.documentWidth > row.viewport + 1 ||
        row.charts.some(chart => chart.left < -1 || chart.right > row.width + 1 || chart.titleClipped ||
                                 chart.totals?.some(label => label.clipped))));
    if (errors.length || failedLayouts.length) throw new Error(`Browser audit failed: ${errors.length} runtime/network errors; ${failedLayouts.length} invalid layouts`);
} finally {
    await send('Browser.close').catch(() => {});
    ws.close();
    chrome.kill();
}
