# stealth.py
"""
Browser stealth injection layer.

Patches every new Playwright page with JavaScript that masks automation signals.
Without this, sites like Amazon, Walmart, Target detect headless Chrome instantly
via: navigator.webdriver, missing plugins, Chrome runtime absence, permissions API,
WebGL fingerprint, and screen/window dimension anomalies.

Usage
-----
from stealth import apply_stealth, STEALTH_LAUNCH_ARGS, get_stealth_context_kwargs
"""

import random

# ---------------------------------------------------------------------------
# Launch args — remove automation-specific Chromium flags
# ---------------------------------------------------------------------------
STEALTH_LAUNCH_ARGS: dict = {
    "headless": True,
    "args": [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1920,1080",
        "--disable-infobars",
        "--disable-extensions",
        "--no-default-browser-check",
        "--no-first-run",
        "--disable-web-security",
        "--allow-running-insecure-content",
        "--ignore-certificate-errors",
        "--disable-breakpad",
    ],
}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
]


def get_stealth_context_kwargs() -> dict:
    """Return a randomised context config to avoid fingerprint consistency."""
    return {
        "user_agent": random.choice(_USER_AGENTS),
        "viewport": random.choice(_VIEWPORTS),
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "java_script_enabled": True,
        "bypass_csp": True,
        "ignore_https_errors": True,
        "extra_http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        },
    }


# ---------------------------------------------------------------------------
# Core stealth JS — injected before any page content executes
# ---------------------------------------------------------------------------
_STEALTH_JS = r"""
(function () {
    // 1. Remove webdriver flag
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });
    } catch(_) {}

    // 2. Mock navigator.plugins
    try {
        const fakePlugins = [
            { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',            description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client',      filename: 'internal-nacl-plugin',            description: '' },
        ];
        Object.defineProperty(navigator, 'plugins', {
            get: () => Object.assign(fakePlugins, { item: i => fakePlugins[i], namedItem: n => fakePlugins.find(p=>p.name===n)||null, refresh: ()=>{}, length: fakePlugins.length }),
            configurable: true,
        });
    } catch(_) {}

    // 3. Mock navigator.languages
    try { Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true }); } catch(_) {}

    // 4. Add window.chrome
    try {
        if (!window.chrome) {
            window.chrome = { app: { isInstalled: false }, runtime: {}, loadTimes: ()=>({}), csi: ()=>({}) };
        }
    } catch(_) {}

    // 5. Spoof permissions query
    try {
        const orig = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = p => (p&&p.name==='notifications') ? Promise.resolve({state: Notification.permission, onchange: null}) : orig(p);
    } catch(_) {}

    // 6. WebGL: spoof real GPU strings
    try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (gl) {
            const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
            if (debugInfo) {
                const getParam = gl.getParameter.bind(gl);
                gl.getParameter = function(p) {
                    if (p === debugInfo.UNMASKED_VENDOR_WEBGL) return 'Intel Inc.';
                    if (p === debugInfo.UNMASKED_RENDERER_WEBGL) return 'Intel Iris OpenGL Engine';
                    return getParam(p);
                };
            }
        }
    } catch(_) {}

    // 7. Platform / hardware
    try { Object.defineProperty(navigator, 'platform', { get: () => 'Win32', configurable: true }); } catch(_) {}
    try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(_) {}
    try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 }); } catch(_) {}
    
    // 8. Mask outerHeight/innerWidth to look like real window
    try {
        window.outerHeight = window.innerHeight + 100;
        window.outerWidth = window.innerWidth;
    } catch(_) {}
})();
"""


async def apply_stealth(page) -> None:
    """Inject stealth JS as an init script (runs before any page JS)."""
    await page.add_init_script(_STEALTH_JS)
