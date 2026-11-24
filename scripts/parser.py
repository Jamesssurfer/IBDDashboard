# scripts/parser.py — IBD (Investor's Business Daily) Daily Market Report
#
# Converts ONE raw narrative report into the structured dict logger.py needs.
# Supports BOTH formats:
#   OLD (e.g. 14 / 13 Nov style):
#     - Date lines: "2 October 2026", "1st October 2026", "30 September 2026"
#     - Numbered ### **N. Title** sections
#     - Bullet lines with **Bold Title**: prose
#   NEW (post-mid-Nov style):
#     - Same date lines
#     - Plain numbered top-level sections: "1. Broad Market ..."
#     - Mix of * bullets, Company (TICKER) paragraphs, and markdown/TSV tables
#     - Sub-bullets and prose under stock headings
#
# Multiple stories separated by a line containing only "===".

import re
from datetime import datetime

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _strip_refs(text):
    if not text:
        return ""
    t = text
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', t)
    t = re.sub(r'\[[\d,\.\s]+\]', '', t)
    t = re.sub(r'\[\s*\]', '', t)
    t = re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
    t = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', t)
    t = re.sub(r'\\\$', '$', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    if t and not t.endswith((".", "!", "?", '"')):
        t += "."
    return t


def _split_stories(raw_text):
    normalized = raw_text.replace("\r\n", "\n")
    parts = re.split(r'\n===\n|^===\n|\n===$', normalized)
    return [p.strip() for p in parts if p.strip()]


def _find_header_and_date(text):
    # "2 October 2026" / "1st October 2026" / "30 September 2026"
    m = re.search(
        r'(?:^|\n)\s*(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})\s*(?:\n|$)',
        text,
    )
    if m:
        day, month_name, year = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            day_i, year_i = int(day), int(year)
            try:
                weekday = datetime(year_i, month, day_i).strftime("%A")
            except ValueError:
                weekday = "Unknown"
            header = f"{weekday}, {month_name} {day_i}, {year_i}'s Report"
            return header, (year_i, month, day_i)

    # Fallback: "Friday, October 2, 2026"
    m = re.search(r'(\w+),\s*(\w+)\s+(\d{1,2}),\s*(\d{4})', text)
    if m:
        weekday, month_name, day, year = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            header = f"{weekday}, {month_name} {day}, {year}'s Report"
            return header, (int(year), month, int(day))

    # Bold date
    m = re.search(r'\*\*(\w+)\s+(\d{1,2}),\s*(\d{4})\*\*', text)
    if m:
        month_name, day, year = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            try:
                weekday = datetime(int(year), month, int(day)).strftime("%A")
            except ValueError:
                weekday = "Unknown"
            header = f"{weekday}, {month_name} {day}, {year}'s Report"
            return header, (int(year), month, int(day))

    return None, None


def _opening_blurb(text):
    """Text between date and first section heading (### or plain numbered)."""
    body = text
    # Drop date line
    body = re.sub(
        r'(?:^|\n)\s*\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+\d{4}\s*\n',
        '\n',
        body,
        count=1,
    )
    body = body.strip()
    # Stop at first ### (anywhere, including start) or top-level numbered section
    m = re.search(r'(?:^|\n)(?:###\s+|\d+\.\s+[A-Z])', body)
    if m:
        body = body[:m.start()].strip()
    body = re.sub(r'^---+\s*', '', body, flags=re.MULTILINE).strip()
    # Also drop a leading report title line that is not a full sentence
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip() and p.strip() != '---']
    if not paragraphs:
        return ""
    for p in paragraphs:
        # Skip pure boilerplate / short titles
        if len(p) > 40 and not p.lower().startswith("here is a detailed"):
            # Skip if it looks like a report title (no period, short-ish)
            if len(p) < 140 and not re.search(r'[.!?]', p) and p[0].isupper():
                continue
            return _strip_refs(p)
        if len(p) > 80:
            return _strip_refs(p)
    return _strip_refs(paragraphs[0]) if paragraphs else ""


