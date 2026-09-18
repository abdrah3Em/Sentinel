// Dark is the design's native theme; apply a saved/requested light theme before first paint.
try {
  const t = new URLSearchParams(location.search).get('theme') || localStorage.getItem('sentinel-theme');
  if (t === 'light') document.documentElement.dataset.theme = 'light';
} catch (e) {}
