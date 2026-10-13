const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');

function helpers() {
    let update;
    const context = {
        document: {readyState: 'loading', addEventListener() {}},
        window: {Plotly: {relayout(plot, next) { update = next; }}},
    };
    const source = fs.readFileSync('src/slicing_dashboard/assets/chart_guardrails.js', 'utf8')
        .replace("if (document.readyState === 'loading')",
            "globalThis.check = {clock, durationTicks, refreshHourLabels}; if (document.readyState === 'loading')");
    vm.runInNewContext(source, context);
    return {...context.check, update: () => update};
}

test('elapsed time retains seconds and hours beyond 24', () => {
    const {clock} = helpers();
    assert.equal(clock(2.5, true), '02:30');
    assert.equal(clock(100 + 61 / 3600, false), '100:01:01');
    assert.equal(clock(3599.5 / 3600, false), '01:00:00');
});

test('short duration axes use second intervals', () => {
    const {durationTicks} = helpers();
    assert.deepEqual(Array.from(durationTicks(0, 10 / 3600)[1]),
        ['00:00:00', '00:00:02', '00:00:04', '00:00:06', '00:00:08', '00:00:10']);
});

test('legend changes preserve clock totals and numeric axis values', () => {
    const checks = helpers();
    checks.refreshHourLabels({
        layout: {meta: {hour_axis_orientation: 'v', bar_total_labels: 'v'}, barmode: 'stack'},
        _fullData: [
            {type: 'bar', x: ['A'], y: [1]},
            {type: 'bar', x: ['A'], y: [.5]},
            {type: 'bar', visible: 'legendonly', x: ['A'], y: [50]},
        ],
    });
    const update = checks.update();
    assert.equal(update.annotations[0].text, '01:30');
    assert.equal(update.annotations[0].y, 1.5);
    assert.equal(update['yaxis.range'][1], 1.5 * 1.1);
    assert.ok(update['yaxis.ticktext'].every(label => /^\d+:\d{2}(:\d{2})?$/.test(label)));
});
