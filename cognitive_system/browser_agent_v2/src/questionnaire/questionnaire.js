/**
 * Questionnaire logic.
 * - Reads session_id from URL query param.
 * - Live-updates slider value badges.
 * - On submit: sends results to background -> system agent, shows confirmation.
 */

const SLIDERS = [
  "mental_demand", "physical_demand", "temporal_demand",
  "performance", "effort", "frustration",
  "stress_self_report",
  "valence", "arousal",
];

// ---------------------------------------------------------------------------
// Session ID from URL
// ---------------------------------------------------------------------------

const sessionId = new URLSearchParams(window.location.search).get("session_id") || "";
const badge = document.getElementById("session-badge");
badge.textContent = sessionId ? `Session: ${sessionId}` : "Session: unknown";

// ---------------------------------------------------------------------------
// Slider live-update
// ---------------------------------------------------------------------------

SLIDERS.forEach((name) => {
  const input = document.getElementById(name);
  const valEl = document.getElementById(`val-${name}`);
  if (!input || !valEl) return;

  function update() {
    valEl.textContent = input.value;
    // Color the badge relative to value
    const pct = (input.value - input.min) / (input.max - input.min);
    const h   = Math.round((1 - pct) * 120); // green (120) to red (0)
    valEl.style.background = `hsl(${h}, 65%, 38%)`;
  }

  input.addEventListener("input", update);
  update(); // initial colour
});

// ---------------------------------------------------------------------------
// Form submission
// ---------------------------------------------------------------------------

const form   = document.getElementById("q-form");
const btn    = document.getElementById("submit-btn");
const conf   = document.getElementById("confirmation");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  btn.disabled = true;
  btn.textContent = "Submitting…";

  const results = {};
  SLIDERS.forEach((name) => {
    const el = document.getElementById(name);
    results[name] = el ? parseFloat(el.value) : null;
  });
  results.notes      = (document.getElementById("notes")?.value || "").trim();
  results.session_id = sessionId;
  results.timestamp  = Date.now() / 1000;

  try {
    await chrome.runtime.sendMessage({ type: "questionnaire_submit", results });
  } catch (err) {
    console.error("Could not send to background:", err);
    // Fallback: try HTTP directly
    try {
      await fetch("http://localhost:8080/questionnaire", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(results),
      });
    } catch (fetchErr) {
      console.error("HTTP fallback failed:", fetchErr);
    }
  }

  // Show confirmation
  form.classList.add("hidden");
  conf.classList.remove("hidden");

  // Auto-close after 3 seconds
  setTimeout(() => {
    try { window.close(); } catch (_) {}
  }, 3000);
});
