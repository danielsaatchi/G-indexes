let currentPublications = [];
let currentMetrics = null;
let currentSort = { key: null, dir: 1 };

const $ = (id) => document.getElementById(id);

function setStatus(msg, cls) {
  const el = $('statusMsg');
  el.textContent = msg;
  el.className = 'status-msg' + (cls ? ' ' + cls : '');
}

function setAiStatus(msg) {
  $('aiStatus').textContent = msg || '';
}

function formatGCell(value, totalCitations) {
  const v = value || 0;
  if (!totalCitations) return `${v} (0%)`;
  const pct = Math.round((v / totalCitations) * 100);
  return `${v} <span class="stat-pct">(${pct}%)</span>`;
}

function setGStats(g1, g2, g3, totalCitations) {
  $('statG1').innerHTML = formatGCell(g1, totalCitations);
  $('statG2').innerHTML = formatGCell(g2, totalCitations);
  $('statG3').innerHTML = formatGCell(g3, totalCitations);
}

async function fetchProfile() {
  const url = $('scholarUrlInput').value.trim();
  if (!url) {
    setStatus('Enter a Google Scholar profile URL first.', 'err');
    return;
  }
  const resolveTruncated = $('resolveTruncated').checked;
  setStatus('Fetching profile from Google Scholar…');
  $('fetchBtn').disabled = true;
  $('emptyState').style.display = 'none';

  try {
    const resp = await fetch(
      `/api/profile?url=${encodeURIComponent(url)}&resolve_truncated=${resolveTruncated}`
    );
    const data = await resp.json();
    if (!data.ok) {
      setStatus(data.message || 'Could not fetch that profile.', 'err');
      $('profileSection').style.display = 'none';
      $('emptyState').style.display = 'block';
      return;
    }
    renderProfile(data);
    setStatus(`Loaded ${data.publications.length} publication(s).`, 'ok');
  } catch (err) {
    setStatus('Network error contacting the server: ' + err.message, 'err');
  } finally {
    $('fetchBtn').disabled = false;
  }
}

function renderProfile(data) {
  currentPublications = data.publications.map((p) => ({ ...p, position: null, position_source: null }));
  currentMetrics = null;

  $('profName').textContent = data.name || '';
  $('profAffil').textContent = data.affiliation || '';
  $('profLink').textContent = data.scholar_url;
  $('profLink').href = data.scholar_url;

  const avatarImg = $('avatarImg');
  const avatarFallback = $('avatarFallback');
  if (data.photo_proxy_url) {
    avatarImg.onerror = () => {
      avatarImg.style.display = 'none';
      avatarFallback.style.display = 'block';
    };
    avatarImg.onload = () => {
      avatarImg.style.display = 'block';
      avatarFallback.style.display = 'none';
    };
    avatarImg.src = data.photo_proxy_url + `&t=${Date.now()}`;
  } else {
    avatarImg.style.display = 'none';
    avatarFallback.style.display = 'block';
  }

  const all = data.stats && data.stats['All'] ? data.stats['All'] : null;
  $('statCitations').textContent = all ? all.citations : '–';
  $('statH').textContent = all ? all.hindex : '–';
  $('statI10').textContent = all ? all.i10index : '–';
  $('statG1').textContent = '–';
  $('statG2').textContent = '–';
  $('statG3').textContent = '–';
  setAiStatus('');

  $('pubCount').textContent = `${currentPublications.length} publications`;
  renderPubTable();
  $('profileSection').style.display = 'block';
}

function renderPubTable() {
  const body = $('pubBody');
  body.innerHTML = '';
  currentPublications.forEach((p, idx) => {
    const tr = document.createElement('tr');

    const titleTd = document.createElement('td');
    titleTd.className = 'col-title';
    const a = document.createElement('a');
    a.className = 'pub-title';
    a.textContent = p.title;
    a.href = p.link || '#';
    a.target = '_blank';
    a.rel = 'noopener';
    const meta = document.createElement('div');
    meta.className = 'pub-meta';
    meta.textContent = [p.authors, p.venue].filter(Boolean).join(' — ');
    titleTd.appendChild(a);
    titleTd.appendChild(meta);

    const citesTd = document.createElement('td');
    citesTd.className = 'pub-cites';
    citesTd.textContent = p.citations ?? 0;

    const yearTd = document.createElement('td');
    yearTd.className = 'pub-year';
    yearTd.textContent = p.year || '';

    const posTd = document.createElement('td');
    posTd.className = 'pub-pos-wrap';
    const posInput = document.createElement('input');
    posInput.type = 'number';
    posInput.min = '1';
    posInput.className = 'pub-pos';
    posInput.value = p.position ?? '';
    posInput.placeholder = '?';
    posInput.addEventListener('input', (e) => {
      const v = parseInt(e.target.value, 10);
      currentPublications[idx].position = isNaN(v) ? null : v;
      currentPublications[idx].position_source = 'manual';
      recomputeMetricsFromCurrentPositions();
    });
    posTd.appendChild(posInput);
    if (p.position_source) {
      const srcSpan = document.createElement('span');
      srcSpan.className = 'pos-source ' + p.position_source;
      srcSpan.textContent = p.position_source;
      posTd.appendChild(srcSpan);
    }

    tr.appendChild(titleTd);
    tr.appendChild(citesTd);
    tr.appendChild(yearTd);
    tr.appendChild(posTd);
    body.appendChild(tr);
  });
}