def _headline_from_blurb(blurb):
    if not blurb:
        return ""
    protected = blurb
    for abbr in ("U.S.", "U.K.", "U.N.", "E.U.", "e.g.", "i.e.", "vs.", "Mr.", "Mrs.", "Ms.", "Dr."):
        protected = protected.replace(abbr, abbr.replace(".", "\uff0e"))
    m = re.match(r'^([^.!?]+[.!?])', protected)
    if m and len(m.group(1)) > 25:
        return m.group(1).replace("\uff0e", ".").strip()
    if len(blurb) <= 140:
        return blurb
    return blurb[:140].rsplit(" ", 1)[0].rstrip() + "..."


def _parse_bullets(section_body):
    """Parse * **Title**: text  / * Title: text / plain continuation into {title, text} list.
    Also treats lines that look like 'Company (TICKER) — ...' as titled items.
    """
    items = []
    lines = section_body.split('\n')
    current_title = None
    current_parts = []

    def flush():
        nonlocal current_title, current_parts
        if current_title is not None or current_parts:
            text = _strip_refs(' '.join(current_parts))
            items.append({"title": current_title or "", "text": text})
        current_title = None
        current_parts = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == '---':
            continue

        # Numbered sub-items under a bullet (e.g. Power Trend criteria)
        if re.match(r'^\d+\.\s+', stripped) and current_title is not None:
            current_parts.append(stripped)
            continue

        # New bullet (* or -)
        if stripped.startswith('*') or stripped.startswith('-'):
            content = re.sub(r'^[-*]\s*', '', stripped)
            # **Title**: rest   or  **Title** rest
            m = re.match(r'\*\*([^*]+)\*\*\s*:?\s*(.*)$', content)
            if m:
                flush()
                current_title = m.group(1).strip().rstrip(':')
                rest = m.group(2).strip()
                if rest:
                    current_parts.append(rest)
            else:
                # Title: rest  (no bold)
                m2 = re.match(r'^([^:]{2,60}?):\s+(.*)$', content)
                if m2 and not content.lower().startswith(('http', 'www')):
                    flush()
                    current_title = m2.group(1).strip()
                    rest = m2.group(2).strip()
                    if rest:
                        current_parts.append(rest)
                else:
                    # Continuation or untitled bullet
                    if current_title is not None:
                        current_parts.append(content)
                    else:
                        flush()
                        current_title = ""
                        current_parts.append(content)
            continue

        # Company (TICKER) — description   or   Company (TICKER)
        m_co = re.match(
            r'^([A-Z][A-Za-z0-9\s&\.\-\']+?)\s*\(([A-Z]{1,5}(?:\s*/\s*[A-Z]{1,5})?)\)\s*(?:—|-|:)?\s*(.*)$',
            stripped,
        )
        if m_co and len(m_co.group(1).split()) <= 6:
            flush()
            company = m_co.group(1).strip()
            ticker = m_co.group(2).strip()
            rest = m_co.group(3).strip()
            current_title = f"{company} ({ticker})"
            if rest:
                current_parts.append(rest)
            continue

        # Continuation line
        if current_title is not None or current_parts:
            current_parts.append(stripped)
        else:
            # Orphan prose → treat as untitled item if substantial
            if len(stripped) > 40:
                flush()
                current_title = ""
                current_parts.append(stripped)

    flush()
    return items


# Words that look like tickers but are not (RS line, EPS rating, etc.)
_TICKER_STOP = {
    "RS", "EPS", "ETF", "ADR", "CEO", "CFO", "IPO", "GDP", "CPI", "PCE",
    "FOMC", "FED", "USD", "JPY", "EUR", "AI", "IT", "US", "UK", "EU",
    "PMI", "YOY", "QOQ", "ATM", "OTC", "SEC", "FDA", "MOU", "ATR",
    "SMA", "EMA", "VIX", "FTD", "IBD",
}


