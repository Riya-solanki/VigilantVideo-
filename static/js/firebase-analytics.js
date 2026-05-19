// ════════════════════════════════════════════════════════════════
//  firebase-analytics.js  —  VigilantVideo Analytics
//  Drop your Firebase config below and this file handles
//  ALL tracking across every page automatically.
// ════════════════════════════════════════════════════════════════

import { initializeApp }        from "https://www.gstatic.com/firebasejs/11.8.1/firebase-app.js";
import { getAnalytics, logEvent, setUserProperties }
                                from "https://www.gstatic.com/firebasejs/11.8.1/firebase-analytics.js";

// ── 🔴 PASTE YOUR FIREBASE CONFIG HERE ──────────────────────────
const firebaseConfig = {
  apiKey: "AIzaSyCLcvY-yGqLIYxXq0o7UWZJBPuvys3TrVc",
  authDomain: "vigilantvideo-b2718.firebaseapp.com",
  projectId: "vigilantvideo-b2718",
  storageBucket: "vigilantvideo-b2718.firebasestorage.app",
  messagingSenderId: "1070331903186",
  appId: "1:1070331903186:web:6b3252eef628cffa1c410b",
  measurementId: "G-XZB54M065G"
};
// ────────────────────────────────────────────────────────────────

const app       = initializeApp(firebaseConfig);
const analytics = getAnalytics(app);

// ── Helpers ──────────────────────────────────────────────────────

/** Log any custom event safely (never throws). */
export function track(eventName, params = {}) {
  try {
    logEvent(analytics, eventName, params);
  } catch (e) {
    console.warn('[Analytics] logEvent failed:', e);
  }
}

/** Tag the current user so all subsequent events carry these props. */
export function identifyUser(props = {}) {
  try {
    setUserProperties(analytics, props);
  } catch (e) {
    console.warn('[Analytics] setUserProperties failed:', e);
  }
}

// ── Auto-track page_view on every load ───────────────────────────
track('page_view', {
  page_title:    document.title,
  page_location: window.location.href,
  page_path:     window.location.pathname,
});

// ── Auto-track outbound link clicks ──────────────────────────────
document.addEventListener('click', (e) => {
  const link = e.target.closest('a[href]');
  if (!link) return;
  const href = link.getAttribute('href');
  if (href && href.startsWith('http') && !href.includes(location.hostname)) {
    track('outbound_click', { url: href, text: link.innerText?.trim() });
  }
});

export { analytics };