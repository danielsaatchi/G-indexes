# G-Indexes
Anti-Engineered G-indexes for researhers forensics powered by Ollama AI-Agent &amp; Google Scholar 
<img width="3814" height="5046" alt="image" src="https://github.com/user-attachments/assets/11600cb2-e87d-44a3-b752-494a4ceb528b" />
->/

## G-Index Scholar Agent (local AI-Agent app)
A small local web app that reconstructs a Google Scholar "Citations" page and
adds three extra indicators — **G1, G2, G3** — computed from the
Graded Authorship-Filtered Citation Metrics (GAFCM) framework:

- **G1-index** = citations from publications where you are the 1st author
- **G2-index** = citations from publications where you are 1st or 2nd author
- **G3-index** = citations from publications where you are 1st, 2nd, or 3rd author

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

1. The Scholar URL field is pre-filled with your profile
   (`https://scholar.google.com/citations?user=Uv7KQxEAAAAJ&hl=en`) — change
   it if needed.
2. Set **Ollama model** to whatever you have pulled locally, e.g. `gemma4:latest`
   (Ollama's current naming — if you literally have a model tagged
   `gemma4:latest`, type that exact tag; just make sure it's been pulled with
   `ollama pull <model>` first).
3. Leave **Ollama server** as `http://localhost:11434` or `http://127.0.0.1:11434` unless yours runs
   elsewhere.
4. Click **Fetch profile** — this pulls your full publication list, citation
   counts, and Scholar's own Citations/h-index/i10-index, laid out like your
   real Scholar "Citations" page.
5. Click **Compute G1 / G2 / G3** — the server sends each paper's author
   byline to your local Ollama model, asks it to find your position in the
   author list, falls back to a heuristic match where the model can't decide,
   and then computes G1/G2/G3 (and re-derives Citations/h-index/i10-index)
   from the results.
6. Every row's **Position** box is editable — fix it directly for any paper
   (equal-contribution, alphabetical ordering, consortium authorship, etc.)
   and the totals recompute instantly.

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
