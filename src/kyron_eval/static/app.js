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
  button.disabled = true; button.textContent = 'Replaying and evaluating…'; error.textContent = '';
  try {
    const run = await postJSON('/api/runs', {variants: data.get('variant') === 'both' ? ['A', 'B'] : [data.get('variant')], scenario_ids: data.get('scenario') === 'all' ? null : [data.get('scenario')]});
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
