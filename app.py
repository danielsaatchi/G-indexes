"""
G-Index Scholar Metrics — local operational web app.

Runs a small Flask server that:
  1. Scrapes a given public Google Scholar profile (server-side, so no browser
     CORS restrictions apply) and extracts profile info + full publication list.
  2. Proxies requests to a local Ollama instance (also server-side) to help
     determine each publication's authorship position for the target researcher.
  3. Computes Citations / h-index / i10-index / G1 / G2 / G3 deterministically
     from the resulting (citations, position) pairs, per the GAFCM framework.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000
"""

import re
import time
import unicodedata
from urllib.parse import urlparse, parse_qs, urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

SCHOLAR_BASE = "https://scholar.google.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
PAGE_SIZE = 100
MAX_PAGES = 20           # safety cap: up to 2000 publications
MAX_DETAIL_FETCHES = 15  # cap on extra per-publication detail-page fetches


def extract_user_id(scholar_url: str) -> str:
    """Pull the `user` query parameter out of a full Google Scholar profile URL."""
    parsed = urlparse(scholar_url.strip())
    qs = parse_qs(parsed.query)
    if "user" in qs and qs["user"]:
        return qs["user"][0]
    # Fall back: maybe they pasted just the ID itself.
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", scholar_url.strip()):
        return scholar_url.strip()
    raise ValueError("Could not find a 'user=' parameter in that Google Scholar URL.")


def looks_blocked(html: str) -> bool:
    markers = ["Our systems have detected unusual traffic", "/sorry/", "captcha"]
    low = html.lower()
    return any(m.lower() in low for m in markers)


def fetch_profile_page(session: requests.Session, user_id: str, cstart: int):
    url = (
        f"{SCHOLAR_BASE}/citations?user={user_id}&hl=en"
        f"&cstart={cstart}&pagesize={PAGE_SIZE}"
    )
    resp = session.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    if looks_blocked(resp.text):
        raise RuntimeError(
            "Google Scholar returned a bot-check / CAPTCHA page instead of profile "
            "data. Wait a few minutes before retrying, or use the manual-paste "
            "fallback."
        )
    return resp.text


def parse_stats_table(soup: BeautifulSoup):
    table = soup.find("table", id="gsc_rsb_st")
    stats = {}
    if not table:
        return stats
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        period = tds[0].get_text(strip=True)
        try:
            citations = int(tds[1].get_text(strip=True) or 0)
            hindex = int(tds[2].get_text(strip=True) or 0)
            i10 = int(tds[3].get_text(strip=True) or 0)
        except ValueError:
            continue
        stats[period] = {"citations": citations, "hindex": hindex, "i10index": i10}
    return stats


def parse_publications(soup: BeautifulSoup):
    rows = soup.select("#gsc_a_b .gsc_a_tr")
    pubs = []
    for row in rows:
        title_a = row.select_one("td.gsc_a_t a.gsc_a_at")
        if not title_a:
            continue
        title = title_a.get_text(strip=True)
        href = title_a.get("href", "")
        link = urljoin(SCHOLAR_BASE, href) if href else None

        gray_divs = row.select("td.gsc_a_t div.gs_gray")
        authors = gray_divs[0].get_text(strip=True) if len(gray_divs) > 0 else ""
        venue = gray_divs[1].get_text(strip=True) if len(gray_divs) > 1 else ""

        cite_a = row.select_one("td.gsc_a_c a.gsc_a_ac")
        cite_text = cite_a.get_text(strip=True) if cite_a else ""
        try:
            citations = int(cite_text) if cite_text else 0
        except ValueError:
            citations = 0

        year_span = row.select_one("td.gsc_a_y span.gsc_a_h")
        year = year_span.get_text(strip=True) if year_span else ""

        truncated = authors.rstrip().endswith("…") or authors.rstrip().endswith("...")

        pubs.append(
            {
                "title": title,
                "authors": authors,
                "venue": venue,
                "year": year,
                "citations": citations,
                "link": link,
                "truncated": truncated,
            }
        )
    return pubs


