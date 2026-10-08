window.dash_clientside = Object.assign({}, window.dash_clientside, {
    clientside: {
        toggleTheme: function(n_clicks) {
            let isDark = (n_clicks || 0) % 2 === 0;
            if (isDark) {
                document.documentElement.setAttribute('data-bs-theme', 'dark');
                return 'theme-dark';
            } else {
                document.documentElement.setAttribute('data-bs-theme', 'light');
                return 'theme-light';
            }
        }
    }
});

// Report graphs are mounted while their route is hidden. Plotly needs the
// visible container size after route changes, and after a new figure arrives.
(function () {
    const observed = new WeakSet();
    const sizes = new WeakMap();
    const observer = new ResizeObserver(function (entries) {
        entries.forEach(function (entry) {
            const box = entry.contentRect;
            if (!box.width || !box.height) {
                sizes.set(entry.target, [0, 0]);
                return;
            }
            const previous = sizes.get(entry.target);
            if (previous && previous[0] === box.width && previous[1] === box.height) return;
            sizes.set(entry.target, [box.width, box.height]);
            requestAnimationFrame(function () {
                const plot = entry.target.querySelector('.js-plotly-plot');
                if (plot && plot.data && window.Plotly) window.Plotly.Plots.resize(plot);
            });
        });
    });
    function watchGraphs() {
        document.querySelectorAll('.dash-graph').forEach(function (graph) {
            if (!observed.has(graph)) {
                observed.add(graph);
                observer.observe(graph);
            }
        });
    }
    function start() {
        watchGraphs();
        let scheduled = false;
        new MutationObserver(function () {
            if (scheduled) return;
            scheduled = true;
            requestAnimationFrame(function () {
                scheduled = false;
                watchGraphs();
            });
        }).observe(document.body, {childList: true, subtree: true});
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();

// Auto-open date picker when clicking anywhere on the date input field
document.addEventListener('click', function(e) {
    if (e.target && e.target.classList && e.target.classList.contains('date-input-custom')) {
        if (typeof e.target.showPicker === 'function') {
            try {
                e.target.showPicker();
            } catch (err) {
                // Ignore if already open or not supported
            }
        }
    }
});
