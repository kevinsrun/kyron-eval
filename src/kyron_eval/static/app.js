async function postJSON(url, data) {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
const runForm = document.querySelector('#run-form');
runForm?.addEventListener('submit', async event => {
  event.preventDefault();
  const button = runForm.querySelector('button');
  const error = document.querySelector('#run-error');
  const data = new FormData(runForm);
  button.disabled = true; button.textContent = 'Executing and evaluating…'; error.textContent = '';
  try {
    const fault = data.get('fault');
    const faults = fault === 'none' ? {} : {[fault === 'readback_failure' ? 'read_state' : fault === 'stale' ? 'get_availability' : 'create_appointment']: fault};
    const run = await postJSON('/api/runs', {adapter: data.get('adapter'), ehr_faults: faults, variants: data.get('variant') === 'both' ? ['A', 'B'] : [data.get('variant')], scenario_ids: data.get('scenario') === 'all' ? null : [data.get('scenario')]});
    window.location.assign('/runs/' + encodeURIComponent(run.id));
  } catch (failure) { error.textContent = failure.message; button.disabled = false; button.textContent = 'Run evaluation'; }
});
document.querySelectorAll('.label-form').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  const result = form.querySelector('.form-result');
  const data = new FormData(form);
  button.disabled = true;
  try {
    await postJSON(`/api/runs/${encodeURIComponent(form.dataset.run)}/labels`, {scenario_id: form.dataset.scenario, variant: form.dataset.variant, trace_sha256: form.dataset.trace, label: data.get('label'), rationale: data.get('rationale'), reviewer: data.get('reviewer')});
    result.textContent = 'Human label saved. Reload to inspect the annotation, or open Human review.';
  } catch (failure) { result.textContent = failure.message; }
  finally { button.disabled = false; }
}));

document.querySelector('#ehr-probe')?.addEventListener('click', async event => {
  const output = document.querySelector('#ehr-probe-result');
  event.target.disabled = true; output.textContent = 'Discovering capabilities and reading synthetic resources…';
  try { output.textContent = JSON.stringify(await postJSON('/api/ehr/probe', {}), null, 2); }
  catch (failure) { output.textContent = failure.message; }
  finally { event.target.disabled = false; }
});
