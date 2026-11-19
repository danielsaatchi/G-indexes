"""
G-Index Scholar Metrics — local operational web app.

Runs a small Flask server that:
  1. Scrapes a given public Google Scholar profile (server-side, so no browser
     CORS restrictions apply) and extracts profile info + full publication list.
  2. Proxies requests to a local Ollama instance (also server-side) to help
     determine each publication's authorship position for the target researcher.
  3. Computes Citations / h-index / i10-index / G1 / G2 / G3 deterministically
     from the resulting (citations, position) pairs, per the GAFCM framework.
  4. Computes GRW-Index: citations accumulated from review-type publications
     (literature reviews, systematic reviews, meta-analyses, surveys), using
     a title-keyword heuristic and/or the local Ollama model to classify each
     publication, flagging profiles that may rely heavily on co-authored
     review papers for citation volume.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000
"""

import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, send_file
from openpyxl import Workbook, load_workbook

app = Flask(__name__)

DATASET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gindex_dataset.xlsx")
DATASET_COLUMNS = [
    "Researcher Name",
    "Google Scholar URL",
    "Total Citations",
    "H-index",
    "i10-index",
    "G1",
    "G1 (%)",
    "G2",
    "G2 (%)",
    "G3",
    "G3 (%)",
    "GRW",
    "GRW (%)",
    "Affiliation",
    "Detected Title/Position",
    "Country",
    "Country Source",
    "Snapshot Date (UTC)",
]

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


def parse_citations_by_year(soup: BeautifulSoup):
    """Parse Scholar's 'Cited by' per-year bar graph into {year: count}.

    Scholar's histogram markup has shifted slightly across versions, so this
    tries several known variants and falls back gracefully rather than
    silently returning nothing.
    """
    graph = soup.find(id="gsc_graph") or soup.find(class_="gsc_graph")
    if not graph:
        return {}

    # Year labels: historically <span class="gsc_g_t">, sometimes inside <a>.
    year_tags = graph.find_all(class_="gsc_g_t")
    years = [t.get_text(strip=True) for t in year_tags]
    years = [y for y in years if y]  # drop empties

    # Bars: <a class="gsc_g_a">, count shown either in a child
    # <span class="gsc_g_al"> or as the anchor's own text.
    bar_tags = graph.find_all("a", class_="gsc_g_a")
    counts = []
    for bar in bar_tags:
        label = bar.find(class_="gsc_g_al")
        text = (label.get_text(strip=True) if label else bar.get_text(strip=True))
        text = re.sub(r"[^\d]", "", text)
        counts.append(int(text) if text else 0)

    if not years or not counts:
        return {}

    # Zero-citation years sometimes have no rendered bar element at all.
    # When bars are fewer than year labels, assume the missing ones are the
    # earliest years (left-padded with zero) rather than misaligning later
    # non-zero years.
    if len(counts) < len(years):
        counts = [0] * (len(years) - len(counts)) + counts
    elif len(counts) > len(years):
        counts = counts[-len(years):]

    return dict(zip(years, counts))


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
    citations_by_year = {}
    interests = []

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
                interests = [
                    a.get_text(strip=True)
                    for a in soup.select("#gsc_prf_int a.gsc_prf_inta")
                ]
                stats = parse_stats_table(soup)
                citations_by_year = parse_citations_by_year(soup)
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
        interests=interests,
        scholar_url=f"{SCHOLAR_BASE}/citations?user={user_id}&hl=en",
        photo_proxy_url=f"/api/photo?user={user_id}" if photo_url else None,
        stats=stats,
        citations_by_year=citations_by_year,
        publications=all_pubs,
    )