def _extract_tickers(title, text, title_only=False):
    """Pull ticker symbols from 'Company (TICKER)' patterns."""
    tickers = []
    # Prefer tickers in the title (Company (TICKER))
    for m in re.finditer(r'\(([A-Z]{1,5}(?:\s*/\s*[A-Z]{1,5})?)\)', title or ""):
        for t in re.split(r'\s*/\s*', m.group(1)):
            t = t.strip()
            if t and t not in tickers and t not in _TICKER_STOP and 1 < len(t) <= 5:
                tickers.append(t)
    if title_only or tickers:
        return tickers
    # Fallback: scan body, but skip stopwords
    for m in re.finditer(r'\(([A-Z]{2,5})\)', text or ""):
        t = m.group(1).strip()
        if t and t not in tickers and t not in _TICKER_STOP:
            tickers.append(t)
    return tickers


def _pct_from_text(text):
    # Prefer explicit move phrases: "Advanced 4.5%", "surged 10%", "+0.7%", "falling 1.2%"
    verb_pat = (
        r'(?:advanced|surged|gained|rose|jumped|fell|falling|dropped|declined|lost|up|down|'
        r'plunged|tumbled|slipped|rebounded|climbed|declining|dropping|gaining|rising)'
    )
    m = re.search(
        verb_pat + r'\s+'
        r'([+\-]?\d{1,3}(?:,\d{3})*\.?\d*)\s*%',
        text or '',
        re.IGNORECASE,
    )
    if m:
        val = m.group(1).replace(',', '')
        # Infer sign from verb if missing
        verb = re.search(verb_pat, text or '', re.IGNORECASE)
        if verb and not val.startswith(('+', '-')):
            v = verb.group(0).lower()
            if any(neg in v for neg in ('fell', 'fall', 'drop', 'declin', 'lost', 'down', 'plung', 'tumbl', 'slip')):
                val = '-' + val
            else:
                val = '+' + val
        return val + '%'
    m = re.search(r'([+\-]\d+\.?\d*)\s*%', text or '')
    if m:
        return m.group(1) + '%'
    # Avoid grabbing "50% retracement" style — only take a lone percentage if it looks like a daily change
    # (preceded by space/pipe and not followed by a word like retracement/level)
    m = re.search(
        r'(?:^|[\s\|])([+\-]?\d{1,3}\.?\d*)\s*%(?!\s*(?:retracement|level|fib|of\b|midpoint))',
        text or '',
        re.IGNORECASE,
    )
    if m:
        return m.group(1) + '%'
    return ""


def _classify_section(title):
    t = (title or '').lower()
    if any(k in t for k in (
        'index', 'technical level', 'technical structure', 'technical divergence',
        'broad market', 'benchmark', 'market structure', 'index performance',
        'index breakdown', 'index dynamics', 'technical failure', 'support levels',
        'rally dynamics', 'day 1 rally',
    )):
        return 'indexes'
    if any(k in t for k in (
        'macro', 'intermarket', 'rate expectation', 'fed expectation', 'inflation',
        'treasury', 'yield', 'catalyst', 'economic data', 'monetary', 'liquidity',
        'volatility', 'sentiment', 'fomc',
    )):
        return 'macro'
    if any(k in t for k in (
        'sector', 'etf', 'rotation', 'thematic', 'industry group',
    )):
        return 'sectors'
    if any(k in t for k in (
        'stock', 'breakout', 'setup', 'leading stock', 'featured', 'equity',
        'individual', 'growth stock', 'watchlist', 'model-book', 'leadership',
    )):
        return 'stocks'
    if any(k in t for k in (
        'strategy', 'portfolio', 'risk', 'execution', 'weekend', 'catalyst',
        'positioning', 'capital preservation', 'risk management', 'tactical',
        'protocol', 'checklist', 'trend change', 'follow-through',
    )):
        return 'strategy'
    return 'other'


