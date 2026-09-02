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