@app.route("/api/debug_graph")
def api_debug_graph():
    """Diagnostic: returns the raw HTML Scholar sent for the citation-graph
    area, so the exact markup can be inspected if the chart isn't rendering."""
    scholar_url = request.args.get("url", "").strip()
    try:
        user_id = extract_user_id(scholar_url)
    except ValueError as e:
        return jsonify(ok=False, message=str(e)), 400

    session = requests.Session()
    try:
        html = fetch_profile_page(session, user_id, 0)
    except (RuntimeError, requests.RequestException) as e:
        return jsonify(ok=False, message=str(e)), 502

    soup = BeautifulSoup(html, "lxml")
    graph = soup.find(id="gsc_graph") or soup.find(class_="gsc_graph")
    return jsonify(
        ok=True,
        found_graph_container=bool(graph),
        graph_html=str(graph)[:8000] if graph else None,
        parsed=parse_citations_by_year(soup),
        page_length=len(html),
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


TITLE_KEYWORDS = [
    # longer/more-specific phrases first so they match before a generic
    # substring (e.g. "Associate Professor" before bare "Professor").
    "Distinguished Professor", "Emeritus Professor", "Research Professor",
    "Associate Professor", "Assistant Professor", "Adjunct Professor",
    "Visiting Professor", "Full Professor", "Professor Emeritus",
    "Principal Investigator", "Senior Lecturer", "Research Fellow",
    "Postdoctoral Fellow", "Postdoctoral Researcher", "Postdoc",
    "PhD Candidate", "PhD Student", "Graduate Student", "Doctoral Student",
    "Research Scientist", "Senior Scientist", "Staff Scientist",
    "Group Leader", "Lab Director", "Laboratory Director",
    "Department Chair", "Dean", "Provost", "Director", "Chair",
    "Lecturer", "Professor", "Fellow", "Scientist", "Engineer",
]
TITLE_PATTERN = re.compile(
    "(" + "|".join(re.escape(t) for t in TITLE_KEYWORDS) + ")", re.IGNORECASE
)


def detect_title(affiliation: str):
    """Pull a likely academic/professional title out of a Scholar affiliation
    string (e.g. "Associate Professor, University of X" -> "Associate Professor").
    Returns None if no known title keyword is found."""
    if not affiliation:
        return None
    match = TITLE_PATTERN.search(affiliation)
    return match.group(1) if match else None


# Coarse country signal from common country names/abbreviations appearing
# directly in the affiliation string.
COUNTRY_NAME_KEYWORDS = {
    "united states": "United States", "usa": "United States", "u.s.a": "United States",
    "u.s.": "United States", "united kingdom": "United Kingdom", "uk": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "canada": "Canada", "australia": "Australia", "germany": "Germany",
    "france": "France", "italy": "Italy", "spain": "Spain", "espana": "Spain",
    "españa": "Spain", "netherlands": "Netherlands", "switzerland": "Switzerland",
    "sweden": "Sweden", "norway": "Norway", "denmark": "Denmark", "finland": "Finland",
    "belgium": "Belgium", "austria": "Austria", "ireland": "Ireland",
    "portugal": "Portugal", "greece": "Greece", "poland": "Poland",
    "china": "China", "japan": "Japan", "south korea": "South Korea",
    "korea": "South Korea", "india": "India", "singapore": "Singapore",
    "taiwan": "Taiwan", "hong kong": "Hong Kong", "israel": "Israel",
    "iran": "Iran", "turkey": "Turkey", "türkiye": "Turkey", "saudi arabia": "Saudi Arabia",
    "united arab emirates": "United Arab Emirates", "uae": "United Arab Emirates",
    "qatar": "Qatar", "egypt": "Egypt", "south africa": "South Africa",
    "brazil": "Brazil", "mexico": "Mexico", "argentina": "Argentina",
    "chile": "Chile", "colombia": "Colombia", "new zealand": "New Zealand",
    "russia": "Russia", "ukraine": "Ukraine", "czech republic": "Czech Republic",
    "hungary": "Hungary", "romania": "Romania", "thailand": "Thailand",
    "malaysia": "Malaysia", "indonesia": "Indonesia", "vietnam": "Vietnam",
    "pakistan": "Pakistan", "bangladesh": "Bangladesh", "nigeria": "Nigeria",
    "kenya": "Kenya", "morocco": "Morocco",
}

# Well-known universities/institutes mapped to country, for affiliations that
# name only the institution (e.g. "UCLA") without the country itself.
UNIVERSITY_COUNTRY_KEYWORDS = {
    "ucla": "United States", "harvard": "United States", "stanford": "United States",
    "mit": "United States", "berkeley": "United States", "caltech": "United States",
    "princeton": "United States", "yale": "United States", "columbia university": "United States",
    "cornell": "United States", "university of michigan": "United States",
    "university of chicago": "United States", "johns hopkins": "United States",
    "university of pennsylvania": "United States", "upenn": "United States",
    "duke university": "United States", "nyu": "United States",
    "university of california": "United States", "ucsd": "United States",
    "ucsf": "United States", "uc davis": "United States", "uc irvine": "United States",
    "university of washington": "United States", "university of texas": "United States",
    "university of southern california": "United States", "usc": "United States",
    "georgia tech": "United States", "northwestern university": "United States",
    "oxford": "United Kingdom", "cambridge": "United Kingdom",
    "imperial college": "United Kingdom", "university college london": "United Kingdom",
    "ucl": "United Kingdom", "king's college london": "United Kingdom",
    "lse": "United Kingdom", "edinburgh": "United Kingdom", "manchester": "United Kingdom",
    "eth zurich": "Switzerland", "epfl": "Switzerland", "university of zurich": "Switzerland",
    "university of toronto": "Canada", "mcgill": "Canada", "ubc": "Canada",
    "university of waterloo": "Canada",
    "tsinghua": "China", "peking university": "China", "fudan": "China",
    "shanghai jiao tong": "China", "zhejiang university": "China",
    "university of tokyo": "Japan", "kyoto university": "Japan",
    "tokyo institute of technology": "Japan", "osaka university": "Japan",
    "kaist": "South Korea", "seoul national university": "South Korea",
    "national university of singapore": "Singapore", "nus": "Singapore",
    "nanyang technological": "Singapore", "ntu": "Singapore",
    "technion": "Israel", "tel aviv university": "Israel",
    "weizmann institute": "Israel", "hebrew university": "Israel",
    "sharif university": "Iran", "university of tehran": "Iran",
    "amirkabir": "Iran", "iran university of science": "Iran",
    "delft university": "Netherlands", "tu delft": "Netherlands",
    "max planck": "Germany", "tu munich": "Germany", "technical university of munich": "Germany",
    "heidelberg": "Germany", "humboldt university": "Germany",
    "sorbonne": "France", "ecole polytechnique": "France", "cnrs": "France",
    "university of melbourne": "Australia", "university of sydney": "Australia",
    "anu": "Australia", "unsw": "Australia", "monash university": "Australia",
    "iit": "India", "indian institute of technology": "India", "iisc": "India",
    "university of sao paulo": "Brazil", "usp": "Brazil", "unicamp": "Brazil",
}


def detect_country(affiliation: str):
    """Best-effort country inference from a Scholar affiliation string using
    local keyword matching only (no live web search is wired into this app).
    Returns (country_or_None, source) where source is "heuristic" or
    "unresolved"."""
    if not affiliation:
        return None, "unresolved"
    low = affiliation.lower()
    for keyword, country in COUNTRY_NAME_KEYWORDS.items():
        if keyword in low:
            return country, "heuristic"
    for keyword, country in UNIVERSITY_COUNTRY_KEYWORDS.items():
        if keyword in low:
            return country, "heuristic"
    return None, "unresolved"


def ask_ollama_country(ollama_url, model, affiliation):
    """Ask the local Ollama model to infer a country from an affiliation
    string, as a fallback when the keyword lookup can't resolve one. This is
    a model-knowledge guess, not a verified web lookup."""
    base = ollama_url.rstrip("/")
    prompt = (
        "You are given an academic affiliation string from a researcher's "
        "profile. Respond with ONLY the country where that institution is "
        "located, as a plain country name (e.g. \"Germany\"), and nothing "
        "else. If you cannot determine the country, respond with exactly: "
        "unknown.\n\n"
        f"Affiliation: {affiliation}"
    )
    try:
        resp = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "").strip()
        content = content.strip(".\"'")
        if not content or content.lower() == "unknown" or len(content) > 60:
            return None, "unresolved"
        return content, f"ai-assisted ({model})"
    except Exception:
        return None, "unresolved"


