// static/js/main.js

document.addEventListener('DOMContentLoaded', function() {
    initializeThemeToggle();
    initializeTooltips();
    initializeAjaxSetup();
    initializeNavigation();
});

function initializeThemeToggle() {
    const themeToggle = document.querySelector('.theme-toggle');
    const navThemeToggle = document.getElementById('navThemeToggle');
    
    const toggleTheme = function() {
        const currentTheme = document.documentElement.getAttribute('data-bs-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        
        document.documentElement.setAttribute('data-bs-theme', newTheme);
        updateThemeIcons(newTheme);
        localStorage.setItem('theme', newTheme);
    };
    
    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }
    if (navThemeToggle) {
        navThemeToggle.addEventListener('click', toggleTheme);
    }
    
    // Apply saved theme
    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-bs-theme', savedTheme);
    updateThemeIcons(savedTheme);
}

function updateThemeIcons(theme) {
    const icons = document.querySelectorAll('.theme-toggle i, #navThemeToggle i');
    icons.forEach(icon => {
        if (theme === 'dark') {
            icon.className = 'fas fa-moon';
        } else {
            icon.className = 'fas fa-sun';
        }
    });
}

function initializeTooltips() {
    try {
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggerList.map(function (tooltipTriggerEl) {
            try {
                return new bootstrap.Tooltip(tooltipTriggerEl);
            } catch (e) {
                console.warn('Failed to initialize tooltip:', e);
                return null;
            }
        });
    } catch (e) {
        console.warn('Tooltip initialization error:', e);
    }
}

function initializeAjaxSetup() {
    const csrfToken = document.querySelector('meta[name="csrf-token"]');
    if (csrfToken) {
        $.ajaxSetup({
            headers: {
                'X-CSRFToken': csrfToken.getAttribute('content')
            }
        });
    }
}

function initializeNavigation() {
    // Active link highlighting
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });
}

function showToast(message, type = 'info') {
    const toastContainer = document.getElementById('toastContainer');
    
    const toastId = 'toast-' + Date.now();
    const toastHTML = `
        <div id="${toastId}" class="toast align-items-center text-bg-${type} border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body">
                    <i class="fas fa-${getIconForType(type)} me-2"></i>
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    
    toastContainer.insertAdjacentHTML('beforeend', toastHTML);
    const toastElement = document.getElementById(toastId);
    const bsToast = new bootstrap.Toast(toastElement);
    bsToast.show();
    
    toastElement.addEventListener('hidden.bs.toast', function() {
        toastElement.remove();
    });
}

function getIconForType(type) {
    const icons = {
        'success': 'check-circle',
        'error': 'times-circle',
        'warning': 'exclamation-circle',
        'info': 'info-circle'
    };
    return icons[type] || 'info-circle';
}

function showLoadingSpinner() {
    document.getElementById('loading-spinner').classList.remove('d-none');
}

function hideLoadingSpinner() {
    document.getElementById('loading-spinner').classList.add('d-none');
}

// Error handling - suppress third-party library errors that don't affect functionality
window.addEventListener('error', function(e) {
    // Ignore errors from floating-ui and other third-party libraries
    if (e.filename && (e.filename.includes('floating-ui') || e.filename.includes('popper'))) {
        e.preventDefault();
        return;
    }
    console.error('Global error:', e.error);
});

// Network error handling
document.addEventListener('ajaxError', function(e, xhr, settings, exception) {
    console.error('AJAX error:', exception);
    if (xhr.status === 401) {
        showToast('Your session has expired. Please login again.', 'error');
        window.location.href = '/account/login/';
    } else if (xhr.status === 403) {
        showToast('You do not have permission to perform this action.', 'error');
    } else {
        showToast('An error occurred. Please try again.', 'error');
    }
});

// Performance monitoring
if (window.performance && window.performance.navigation.type === 1) {
    console.log('Page was refreshed');
}