const form = document.querySelector('#monitor-form');
const sample = document.querySelector('#sample');
const feedback = document.querySelector('#feedback');
const locationFor = (name, run) => {
  const url = new URL(location.href);
  url.searchParams.set('sample', name);
  if (run) url.searchParams.set('run', run);
  else url.searchParams.delete('run');
  return url;
};
sample.addEventListener('change', () => location.assign(locationFor(sample.value)));
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  feedback.hidden = false;
  feedback.dataset.failed = 'false';
  feedback.textContent = '正在复算与归档';
  try {
    const response = await fetch('/api/runs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sample: sample.value}),
    });
    const record = await response.json();
    if (!response.ok) throw new Error(record.detail);
    const target = locationFor(sample.value, record.run_id);
    if (target.href === location.href) feedback.textContent = '已复用原归档';
    else location.assign(target);
  } catch (error) {
    feedback.dataset.failed = 'true';
    feedback.textContent = error.message || '归档失败，请重试';
  } finally {
    button.disabled = false;
  }
});
const filter = document.querySelector('#state-filter');
filter?.addEventListener('change', () => {
  for (const row of document.querySelectorAll('#verdict-table tbody tr')) {
    row.hidden = filter.value !== 'all' && (filter.value === 'late' ? row.dataset.late !== 'True' : row.dataset.state !== filter.value);
  }
});
const postRule = async (url, body) => {
  feedback.hidden = false;
  feedback.dataset.failed = 'false';
  try {
    const response = await fetch(url, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const record = await response.json();
    if (!response.ok) throw new Error(record.detail);
    location.reload();
  } catch (error) {
    feedback.dataset.failed = 'true';
    feedback.textContent = error.message || '换规办理失败，请重试';
  }
};
const trialForm = document.querySelector('#trial-form');
trialForm?.addEventListener('submit', event => {
  event.preventDefault();
  feedback.textContent = '正在试算换规';
  postRule('/api/rule-changes', {
    run_id: document.querySelector('#trial-run').value,
    threshold: Number(document.querySelector('#trial-threshold').value),
  });
});
for (const button of document.querySelectorAll('[data-approve]')) {
  button.addEventListener('click', () => {
    feedback.textContent = '正在核准换规';
    postRule(`/api/rule-changes/${button.dataset.approve}/approve`, {});
  });
}