def append_dataset_row(row_values):
    """Append one row to the cumulative dataset workbook, creating it with
    headers on first use."""
    if os.path.exists(DATASET_PATH):
        wb = load_workbook(DATASET_PATH)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Researcher Profiles"
        ws.append(DATASET_COLUMNS)
    ws.append(row_values)
    wb.save(DATASET_PATH)
    return ws.max_row - 1  # number of data rows, excluding header


REVIEW_TITLE_PATTERNS = re.compile(
    r"\b("
    r"a\s+review|systematic\s+review|literature\s+review|narrative\s+review|"
    r"scoping\s+review|review\s+of|review\s+on|review\s+article|"
    r"meta-analysis|meta\s+analysis|an\s+overview|a\s+survey\s+of|"
    r"state[\s-]of[\s-]the[\s-]art|current\s+status\s+and|"
    r"recent\s+advances|recent\s+progress|a\s+comprehensive\s+review"
    r")\b",
    re.IGNORECASE,
)


def heuristic_is_review(title: str) -> bool:
    """Flag likely review/survey/meta-analysis papers from the title alone.

    This is a coarse, title-only heuristic: it will miss reviews with
    non-obvious titles and may mis-flag the rare original-research paper
    that happens to use one of these words. It is meant as a starting point
    for the user to correct per-row, not a final determination.
    """
    return bool(REVIEW_TITLE_PATTERNS.search(title or ""))


