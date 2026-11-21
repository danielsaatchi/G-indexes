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

function setReviewStatus(msg) {
  $('reviewStatus').textContent = msg || '';
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

function setGrwStat(grw, totalCitations) {
  $('statGRW').innerHTML = formatGCell(grw, totalCitations);
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
  $('exportRowBtn').disabled = true;
  $('exportStatus').textContent = '';

  $('profName').textContent = data.name || '';
  $('profAffil').textContent = data.affiliation || '';
  $('profLink').textContent = data.scholar_url;
  $('profLink').href = data.scholar_url;

  const interestsEl = $('profInterests');
  interestsEl.innerHTML = '';
  (data.interests || []).forEach((tag) => {
    const span = document.createElement('span');
    span.className = 'interest-tag';
    span.textContent = tag;
    interestsEl.appendChild(span);
  });

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
  renderYearChart(data.citations_by_year || {});
  renderPubTable();
  $('profileSection').style.display = 'block';
}

function renderYearChart(citationsByYear) {
  const container = $('yearChart');
  container.innerHTML = '';
  const years = Object.keys(citationsByYear);
  if (!years.length) {
    container.parentElement.style.display = 'none';
    return;
  }
  container.parentElement.style.display = 'block';

  const counts = years.map((y) => citationsByYear[y] || 0);
  const maxCount = Math.max(...counts, 1);
  // round axis max up to a "nice" number
  const niceMax = Math.ceil(maxCount / 10) * 10 || 10;

  years.forEach((year, idx) => {
    const col = document.createElement('div');
    col.className = 'year-bar-col';
    col.title = `${year}: ${counts[idx]} citation(s)`;

    const countLabel = document.createElement('div');
    countLabel.className = 'year-bar-count';
    countLabel.textContent = counts[idx] > 0 ? counts[idx] : '';

    const bar = document.createElement('div');
    bar.className = 'year-bar';
    const heightPct = (counts[idx] / niceMax) * 100;
    bar.style.height = heightPct > 0 ? `${heightPct}%` : '1px';

    col.appendChild(countLabel);
    col.appendChild(bar);
    container.appendChild(col);
  });

  const axis = document.createElement('div');
  axis.className = 'year-axis';
  axis.innerHTML = `<span>${niceMax}</span><span>${Math.round(niceMax / 2)}</span><span>0</span>`;
  container.appendChild(axis);

  const labelsRow = $('yearChartLabels') || document.createElement('div');
  labelsRow.id = 'yearChartLabels';
  labelsRow.className = 'year-labels';
  labelsRow.innerHTML = '';
  years.forEach((year) => {
    const lbl = document.createElement('div');
    lbl.className = 'year-label-col';
    lbl.textContent = year;
    labelsRow.appendChild(lbl);
  });
  if (!labelsRow.parentElement) {
    container.insertAdjacentElement('afterend', labelsRow);
  }
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

    const reviewTd = document.createElement('td');
    reviewTd.className = 'pub-review-wrap';
    const reviewCheckbox = document.createElement('input');
    reviewCheckbox.type = 'checkbox';
    reviewCheckbox.checked = !!p.is_review;
    reviewCheckbox.addEventListener('change', (e) => {
      currentPublications[idx].is_review = e.target.checked;
      currentPublications[idx].review_source = 'manual';
      recomputeMetricsFromCurrentPositions();
    });
    reviewTd.appendChild(reviewCheckbox);
    if (p.review_source) {
      const revSrcSpan = document.createElement('span');
      revSrcSpan.className = 'review-source ' + p.review_source;
      revSrcSpan.textContent = p.review_source;
      reviewTd.appendChild(revSrcSpan);
    }

    tr.appendChild(titleTd);
    tr.appendChild(citesTd);
    tr.appendChild(yearTd);
    tr.appendChild(posTd);
    tr.appendChild(reviewTd);
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

  const grw = currentPublications
    .filter((p) => p.is_review)
    .reduce((a, p) => a + (p.citations || 0), 0);

  $('statCitations').textContent = total;
  $('statH').textContent = h;
  $('statI10').textContent = i10;
  setGStats(g1, g2, g3, total);
  setGrwStat(grw, total);
}

let lastAnalyzeElapsedSeconds = null;

async function analyze() {
  if (!currentPublications.length) return;
  const authorName = $('profName').textContent.trim();
  const ollamaUrl = $('ollamaUrlInput').value.trim() || 'http://localhost:11434';
  const ollamaModel = $('modelInput').value.trim();
  const startTime = performance.now();

  $('analyzeBtn').disabled = true;
  setAiStatus(
    ollamaModel
      ? `Asking local Ollama model "${ollamaModel}" to resolve authorship positions…`
      : 'No model set — using surname/initial heuristic only…'
  );
  setReviewStatus(
    ollamaModel
      ? `Asking "${ollamaModel}" to classify review-type publications…`
      : 'No model set — using title-keyword heuristic only…'
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
      is_review: data.results[idx].is_review,
      review_source: data.results[idx].review_source,
    }));
    currentMetrics = data.metrics;
    lastAnalyzeElapsedSeconds = (performance.now() - startTime) / 1000;

    $('statCitations').textContent = data.metrics.total_citations;
    $('statH').textContent = data.metrics.hindex;
    $('statI10').textContent = data.metrics.i10index;
    setGStats(data.metrics.g1, data.metrics.g2, data.metrics.g3, data.metrics.total_citations);
    setGrwStat(data.metrics.grw, data.metrics.total_citations);

    renderPubTable();
    setAiStatus(data.ai_status);
    setReviewStatus(
      `${data.review_status} — ${data.metrics.review_count} of ${data.metrics.publication_count} publications flagged as reviews. ` +
      `(computed in ${lastAnalyzeElapsedSeconds.toFixed(1)}s)`
    );
    $('exportRowBtn').disabled = false;
  } catch (err) {
    setAiStatus('Could not reach the server: ' + err.message);
    setReviewStatus('');
  } finally {
    $('analyzeBtn').disabled = false;
  }
}