def _parse_table_rows(body):
    """Best-effort parse of simple TSV / pipe / markdown tables into list of dicts
    using the header row as keys (lower-cased, simplified).
    """
    rows = []
    lines = [ln.strip() for ln in body.split('\n') if ln.strip()]
    header = None
    for ln in lines:
        # Skip pure separator lines
        if re.match(r'^[\|\-\s:]+$', ln):
            continue
        # Detect header-ish line containing "Index" or "ETF" or "Ticker" etc.
        cells = re.split(r'\t|\s{2,}|\s*\|\s*', ln)
        cells = [c.strip() for c in cells if c.strip()]
        if not cells or len(cells) < 2:
            continue
        joined = ' '.join(cells).lower()
        if header is None:
            # Heuristic: first multi-cell line that looks like a header
            if any(k in joined for k in (
                'index', 'benchmark', 'etf', 'ticker', 'name', 'daily',
                'return', 'change', 'performance', 'session return',
            )):
                header = [re.sub(r'[^a-z0-9]+', '_', c.lower()).strip('_') for c in cells]
                continue
            # Otherwise treat as data under generic keys (e.g. "NASDAQ Composite	-2.5%	...")
            name = cells[0]
            # Skip if name itself looks like a header fragment
            if any(k in name.lower() for k in ('index /', 'benchmark', 'daily change', 'ticker')):
                header = [re.sub(r'[^a-z0-9]+', '_', c.lower()).strip('_') for c in cells]
                continue
            pct = ""
            action = ' | '.join(cells[1:])
            for c in cells[1:]:
                p = _pct_from_text(c)
                if p:
                    pct = p
                    break
            rows.append({"name": name, "pct": pct, "action": _strip_refs(action)})
            continue
        # Data row under known header
        d = {}
        for i, h in enumerate(header):
            if i < len(cells):
                d[h] = cells[i]
        name = (
            d.get('index_benchmark') or d.get('index') or d.get('benchmark')
            or d.get('etf_ticker_name') or d.get('etf') or d.get('name')
            or d.get('stock_name_ticker') or d.get('equity_benchmark')
            or cells[0]
        )
        pct = ""
        for k, v in d.items():
            if any(x in k for x in ('change', 'return', 'performance', 'daily')) or '%' in (v or ''):
                pct = _pct_from_text(v) or (v.strip() if re.search(r'[\d.]+\s*%', v or '') else "")
                if pct:
                    break
        action_parts = []
        for k, v in d.items():
            if k in ('index_benchmark', 'index', 'benchmark', 'name', 'etf', 'etf_ticker_name') or not v:
                continue
            action_parts.append(f"{k.replace('_', ' ')}: {v}")
        action = ' | '.join(action_parts) if action_parts else ' | '.join(cells[1:])
        rows.append({"name": name, "pct": pct, "action": _strip_refs(action)})
    return rows