def ask_ollama_review_classification(ollama_url, model, pubs):
    """
    Ask the local Ollama model to classify each publication as a review-type
    paper (review article, systematic review, meta-analysis, survey, etc.)
    versus original research, based on its title. Returns (bool_list, status).
    """
    base = ollama_url.rstrip("/")
    numbered = "\n".join(f"{i}: {p['title']}" for i, p in enumerate(pubs))
    prompt = (
        "You are given a numbered list of publication titles (format: "
        "index: title). For each index, decide whether the publication is a "
        "review-type paper — a literature review, systematic review, scoping "
        "review, narrative review, meta-analysis, or broad survey/overview "
        "article that synthesizes existing work rather than reporting new "
        "original research or a new method/result.\n\n"
        "Return ONLY a JSON array of true/false values, in the exact same "
        "order as the input indices, with no commentary, e.g. "
        "[false, true, false].\n\n"
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
        flags = [bool(v) for v in parsed]
        if len(flags) < len(pubs):
            flags += [False] * (len(pubs) - len(flags))
        return flags[: len(pubs)], f"ai-assisted ({model})"
    except Exception as e:
        return None, f"ollama unavailable ({e}); using title-keyword heuristic only"


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

    heuristic_reviews = [
        heuristic_is_review(p.get("title", "")) for p in publications
    ]
    ai_reviews, review_status = None, "AI step skipped"
    if use_ai and ollama_model:
        ai_reviews, review_status = ask_ollama_review_classification(
            ollama_url, ollama_model, publications
        )
    final_reviews = []
    review_sources = []
    for i in range(len(publications)):
        if ai_reviews is not None:
            final_reviews.append(ai_reviews[i])
            review_sources.append("ai")
        else:
            final_reviews.append(heuristic_reviews[i])
            review_sources.append("heuristic")

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

    grw = sum(
        c for c, is_rev in zip(citations, final_reviews) if is_rev
    )
    review_count = sum(1 for is_rev in final_reviews if is_rev)

    results = [
        {
            "title": p.get("title"),
            "authors": p.get("authors"),
            "citations": citations[i],
            "position": final_positions[i],
            "position_source": sources[i],
            "is_review": final_reviews[i],
            "review_source": review_sources[i],
        }
        for i, p in enumerate(publications)
    ]

    return jsonify(
        ok=True,
        ai_status=ai_status,
        review_status=review_status,
        results=results,
        metrics={
            "total_citations": total_citations,
            "hindex": h,
            "i10index": i10,
            "g1": g1,
            "g2": g2,
            "g3": g3,
            "grw": grw,
            "review_count": review_count,
            "publication_count": len(publications),
        },
    )


@app.route("/api/export_row", methods=["POST"])
def api_export_row():
    body = request.get_json(force=True)
    author_name = body.get("author_name", "").strip()
    scholar_url = body.get("scholar_url", "").strip()
    affiliation = body.get("affiliation", "").strip()
    ollama_url = body.get("ollama_url", "http://localhost:11434").strip()
    ollama_model = body.get("ollama_model", "").strip()
    use_ai = body.get("use_ai", False)
    metrics = body.get("metrics", {})

    if not author_name:
        return jsonify(ok=False, message="Missing author name."), 400

    def pct(part, total):
        total = total or 0
        return round((part / total) * 100, 1) if total else 0

    total_citations = metrics.get("total_citations", 0)
    g1 = metrics.get("g1", 0)
    g2 = metrics.get("g2", 0)
    g3 = metrics.get("g3", 0)
    grw = metrics.get("grw", 0)

    title_detected = detect_title(affiliation) or ""

    country, country_source = detect_country(affiliation)
    if not country and use_ai and ollama_model:
        country, country_source = ask_ollama_country(ollama_url, ollama_model, affiliation)
    country = country or ""
    if not country:
        country_source = "unresolved"

    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    row = [
        author_name,
        scholar_url,
        total_citations,
        metrics.get("hindex", 0),
        metrics.get("i10index", 0),
        g1, pct(g1, total_citations),
        g2, pct(g2, total_citations),
        g3, pct(g3, total_citations),
        grw, pct(grw, total_citations),
        affiliation,
        title_detected,
        country,
        country_source,
        snapshot,
    ]

    try:
        row_count = append_dataset_row(row)
    except Exception as e:
        return jsonify(ok=False, message=f"Could not write to dataset file: {e}"), 500

    return jsonify(
        ok=True,
        row_count=row_count,
        dataset_path=DATASET_PATH,
        detected_title=title_detected,
        country=country,
        country_source=country_source,
    )


@app.route("/api/export_download")
def api_export_download():
    if not os.path.exists(DATASET_PATH):
        return jsonify(ok=False, message="No dataset file exists yet — add at least one row first."), 404
    return send_file(
        DATASET_PATH,
        as_attachment=True,
        download_name="gindex_dataset.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