async function exportRow() {
  if (!currentMetrics) return;
  const authorName = $('profName').textContent.trim();
  const affiliation = $('profAffil').textContent.trim();
  const scholarUrl = $('profLink').href;
  const ollamaUrl = $('ollamaUrlInput').value.trim() || 'http://localhost:11434';
  const ollamaModel = $('modelInput').value.trim();

  $('exportRowBtn').disabled = true;
  $('exportStatus').textContent = 'Writing row to gindex_dataset.xlsx…';
  $('exportStatus').className = 'ai-status';

  try {
    const resp = await fetch('/api/export_row', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        author_name: authorName,
        scholar_url: scholarUrl,
        affiliation: affiliation,
        ollama_url: ollamaUrl,
        ollama_model: ollamaModel,
        use_ai: !!ollamaModel,
        metrics: currentMetrics,
        elapsed_seconds: lastAnalyzeElapsedSeconds,
      }),
    });
    const data = await resp.json();
    if (!data.ok) {
      $('exportStatus').textContent = data.message || 'Could not write dataset row.';
      $('exportStatus').className = 'ai-status err';
      return;
    }
    $('exportStatus').textContent =
      `Saved row ${data.row_count} to gindex_dataset.xlsx` +
      (data.country ? ` (country: ${data.country}, ${data.country_source})` : ' (country: unresolved)') +
      (lastAnalyzeElapsedSeconds ? ` — runtime ${lastAnalyzeElapsedSeconds.toFixed(1)}s.` : '.');
    $('exportStatus').className = 'ai-status ok';
  } catch (err) {
    $('exportStatus').textContent = 'Could not reach the server: ' + err.message;
    $('exportStatus').className = 'ai-status err';
  } finally {
    $('exportRowBtn').disabled = false;
  }
}

$('exportRowBtn').addEventListener('click', exportRow);

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

// ---------- Batch Dataset Builder ----------

let batchPollTimer = null;

function formatSeconds(s) {
  if (s === null || s === undefined) return '–';
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = s - m * 60;
  return `${m}m ${rem.toFixed(0)}s`;
}

function renderBatchRow(idx, item) {
  const tr = document.createElement('tr');
  const label = item.name || item.url;
  const resultCls = item.ok ? 'result-ok' : 'result-fail';
  const resultText = item.ok
    ? `✓ ${item.message}`
    : `✗ ${item.message}`;
  tr.innerHTML = `
    <td>${idx + 1}</td>
    <td>${label}</td>
    <td class="${resultCls}">${resultText}</td>
    <td>${formatSeconds(item.elapsed_seconds)}</td>
  `;
  return tr;
}

