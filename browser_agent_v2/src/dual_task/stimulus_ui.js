/**
 * Dual-task stimulus UI — modular implementation.
 *
 * NOTE: Logic is inlined in content_script.js for deployment.
 *       See scroll_tracker.js header for rationale.
 *
 * Displays a minimal overlay on the active page.
 * The user must click or press Space/Enter within RESPONSE_WINDOW_MS.
 *
 * Collected fields:
 *   reaction_time   ms from stimulus appearance to response
 *   success         1 if responded in time
 *   error           0 (reserved — always 0 for click/space stimuli)
 *   missed_response 1 if no response (auto-closes after window)
 */

const RESPONSE_WINDOW_MS = 3_000;

export function showStimulus(probeId, onResult) {
  const triggerMs  = Date.now();
  let responded    = false;

  // ── Overlay element ───────────────────────────────────────────────────────
  const overlay = document.createElement('div');
  overlay.setAttribute('id', 'cog-probe-overlay');
  overlay.style.cssText = `
    all: initial;
    position: fixed;
    top: 20px;
    right: 20px;
    z-index: 2147483647;
    background: rgba(15, 15, 15, 0.92);
    color: #ffffff;
    padding: 14px 20px;
    border-radius: 8px;
    border: 2px solid #4ade80;
    font: 600 14px/1.4 system-ui, sans-serif;
    cursor: pointer;
    box-shadow: 0 4px 16px rgba(0,0,0,0.5);
    user-select: none;
    animation: cog-probe-in 120ms ease-out;
  `;

  // Scoped keyframe so we don't pollute the host page's styles
  const style = document.createElement('style');
  style.textContent = `
    @keyframes cog-probe-in {
      from { opacity: 0; transform: translateY(-8px); }
      to   { opacity: 1; transform: translateY(0); }
    }
  `;
  document.head.appendChild(style);

  overlay.innerHTML = `
    <div style="margin-bottom:6px;letter-spacing:.5px;">● PRESS SPACE or CLICK</div>
    <div style="font-weight:400;font-size:12px;opacity:.65;">Respond as fast as you can</div>
  `;

  document.body.appendChild(overlay);

  // ── Response handlers ─────────────────────────────────────────────────────

  const respond = () => {
    if (responded) return;
    responded = true;
    cleanup();
    onResult({
      probeId,
      reaction_time: Date.now() - triggerMs,
      success: 1,
      error:   0,
    });
  };

  const onKey = (e) => {
    if (e.code === 'Space' || e.code === 'Enter') {
      e.preventDefault();
      respond();
    }
  };

  overlay.addEventListener('click', respond);
  document.addEventListener('keydown', onKey, { capture: true });

  // ── Auto-expire ───────────────────────────────────────────────────────────
  const expireTimer = setTimeout(() => {
    if (responded) return;
    responded = true;
    cleanup();
    onResult({
      probeId,
      reaction_time: -1,
      success: 0,
      error:   0,
    });
  }, RESPONSE_WINDOW_MS);

  function cleanup() {
    clearTimeout(expireTimer);
    document.removeEventListener('keydown', onKey, { capture: true });
    overlay.remove();
    style.remove();
  }
}