function recomputeMetricsFromCurrentPositions() {
  const citations = currentPublications.map((p) => p.citations || 0);
  const total = citations.reduce((a, b) => a + b, 0);
  const sorted = [...citations].sort((a, b) => b - a);
  let h = 0;
  for (let i = 0; i < sorted.length; i++) {
    if (sorted[i] >= i + 1) h = i + 1;
    else break;
  }
  const i10 = citations.filter((c) => c >= 10).length;

  const gSum = (maxPos) =>
    currentPublications
      .filter((p) => p.position != null && p.position <= maxPos)
      .reduce((a, p) => a + (p.citations || 0), 0);

  const g1 = gSum(1), g2 = gSum(2), g3 = gSum(3);

  $('statCitations').textContent = total;
  $('statH').textContent = h;
  $('statI10').textContent = i10;
  setGStats(g1, g2, g3, total);
}

async function analyze() {
  if (!currentPublications.length) return;
  const authorName = $('profName').textContent.trim();
  const ollamaUrl = $('ollamaUrlInput').value.trim() || 'http://localhost:11434';
  const ollamaModel = $('modelInput').value.trim();

  $('analyzeBtn').disabled = true;
  setAiStatus(
    ollamaModel
      ? `Asking local Ollama model "${ollamaModel}" to resolve authorship positions…`
      : 'No model set — using surname/initial heuristic only…'
  );

  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        author_name: authorName,
        ollama_url: ollamaUrl,
        ollama_model: ollamaModel,
        use_ai: !!ollamaModel,
        publications: currentPublications.map((p) => ({
          title: p.title,
          authors: p.authors,
          citations: p.citations,
        })),
      }),
    });
    const data = await resp.json();
    if (!data.ok) {
      setAiStatus(data.message || 'Analysis failed.');
      return;
    }
    currentPublications = currentPublications.map((p, idx) => ({
      ...p,
      position: data.results[idx].position,
      position_source: data.results[idx].position_source,
    }));
    currentMetrics = data.metrics;

    $('statCitations').textContent = data.metrics.total_citations;
    $('statH').textContent = data.metrics.hindex;
    $('statI10').textContent = data.metrics.i10index;
    setGStats(data.metrics.g1, data.metrics.g2, data.metrics.g3, data.metrics.total_citations);

    renderPubTable();
    setAiStatus(data.ai_status);
  } catch (err) {
    setAiStatus('Could not reach the server: ' + err.message);
  } finally {
    $('analyzeBtn').disabled = false;
  }
}

function sortBy(key) {
  currentSort.dir = currentSort.key === key ? -currentSort.dir : -1;
  currentSort.key = key;
  currentPublications.sort((a, b) => {
    const av = key === 'year' ? parseInt(a.year, 10) || 0 : a.citations || 0;
    const bv = key === 'year' ? parseInt(b.year, 10) || 0 : b.citations || 0;
    return (av - bv) * currentSort.dir;
  });
  renderPubTable();
}

$('fetchBtn').addEventListener('click', fetchProfile);
$('scholarUrlInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchProfile();
});
$('analyzeBtn').addEventListener('click', analyze);
$('sortYear').addEventListener('click', (e) => { e.preventDefault(); sortBy('year'); });
$('sortCitations').addEventListener('click', (e) => { e.preventDefault(); sortBy('citations'); });

// Prefill with the profile the user gave.
window.addEventListener('DOMContentLoaded', () => {
  $('scholarUrlInput').value = 'https://scholar.google.com/citations?user=Uv7KQxEAAAAJ&hl=en';
  if (!$('modelInput').value) $('modelInput').value = 'gemma4:e4b';
});