def _parse_sections(text):
    """Find sections from either ### headings or plain top-level numbered headings."""
    sections = []

    # Prefer ### style when present
    hash_pattern = re.compile(
        r'###\s*\*?\*?(?:(?:Step\s+)?(\d+)[:.]?\s*)?(.+?)\*?\*?\s*\n',
        re.MULTILINE,
    )
    hash_matches = list(hash_pattern.finditer(text))
    if hash_matches:
        for idx, m in enumerate(hash_matches):
            number = int(m.group(1)) if m.group(1) else idx + 1
            title = m.group(2).strip().strip('*').strip()
            start = m.end()
            end = hash_matches[idx + 1].start() if idx + 1 < len(hash_matches) else len(text)
            body = text[start:end].strip()
            body = re.sub(r'\n---+\s*$', '', body).strip()
            items = _parse_bullets(body)
            # Also try table extraction and merge if bullets empty
            if not items:
                table_rows = _parse_table_rows(body)
                for tr in table_rows:
                    items.append({"title": tr.get("name", ""), "text": tr.get("action", "")})
            sections.append({
                "number": number,
                "title": title,
                "kind": _classify_section(title),
                "items": items,
            })
        return sections

    # NEW format: plain numbered top-level sections
    # Match lines that look like "1. Title Here" (title starts with capital, reasonable length)
    num_pattern = re.compile(
        r'^(\d+)\.\s+([A-Z][^\n]{8,120}?)\s*$',
        re.MULTILINE,
    )
    candidates = list(num_pattern.finditer(text))
    # Keep only those that appear to be top-level (sequential-ish, not deeply nested list items)
    top = []
    seen_nums = set()
    for m in candidates:
        num = int(m.group(1))
        title = m.group(2).strip()
        # Skip obvious sub-list items that are long sentences
        if title.endswith(':') and len(title) > 60:
            continue
        if num in seen_nums and num > 3:
            # later repeats of small numbers are often sub-lists
            continue
        # Prefer first occurrence of each number in ascending order
        if top and num <= top[-1][0] and num != 1:
            # possible sub-list; skip unless it looks like a real section title
            if not any(k in title.lower() for k in (
                'market', 'index', 'sector', 'stock', 'portfolio', 'risk',
                'strategy', 'macro', 'equity', 'technical', 'individual', 'leadership',
            )):
                continue
        top.append((num, title, m.start(), m.end()))
        seen_nums.add(num)

    if not top:
        # Fallback: treat whole body as one big "other" section
        body = re.sub(r'^.*?\d{4}\s*\n', '', text, count=1).strip()
        items = _parse_bullets(body)
        if not items:
            table_rows = _parse_table_rows(body)
            for tr in table_rows:
                items.append({"title": tr.get("name", ""), "text": tr.get("action", "")})
        return [{
            "number": 1,
            "title": "Market Report",
            "kind": "other",
            "items": items,
        }]

    for idx, (number, title, _start, end_header) in enumerate(top):
        start = end_header
        end = top[idx + 1][2] if idx + 1 < len(top) else len(text)
        body = text[start:end].strip()
        body = re.sub(r'\n---+\s*$', '', body).strip()
        items = _parse_bullets(body)
        # Supplement with table rows when present
        table_rows = _parse_table_rows(body)
        if table_rows:
            # Prefer table rows for index/sector sections; merge carefully
            existing_titles = {it["title"].lower() for it in items if it["title"]}
            for tr in table_rows:
                nm = tr.get("name", "")
                if nm.lower() not in existing_titles:
                    items.append({"title": nm, "text": (tr.get("pct", "") + " " + tr.get("action", "")).strip()})
        sections.append({
            "number": number,
            "title": title,
            "kind": _classify_section(title),
            "items": items,
        })
    return sections


def _build_indexes(sections):
    rows = []
    seen = set()
    for sec in sections:
        if sec['kind'] != 'indexes':
            continue
        for item in sec['items']:
            title = item['title']
            text = item['text']
            pct = _pct_from_text(text) or _pct_from_text(title)
            # Clean name: drop trailing pct if present
            name = re.sub(r'\s*[+\-]?\d+\.?\d*\s*%\s*$', '', title or "").strip()
            if not name:
                # Try to pull a leading index name from the action text
                m = re.match(
                    r'^((?:NASDAQ|S&P|Dow|Russell|Equal-Weight|NYSE|New York)[^:|\n]{0,40}?)(?:\s*[|\t:]|\s{2,}|$)',
                    text or '',
                    re.IGNORECASE,
                )
                if m:
                    name = m.group(1).strip()
                else:
                    # Skip pure prose / header leftovers
                    if not pct and len(text or '') > 80:
                        continue
                    name = "Index note"
            # Dedup by name
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "name": name,
                "pct": pct,
                "action": text,
            })
    return rows


def _build_macro(sections):
    items = []
    for sec in sections:
        if sec['kind'] != 'macro':
            continue
        for item in sec['items']:
            items.append({
                "title": item['title'],
                "text": item['text'],
            })
    return items