function renderBatchProgress(job) {
  $('batchProgressWrap').style.display = 'block';
  const pct = job.total ? Math.round((job.processed / job.total) * 100) : 0;
  $('batchProgressFill').style.width = pct + '%';
  $('batchProgressText').textContent =
    `${job.status} — ${job.processed}/${job.total} processed ` +
    `(${job.succeeded} saved, ${job.failed} failed)`;

  const body = $('batchResultsBody');
  body.innerHTML = '';
  job.items.forEach((item, idx) => {
    body.appendChild(renderBatchRow(idx, item));
  });

  const sumProfile = job.sum_profile_elapsed_seconds || 0;
  const batchWall = job.batch_elapsed_seconds || 0;
  const totalsEl = $('batchTotals');
  if (job.processed > 0) {
    totalsEl.textContent =
      `Sum of per-profile runtime: ${formatSeconds(sumProfile)} · ` +
      `Total batch runtime (incl. delays): ${formatSeconds(batchWall)}` +
      (job.status === 'done' || job.status === 'cancelled'
        ? ' — also written as a summary row at the end of gindex_dataset.xlsx.'
        : '');
  } else {
    totalsEl.textContent = '';
  }
}

async function pollBatchStatus(jobId) {
  try {
    const resp = await fetch(`/api/batch_status/${jobId}`);
    const data = await resp.json();
    if (!data.ok) {
      clearInterval(batchPollTimer);
      $('batchProgressText').textContent = data.message || 'Lost track of the batch job.';
      $('batchStartBtn').disabled = false;
      $('batchCancelBtn').disabled = true;
      return;
    }
    renderBatchProgress(data.job);
    if (data.job.status === 'done' || data.job.status === 'cancelled') {
      clearInterval(batchPollTimer);
      $('batchStartBtn').disabled = false;
      $('batchCancelBtn').disabled = true;
    }
  } catch (err) {
    // transient network hiccup while polling; keep trying on the next tick
  }
}

async function startBatch() {
  const raw = $('batchUrls').value;
  const urls = raw.split('\n').map((s) => s.trim()).filter(Boolean);
  if (!urls.length) {
    alert('Paste at least one Google Scholar profile URL first.');
    return;
  }
  if (urls.length > 1000) {
    alert('This tool caps a single batch at 1000 URLs. Split the list into smaller batches.');
    return;
  }

  const ollamaUrl = $('ollamaUrlInput').value.trim() || 'http://localhost:11434';
  const ollamaModel = $('modelInput').value.trim();
  const resolveTruncated = $('batchResolveTruncated').checked;
  const delaySeconds = parseFloat($('batchDelay').value) || 5;

  $('batchStartBtn').disabled = true;
  $('batchCancelBtn').disabled = false;
  $('batchProgressWrap').style.display = 'block';
  $('batchProgressText').textContent = `Starting batch of ${urls.length} profile(s)…`;
  $('batchResultsBody').innerHTML = '';
  $('batchProgressFill').style.width = '0%';

  try {
    const resp = await fetch('/api/batch_start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        urls,
        ollama_url: ollamaUrl,
        ollama_model: ollamaModel,
        use_ai: !!ollamaModel,
        resolve_truncated: resolveTruncated,
        delay_seconds: delaySeconds,
      }),
    });
    const data = await resp.json();
    if (!data.ok) {
      $('batchProgressText').textContent = data.message || 'Could not start batch job.';
      $('batchStartBtn').disabled = false;
      $('batchCancelBtn').disabled = true;
      return;
    }
    const jobId = data.job_id;
    batchPollTimer = setInterval(() => pollBatchStatus(jobId), 2000);
    pollBatchStatus(jobId);
    $('batchCancelBtn').onclick = async () => {
      $('batchCancelBtn').disabled = true;
      await fetch(`/api/batch_cancel/${jobId}`, { method: 'POST' });
    };
  } catch (err) {
    $('batchProgressText').textContent = 'Could not reach the server: ' + err.message;
    $('batchStartBtn').disabled = false;
    $('batchCancelBtn').disabled = true;
  }
}

$('batchStartBtn').addEventListener('click', startBatch);
