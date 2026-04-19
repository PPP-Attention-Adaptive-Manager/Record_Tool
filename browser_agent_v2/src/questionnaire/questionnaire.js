/**
 * Questionnaire page controller.
 *
 * Reads session_id and user_id from URL search params (injected by
 * SessionManager.endSession).  On submit, builds a questionnaire event object
 * and sends it to the service worker for InfluxDB storage.
 */
import { createQuestionnaireEvent } from '../shared/schemas.js';

// ── Read session context from URL ─────────────────────────────────────────────
const params     = new URLSearchParams(location.search);
const sessionId  = params.get('session_id') || 'unknown';
const userId     = params.get('user_id')    || 'anonymous';

// ── Wire live value display for every slider ──────────────────────────────────
const SLIDER_IDS = [
  'mental_demand', 'physical_demand', 'temporal_demand',
  'performance', 'effort', 'frustration',
  'stress_self_report',
  'valence', 'arousal',
];

for (const id of SLIDER_IDS) {
  const slider = document.getElementById(id);
  const display = document.getElementById(`val-${id}`);
  if (!slider || !display) continue;

  display.textContent = slider.value;
  slider.addEventListener('input', () => {
    display.textContent = slider.value;
  });
}

// ── Submit ─────────────────────────────────────────────────────────────────────
document.getElementById('btn-submit').addEventListener('click', async () => {
  const btn = document.getElementById('btn-submit');
  btn.disabled = true;

  const readSlider = (id) => parseInt(document.getElementById(id)?.value ?? '0', 10);

  const event = createQuestionnaireEvent({
    session_id: sessionId,
    user_id:    userId,

    // NASA-TLX
    mental_demand:    readSlider('mental_demand'),
    physical_demand:  readSlider('physical_demand'),
    temporal_demand:  readSlider('temporal_demand'),
    performance:      readSlider('performance'),
    effort:           readSlider('effort'),
    frustration:      readSlider('frustration'),

    // Stress
    stress_self_report: readSlider('stress_self_report'),

    // Affect
    valence: readSlider('valence'),
    arousal: readSlider('arousal'),
  });

  try {
    await send({ type: 'SUBMIT_QUESTIONNAIRE', payload: event });
  } catch (err) {
    console.error('[questionnaire] submit error:', err);
  }

  // Show confirmation regardless of write success
  document.querySelector('.container').style.display = 'none';
  const done = document.getElementById('done-msg');
  done.classList.add('visible');
  document.querySelector('.container').appendChild(done);
  done.style.display = 'block';
  document.querySelector('.container').style.display = '';
});

// ── Helper ─────────────────────────────────────────────────────────────────────
function send(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else resolve(response);
    });
  });
}
