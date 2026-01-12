lucide.createIcons();

// --- DEBUG HUD LOGIC ---
const debugElements = {
    viewport: document.getElementById('debug-viewport'),
    scroll: document.getElementById('debug-scroll'),
    mouse: document.getElementById('debug-mouse'),
    nav: document.getElementById('debug-nav'),
    event: document.getElementById('debug-event'),
    stardate: document.getElementById('debug-stardate')
};

function updateDebugHUD() {
    // Viewport
    debugElements.viewport.textContent = `${window.innerWidth} x ${window.innerHeight}`;
    // Scroll
    debugElements.scroll.textContent = `${Math.round(window.scrollY)} px`;
    // Stardate (Dynamic calculation)
    const now = new Date();
    const start = new Date(now.getFullYear(), 0, 0);
    const diff = now - start;
    const day = Math.floor(diff / (1000 * 60 * 60 * 24));
    debugElements.stardate.textContent = `443${now.getFullYear() % 10}.${day}`;

    requestAnimationFrame(updateDebugHUD);
}

function logEvent(name) {
    debugElements.event.textContent = name;
    debugElements.event.classList.remove('text-purple-400');
    debugElements.event.classList.add('text-cyan-400');
    setTimeout(() => {
        debugElements.event.classList.remove('text-cyan-400');
        debugElements.event.classList.add('text-purple-400');
    }, 500);
}

window.addEventListener('mousemove', (e) => {
    debugElements.mouse.textContent = `${e.clientX}, ${e.clientY}`;
});

// Start loop
updateDebugHUD();

// --- UI LOGIC ---

// Mobile Menu Toggle
const menuBtn = document.getElementById('menu-btn');
const mobileMenu = document.getElementById('mobile-menu');
let isMenuOpen = false;

menuBtn.addEventListener('click', () => {
    isMenuOpen = !isMenuOpen;
    mobileMenu.classList.toggle('active');
    menuBtn.innerHTML = isMenuOpen ? '<i data-lucide="x" class="w-8 h-8"></i>' : '<i data-lucide="menu" class="w-8 h-8"></i>';
    lucide.createIcons();

    debugElements.nav.textContent = isMenuOpen ? 'OPEN (ACTIVE)' : 'CLOSED';
    logEvent(isMenuOpen ? 'MENU_OPEN' : 'MENU_CLOSE');
});

// Form logic
const contactForm = document.getElementById('contact-form');
const formMsg = document.getElementById('form-msg');

contactForm.addEventListener('submit', (e) => {
    e.preventDefault();
    logEvent('FORM_SUBMIT_START');
    formMsg.textContent = 'ENCRYPTING & TRANSMITTING...';
    formMsg.classList.remove('hidden');

    setTimeout(() => {
        formMsg.textContent = 'SIGNAL RECEIVED. AWAIT RESPONSE VIA NEURAL LINK.';
        contactForm.reset();
        logEvent('FORM_SUBMIT_SUCCESS');
    }, 2000);
});

// Smooth Scroll Parallax-ish logic
document.addEventListener('mousemove', (e) => {
    const x = e.clientX / window.innerWidth;
    const y = e.clientY / window.innerHeight;

    document.querySelectorAll('.hero-glow').forEach(el => {
        el.style.backgroundPosition = `${x * 20}% ${y * 20}%`;
    });
});

// Intersection Observer for scroll tracking in debug
const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            logEvent(`SECTION_ENTER:${entry.target.id.toUpperCase()}`);
        }
    });
}, { threshold: 0.5 });

document.querySelectorAll('section').forEach(section => observer.observe(section));
