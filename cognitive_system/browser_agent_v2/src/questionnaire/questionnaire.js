const sliderFields = [
  "mental_demand",
  "physical_demand",
  "temporal_demand",
  "performance",
  "effort",
  "frustration",
  "stress_self_report",
  "valence",
  "arousal",
];

const sessionId =
  new URLSearchParams(window.location.search).get("session_id") || "";
const sessionEl = document.getElementById("session-id");
sessionEl.textContent = `Session: ${sessionId || "-"}`;

for (const name of sliderFields) {
  const input = document.getElementById(name);
  const output = document.getElementById(`out_${name}`);
  if (!input || !output) continue;
  const sync = () => {
    output.textContent = input.value;
  };
  input.addEventListener("input", sync);
  sync();
}

const form = document.getElementById("questionnaire-form");
const submitButton = document.getElementById("submit");
const donePanel = document.getElementById("done");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submitButton.disabled = true;

  const results = {
    session_id: sessionId,
    task_description: document.getElementById("task_description")?.value.trim() || "",
  };
  for (const name of sliderFields) {
    const input = document.getElementById(name);
    results[name] = Number(input?.value || 0);
  }

  try {
    await chrome.runtime.sendMessage({
      type: "questionnaire_submit",
      results,
    });
  } catch (_) {}

  form.classList.add("hidden");
  donePanel.classList.remove("hidden");
});