def fetch_full_authors(session: requests.Session, detail_url: str):
    """Fetch a publication's detail page and return its full 'Authors' field."""
    resp = session.get(detail_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    if looks_blocked(resp.text):
        return None
    soup = BeautifulSoup(resp.text, "lxml")
    for field_div in soup.select("#gsc_oci_table .gs_scl"):
        label = field_div.select_one(".gsc_oci_field")
        value = field_div.select_one(".gsc_oci_value")
        if label and value and label.get_text(strip=True).lower() == "authors":
            return value.get_text(strip=True)
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/profile")
def api_profile():
    scholar_url = request.args.get("url", "").strip()
    resolve_truncated = request.args.get("resolve_truncated", "false") == "true"
    if not scholar_url:
        return jsonify(ok=False, message="Missing Google Scholar URL."), 400

    try:
        user_id = extract_user_id(scholar_url)
    except ValueError as e:
        return jsonify(ok=False, message=str(e)), 400

    session = requests.Session()
    all_pubs = []
    name, affiliation, stats, photo_url = None, "", {}, None

    try:
        for page in range(MAX_PAGES):
            cstart = page * PAGE_SIZE
            html = fetch_profile_page(session, user_id, cstart)
            soup = BeautifulSoup(html, "lxml")

            if page == 0:
                name_el = soup.find(id="gsc_prf_in")
                name = name_el.get_text(strip=True) if name_el else None
                affil_el = soup.select_one(".gsc_prf_il")
                affiliation = affil_el.get_text(strip=True) if affil_el else ""
                stats = parse_stats_table(soup)
                photo_img = soup.select_one("#gsc_prf_pup-img")
                photo_url = urljoin(SCHOLAR_BASE, photo_img["src"]) if photo_img and photo_img.get("src") else None

            page_pubs = parse_publications(soup)
            if not page_pubs:
                break
            all_pubs.extend(page_pubs)
            if len(page_pubs) < PAGE_SIZE:
                break
            time.sleep(0.5)  # be polite between paginated requests

        if not name:
            return jsonify(
                ok=False,
                message=(
                    "Could not find a profile for that user ID. Double-check the "
                    "Scholar URL, or the profile may be private."
                ),
            ), 404

        if resolve_truncated:
            fetch_count = 0
            for pub in all_pubs:
                if pub["truncated"] and pub["link"] and fetch_count < MAX_DETAIL_FETCHES:
                    full = fetch_full_authors(session, pub["link"])
                    if full:
                        pub["authors"] = full
                        pub["truncated"] = False
                    fetch_count += 1
                    time.sleep(1)

    except RuntimeError as e:
        return jsonify(ok=False, message=str(e)), 429
    except requests.RequestException as e:
        return jsonify(ok=False, message=f"Network error reaching Google Scholar: {e}"), 502

    return jsonify(
        ok=True,
        name=name,
        affiliation=affiliation,
        scholar_url=f"{SCHOLAR_BASE}/citations?user={user_id}&hl=en",
        photo_proxy_url=f"/api/photo?user={user_id}" if photo_url else None,
        stats=stats,
        publications=all_pubs,
    )


@app.route("/api/photo")
def api_photo():
    """Proxy a researcher's Scholar profile photo (avoids hotlink/referrer blocks)."""
    user_id = request.args.get("user", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,}", user_id):
        return "", 400
    photo_page_url = f"{SCHOLAR_BASE}/citations?user={user_id}&hl=en"
    try:
        resp = requests.get(photo_page_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        img = soup.select_one("#gsc_prf_pup-img")
        if not img or not img.get("src"):
            return "", 404
        img_url = urljoin(SCHOLAR_BASE, img["src"])
        img_resp = requests.get(img_url, headers=HEADERS, timeout=20)
        img_resp.raise_for_status()
        content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        return img_resp.content, 200, {"Content-Type": content_type}
    except requests.RequestException:
        return "", 502


# ---------- authorship-position matching ----------

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace(".", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def heuristic_position(author_name: str, authors_field: str):
    target = normalize(author_name)
    target_parts = target.split()
    if not target_parts:
        return None
    target_last = target_parts[-1]
    target_first_initial = target_parts[0][0] if target_parts[0] else ""

    entries = [normalize(a) for a in authors_field.split(",")]
    for idx, entry in enumerate(entries, start=1):
        if not entry:
            continue
        entry_parts = entry.split()
        if not entry_parts:
            continue
        entry_last = entry_parts[-1]
        if entry_last == target_last:
            # surname matches; check first-name initial for more confidence
            entry_first_initial = entry_parts[0][0] if entry_parts[0] else ""
            if entry_first_initial == target_first_initial:
                return idx
    # fall back: surname-only match if initial check found nothing
    for idx, entry in enumerate(entries, start=1):
        entry_parts = entry.split()
        if entry_parts and entry_parts[-1] == target_last:
            return idx
    return None


def ask_ollama_positions(ollama_url, model, author_name, pubs):
    """
    Ask the local Ollama model to confirm/resolve authorship position for each
    publication's author string. Returns (positions_list, status_message).
    positions_list[i] is an int or None, aligned with `pubs`.
    """
    base = ollama_url.rstrip("/")
    numbered = "\n".join(
        f"{i}: {p['authors']}" for i, p in enumerate(pubs)
    )
    prompt = (
        f'You are given a researcher name "{author_name}" and a numbered list of '
        f"author-byline strings, one per publication (format: index: authors). "
        f'For each index, find the 1-based position of "{author_name}" within that '
        f"byline (matching on surname and first-name initial; author lists may use "
        f"abbreviated first names). If the name cannot be found, use null.\n\n"
        f"Return ONLY a JSON array of integers or nulls, in the exact same order as "
        f"the input indices, with no commentary, e.g. [1, 3, null, 2].\n\n"
        f"{numbered}"
    )
    try:
        resp = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": "json",
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        match = re.search(r"\[[\s\S]*\]", content)
        raw_list = match.group(0) if match else content
        import json as _json
        parsed = _json.loads(raw_list)
        if not isinstance(parsed, list):
            raise ValueError("model did not return a list")
        positions = [
            int(v) if isinstance(v, (int, float)) and v else None for v in parsed
        ]
        # pad/truncate to match length
        if len(positions) < len(pubs):
            positions += [None] * (len(pubs) - len(positions))
        return positions[: len(pubs)], f"ai-assisted ({model})"
    except Exception as e:
        return None, f"ollama unavailable ({e}); using heuristic matching only"


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    body = request.get_json(force=True)
    author_name = body.get("author_name", "").strip()
    ollama_url = body.get("ollama_url", "http://localhost:11434").strip()
    ollama_model = body.get("ollama_model", "").strip()
    use_ai = body.get("use_ai", True)
    publications = body.get("publications", [])

    if not author_name:
        return jsonify(ok=False, message="Missing author name."), 400

    heuristic_positions = [
        heuristic_position(author_name, p.get("authors", "")) for p in publications
    ]

    ai_positions, ai_status = None, "AI step skipped"
    if use_ai and ollama_model:
        ai_positions, ai_status = ask_ollama_positions(
            ollama_url, ollama_model, author_name, publications
        )

    final_positions = []
    sources = []
    for i, p in enumerate(publications):
        ai_val = ai_positions[i] if ai_positions else None
        heur_val = heuristic_positions[i]
        if ai_val:
            final_positions.append(ai_val)
            sources.append("ai")
        elif heur_val:
            final_positions.append(heur_val)
            sources.append("heuristic")
        else:
            final_positions.append(None)
            sources.append("unresolved")

    citations = [int(p.get("citations", 0) or 0) for p in publications]
    total_citations = sum(citations)
    sorted_cites = sorted(citations, reverse=True)
    h = 0
    for i, c in enumerate(sorted_cites, start=1):
        if c >= i:
            h = i
        else:
            break
    i10 = sum(1 for c in citations if c >= 10)

    def g_sum(max_pos):
        return sum(
            c for c, pos in zip(citations, final_positions)
            if pos is not None and pos <= max_pos
        )

    g1, g2, g3 = g_sum(1), g_sum(2), g_sum(3)

    results = [
        {
            "title": p.get("title"),
            "authors": p.get("authors"),
            "citations": citations[i],
            "position": final_positions[i],
            "position_source": sources[i],
        }
        for i, p in enumerate(publications)
    ]

    return jsonify(
        ok=True,
        ai_status=ai_status,
        results=results,
        metrics={
            "total_citations": total_citations,
            "hindex": h,
            "i10index": i10,
            "g1": g1,
            "g2": g2,
            "g3": g3,
        },
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
