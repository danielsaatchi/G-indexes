# G-Indexes
Forensic G-indexes for researchers powered by Ollama AI-Agent &amp; Google Scholar 

<img width="3670" height="5739" alt="image" src="https://github.com/user-attachments/assets/392f46dd-4a38-4f10-966f-777ee2d635c2" />

## G-Index Scholar Agent (local AI-Agent app)
A small local web app that reconstructs a Google Scholar "Citations" page and
adds three extra indicators — **G1, G2, G3** — computed from the
Graded Authorship-Filtered Citation Metrics (GAFCM) framework:

- **G1-index** = citations from publications where you are the 1st author
- **G2-index** = citations from publications where you are 1st or 2nd author
- **G3-index** = citations from publications where you are 1st, 2nd, or 3rd author
- **GRW-index** = citations from reviews for authorship inflation and citation anomaly

It runs entirely on your machine:
- The Flask server scrapes the public Google Scholar profile you give it
  (server-side, so there's no browser CORS/blocking issue).
- The server also talks to your local Ollama instance to help determine your
  authorship position in each paper (optional — a surname/initial heuristic
  runs even without it, and you can always correct positions by hand).

## Setup

```bash
cd gindex_app
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000** or **http://127.0.0.1:5000** in your browser.

## Using it

1. The Scholar URL field is pre-filled with default profile of DANIEL SAATCHI
   (`https://scholar.google.com/citations?user=Uv7KQxEAAAAJ&hl=en`) — change
   it if needed.
2. Set **Ollama model** to whatever you have pulled locally, e.g. `gemma4:latest`
   (Ollama's current naming — if you literally have a model tagged
   `gemma4:e4b`, type that exact tag; just make sure it's been pulled with
   `ollama pull <model>` first).
3. Leave **Ollama server** as `http://localhost:11434` or `http://127.0.0.1:11434` unless yours runs
   elsewhere.
4. Click **Fetch profile** — this pulls your full publication list, citation
   counts, and Scholar's own Citations/h-index/i10-index, laid out like your
   real Scholar "Citations" page.
5. Click **Compute G1 / G2 / G3 / GRw** — the server sends each paper's author
   byline to your local Ollama model, asks it to find your position in the
   author list, falls back to a heuristic match where the model can't decide,
   and then computes G1/G2/G3 (and re-derives Citations/h-index/i10-index)
   from the results.
6. Every row's **Position** box is editable — fix it directly for any paper
   (equal-contribution, alphabetical ordering, consortium authorship, etc.)
   and the totals recompute instantly.
7. The same **Compute** step also classifies each paper as a review-type
   publication (literature/systematic review, meta-analysis, survey) using a
   title-keyword heuristic and/or the Ollama model, giving a **GRW-Index**
   (citations from review papers) and **GRW%**. Each row has a **Review?**
   checkbox you can correct by hand.
8. Click **Add to Dataset (Excel)** to append the current profile's full row
   (name, Scholar URL, Citations/h/i10, G1–G3, GRW, and their percentages,
   affiliation, detected title, and inferred country) to a cumulative file,
   `gindex_dataset.xlsx`, in the app folder. Load a new profile and repeat —
   each one adds a new row, building a dataset over time. Use **Download
   gindex_dataset.xlsx** to grab the file at any point.

## Batch Dataset Builder (automated, many profiles)

Below the toolbar, click **"Batch Dataset Builder"** to expand it:

1. Paste one Google Scholar profile URL per line (up to 1000 in a single
   batch).
2. Set the **delay between profiles** (default 5 seconds) — this is your
   main defense against Google Scholar's rate limiting/CAPTCHA wall, since
   every profile is a handful of real requests to scholar.google.com.
   Start conservative; lower it only once you've confirmed a run doesn't
   trigger a block.
3. Click **Start batch**. The server processes profiles one at a time in the
   background (fetch → authorship/review classification → append dataset
   row), and the page polls for progress every 2 seconds — you can navigate
   away and come back, the job keeps running server-side.
4. Each profile becomes one row in the same cumulative `gindex_dataset.xlsx`
   used by the single-profile "Add to Dataset" button, so batch runs and
   manual runs build the same dataset.
5. **Cancel** stops the batch after the profile currently in flight
   finishes; already-saved rows stay in the dataset.

**Before running hundreds or 1000 profiles at once:** start with a small
batch (5–10 URLs) first to confirm your delay setting, your Ollama model's
speed, and that Scholar isn't CAPTCHA-blocking you. A batch of 1000 profiles
run sequentially, even at a modest delay, can take many hours — there is no
parallelism, by design, since concurrent requests to Scholar are far more
likely to trigger a block than a slower sequential run.

## Notes

- Google Scholar occasionally shows a CAPTCHA to automated requests. If
  `Fetch profile` fails with a bot-check message, wait a few minutes and
  retry — this is Scholar's rate limiting, not a bug in the app.
- Author lists on the Citations page are sometimes truncated with "…". Check
  **resolve truncated author lists** before fetching to have the app pull the
  full author list from each paper's detail page (slower — capped at 15
  papers per fetch to stay polite to Scholar).
- If the Ollama call fails (model not pulled, server not running, wrong
  port), the app still computes G1/G2/G3 from the heuristic surname match and
  tells you so in the status line next to "Compute G1 / G2 / G3".
- **Country detection is a best-effort guess, not a verified lookup.** This
  app has no live web-search integration — country is inferred only from (a)
  a local keyword list of country names/abbreviations and well-known
  universities found in the Scholar affiliation string, and (b) if that
  fails and an Ollama model is set, asking the model's own knowledge for a
  guess. Neither step actually searches the web. If a profile's affiliation
  doesn't match the built-in keyword list and the model can't resolve it
  either, the Country column is left blank ("unresolved") rather than
  guessed — check and correct the `gindex_dataset.xlsx` file by hand before
  using it for anything downstream.
- The dataset file is cumulative and never overwritten automatically — each
  "Add to Dataset" click appends one row, so re-adding the same profile
  twice creates a duplicate row (there's no de-duplication logic by design,
  since you may want to track the same profile at different snapshot
  dates).


## Citation
If you use this repository in your research, please cite:

Saatchi, D. (2026). *Forensics G-Indexes (G1, G2, G3): Graded Authorship-Filtered Citation Metrics for Researchers by AI-Agent and Google Scholar*. AI Journal of Arts & Metascience (AIJAM). https://zenodo.org/records/24285314 & https://github.com/danielsaatchi/G-indexes
