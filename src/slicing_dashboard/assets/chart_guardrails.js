// Keep dense charts readable without blocking page scrolling or building hidden tables.
(function () {
    const bound = new WeakSet();
    let tooltip;
    function refreshHourLabels(plot) {
        const orientation = plot.layout.meta?.hour_axis_orientation;
        if (!orientation || !window.Plotly) return;
        const horizontal = orientation === 'h', totals = new Map();
        let maximum = 0;
        const stacked = ['stack', 'relative'].includes(plot.layout.barmode);
        for (const trace of plot._fullData || []) {
            if (trace.visible === false || trace.visible === 'legendonly') continue;
            const values = horizontal ? trace.x : trace.y;
            const labels = horizontal ? trace.y : trace.x;
            Array.from(values || []).forEach((value, index) => {
                const valid = typeof value === 'number' && Number.isFinite(value) && value >= 0;
                if (valid) maximum = Math.max(maximum, value);
                if (trace.type !== 'bar' || !labels) return;
                const key = labels[index], previous = totals.has(key) ? totals.get(key) : 0;
                totals.set(key, valid && previous !== null ? previous + value : null);
            });
        }
        if (stacked) for (const value of totals.values()) if (value !== null) maximum = Math.max(maximum, value);
        const upper = maximum > 0 ? maximum * 1.1 : 1;
        if (!Number.isFinite(upper)) return;
        const update = {[`${horizontal ? 'x' : 'y'}axis.range`]: [0, upper],
                        [`${horizontal ? 'x' : 'y'}axis.autorange`]: false};
        if (plot.layout.meta.bar_total_labels) {
            const annotations = (plot.layout.annotations || []).filter(item => item.name !== 'bar-hour-total');
            for (const [category, total] of totals) {
                if (total === null) continue;
                const label = (total >= 10000 ? total.toExponential(2).replace(/\.?0+e/, 'e') :
                    total > 0 && total < .01 ? Number(total.toPrecision(3)).toString() : total.toFixed(2)) + 'h';
                annotations.push({name: 'bar-hour-total', text: label,
                    x: horizontal ? total : category, y: horizontal ? category : total,
                    xref: 'x', yref: 'y', showarrow: false,
                    xanchor: horizontal ? 'left' : 'center', yanchor: horizontal ? 'middle' : 'bottom',
                    xshift: horizontal ? 6 : 0, yshift: horizontal ? 0 : 6,
                    font: {size: 11, color: plot.layout.font?.color}});
            }
            update.annotations = annotations;
        }
        window.Plotly.relayout(plot, update);
    }
    function hideTooltip() { if (tooltip) tooltip.hidden = true; }
    function update(plot, graph) {
        const frame = graph.closest('.chart-frame');
        if (!frame || !plot.data) return;
        const traces = plot._fullData || plot.data;
        const horizontal = new Set();
        const vertical = new Set();
        traces.forEach(trace => {
            if (trace.type !== 'bar') return;
            if (trace.orientation !== 'h' && plot._fullLayout?.xaxis?.type === 'date') return;
            const target = trace.orientation === 'h' ? horizontal : vertical;
            Array.from((trace.orientation === 'h' ? trace.y : trace.x) || []).forEach(value => {
                if (typeof value === 'string') target.add(value);
            });
        });
        if (!graph.dataset.baseHeight) graph.dataset.baseHeight = String(parseFloat(graph.style.height) || 340);
        const height = Math.max(Number(graph.dataset.baseHeight), horizontal.size * 34 + 160);
        const viewport = frame.querySelector('.chart-viewport');
        const width = vertical.size > 14 ? Math.max(viewport.clientWidth, vertical.size * 38 + 90) : 0;
        const nextWidth = width ? width + 'px' : '100%';
        // The shared ResizeObserver handles changed dimensions once.
        if (graph.style.height !== height + 'px') graph.style.height = height + 'px';
        if (graph.style.width !== nextWidth) graph.style.width = nextWidth;
    }
    function watch(root = document) {
        const plots = root.matches?.('.js-plotly-plot') ? [root] :
            root.querySelectorAll('.chart-surface .js-plotly-plot');
        plots.forEach(plot => {
            if (bound.has(plot) || typeof plot.on !== 'function') return;
            const graph = plot.closest('.chart-surface');
            if (!graph) return;
            bound.add(plot);
            plot.on('plotly_afterplot', () => update(plot, graph));
            plot.on('plotly_restyle', () => refreshHourLabels(plot));
            plot.on('plotly_unhover', hideTooltip);
            plot.on('plotly_hover', event => {
                requestAnimationFrame(() => {
                    const hover = plot.querySelector('.hoverlayer');
                    const lines = hover ? Array.from(hover.querySelectorAll('text')).map(node => {
                        const parts = Array.from(node.querySelectorAll('tspan.line'));
                        return parts.length ? parts.map(part => part.textContent).join('\n') : node.textContent;
                    }) : [];
                    if (!lines.length) return;
                    if (!tooltip) {
                        tooltip = document.createElement('div'); tooltip.className = 'chart-tooltip';
                        tooltip.setAttribute('role', 'tooltip'); document.body.appendChild(tooltip);
                    }
                    tooltip.textContent = lines.join('\n');
                    tooltip.hidden = false;
                    const rect = tooltip.getBoundingClientRect();
                    const pointer = event.event || {};
                    tooltip.style.left = Math.max(12, Math.min((pointer.clientX || 12) + 14, innerWidth - rect.width - 12)) + 'px';
                    tooltip.style.top = Math.max(12, Math.min((pointer.clientY || 12) + 14, innerHeight - rect.height - 12)) + 'px';
                    plot.classList.add('bounded-hover');
                });
            });
            update(plot, graph);
        });
    }
    function start() {
        let queued = false;
        const roots = new Set();
        new MutationObserver(records => {
            for (const record of records) {
                const plot = record.target.closest?.('.js-plotly-plot');
                // Plotly redraws and hover updates are already covered by its events.
                if (plot) {
                    if (!bound.has(plot)) roots.add(plot);
                    continue;
                }
                for (const node of record.addedNodes) {
                    if (node.nodeType === 1 && (node.matches('.js-plotly-plot') ||
                        node.querySelector('.chart-surface, .js-plotly-plot'))) roots.add(node);
                }
            }
            if (queued || !roots.size) return;
            queued = true;
            requestAnimationFrame(() => {
                queued = false;
                for (const root of roots) if (root.isConnected) watch(root);
                roots.clear();
            });
        }).observe(document.body, {childList: true, subtree: true});
        document.addEventListener('scroll', hideTooltip, true);
        window.addEventListener('resize', hideTooltip);
        watch();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