def _build_sectors(sections):
    rows = []
    for sec in sections:
        if sec['kind'] != 'sectors':
            continue
        for item in sec['items']:
            title = item['title']
            text = item['text']
            tickers = _extract_tickers(title, text)
            rows.append({
                "name": title,
                "tickers": tickers,
                "pct": _pct_from_text(text) or _pct_from_text(title),
                "action": text,
            })
    return rows


def _build_stocks(sections):
    """Build stock rows. Nested sub-bullets (Technical Action, Fundamentals)
    without their own ticker are merged into the parent stock's action.
    Works for both old * **Title**: style and new 'Company (TICKER) — ...' style.
    """
    rows = []
    for sec in sections:
        if sec['kind'] != 'stocks':
            continue
        current = None
        for item in sec['items']:
            title = item['title']
            text = item['text']
            tickers = _extract_tickers(title, text, title_only=True)
            if tickers:
                # New parent stock
                if current:
                    rows.append(current)
                company = re.sub(r'\s*\([^)]*\)\s*$', '', title).strip() if title else ""
                # Also strip trailing " — desc"
                company = re.sub(r'\s*[—\-:].*$', '', company).strip()
                pct = _pct_from_text(text) or _pct_from_text(title)
                current = {
                    "ticker": tickers[0],
                    "tickers": tickers,
                    "company": company,
                    "pct": pct,
                    "action": text,
                }
            elif current is not None:
                # Sub-bullet under current stock (skip pure empty titles that are just prose)
                label = title.strip() if title else ""
                # Avoid treating long sentence titles as labels
                if label and len(label) > 60 and not re.search(r'\([A-Z]{1,5}\)', label):
                    chunk = f"{label} {text}".strip()
                else:
                    chunk = f"{label}: {text}" if label else text
                if current["action"]:
                    current["action"] = f"{current['action']} | {chunk}"
                else:
                    current["action"] = chunk
                if not current["pct"]:
                    current["pct"] = _pct_from_text(text)
            else:
                # Orphan bullet with no parent — keep only if it has a real ticker
                body_tickers = _extract_tickers(title, text, title_only=False)
                if not body_tickers:
                    continue
                company = re.sub(r'\s*\([^)]*\)\s*$', '', title).strip() if title else ""
                company = re.sub(r'\s*[—\-:].*$', '', company).strip()
                rows.append({
                    "ticker": body_tickers[0],
                    "tickers": body_tickers,
                    "company": company,
                    "pct": _pct_from_text(text) or _pct_from_text(title),
                    "action": text,
                })
        if current:
            rows.append(current)
    return rows


def _build_strategy(sections):
    items = []
    for sec in sections:
        if sec['kind'] != 'strategy':
            continue
        for item in sec['items']:
            items.append({
                "title": item['title'],
                "text": item['text'],
            })
    return items


def parse_story(text):
    header, ymd = _find_header_and_date(text)
    if not header or not ymd:
        raise ValueError("could not find a date anywhere in this story")

    year, month, day = ymd
    timestamp = f"{year:04d}-{month:02d}-{day:02d}T20:00:00+00:00"

    blurb = _opening_blurb(text)
    headline = _headline_from_blurb(blurb)
    sections = _parse_sections(text)

    return {
        "timestamp": timestamp,
        "header": header,
        "headline": headline,
        "summary": blurb,
        "indexes": _build_indexes(sections),
        "macro": _build_macro(sections),
        "sectors": _build_sectors(sections),
        "stocks": _build_stocks(sections),
        "strategy": _build_strategy(sections),
        "sections": [
            {
                "number": s["number"],
                "title": s["title"],
                "kind": s["kind"],
                "items": s["items"],
            }
            for s in sections
        ],
    }


def parse_stories(raw_text):
    events, errors = [], []
    for block in _split_stories(raw_text):
        try:
            events.append(parse_story(block))
        except Exception as e:
            snippet = block.strip().split("\n")[0][:80]
            errors.append((snippet, e))
    return events, errors
