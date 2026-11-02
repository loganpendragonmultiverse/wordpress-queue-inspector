(() => {
  const main = document.querySelector('main[data-site]');
  if (!main) return;
  const key = `wqi-selection:${main.dataset.site}:${main.dataset.capture}`;
  let selected;
  try { selected = new Set(JSON.parse(localStorage.getItem(key) || '[]')); }
  catch { selected = new Set(); }
  const status = document.getElementById('selection-count');
  const update = () => { status.textContent = `${selected.size} jobs selected across pages and filters in this browser`; };
  document.querySelectorAll('.job-select').forEach(box => {
    box.checked = selected.has(box.value);
    box.addEventListener('change', () => {
      if (box.checked) selected.add(box.value); else selected.delete(box.value);
      try { localStorage.setItem(key, JSON.stringify([...selected])); }
      catch { status.textContent = 'Selection could not be saved in this browser'; return; }
      update();
    });
  });
  update();
})();
