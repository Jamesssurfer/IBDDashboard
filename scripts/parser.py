# scripts/parser.py — IBD (Investor's Business Daily) Daily Market Report
#
# Converts ONE raw narrative report into the structured dict logger.py needs.
# Best-effort extraction for the style in IBDDaily.txt:
#   - Date lines: "2 October 2026", "1st October 2026", "30 September 2026"
#   - Numbered ### sections (Index, Macro, Sectors, Stocks, Strategy)
#   - Bullet lines with **Bold Title**: prose
#   - Stock tickers in (TICKER) form
#
# Multiple stories separated by a line containing only "===".

import re
from datetime import datetime, timezone

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
    """Text between date and first ### heading (often an intro sentence)."""
    body = text
    # Drop date line
    body = re.sub(
        r'(?:^|\n)\s*\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+\d{4}\s*\n',
        '\n',
        body,
        count=1,
    )
    body = body.strip()
    m = re.search(r'\n###\s+', body)
    if m:
        body = body[:m.start()]
    body = re.sub(r'^---+\s*', '', body, flags=re.MULTILINE).strip()
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip() and p.strip() != '---']
    if not paragraphs:
        return ""
    # Skip pure boilerplate like "Here is a detailed..."
    for p in paragraphs:
        if len(p) > 40 and not p.lower().startswith("here is a detailed"):
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
    """Parse * **Title**: text  bullets into {title, text} list."""
    items = []
    # Split on bullet starts
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
        # New bullet
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
                # Continuation or untitled bullet
                if current_title is not None:
                    current_parts.append(content)
                else:
                    flush()
                    current_title = ""
                    current_parts.append(content)
        else:
            # Continuation line
            if current_title is not None or current_parts:
                current_parts.append(stripped)
    flush()
    return items


# Words that look like tickers but are not (RS line, EPS rating, etc.)
_TICKER_STOP = {
    "RS", "EPS", "ETF", "ADR", "CEO", "CFO", "IPO", "GDP", "CPI", "PCE",
    "FOMC", "FED", "USD", "JPY", "EUR", "AI", "IT", "US", "UK", "EU",
    "PMI", "YOY", "QOQ", "ATM", "OTC", "SEC", "FDA", "MOU",
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
    # Prefer explicit move phrases: "Advanced 4.5%", "surged 10%", "+0.7%"
    m = re.search(
        r'(?:advanced|surged|gained|rose|jumped|fell|dropped|declined|lost|up|down)\s+'
        r'([+\-]?\d{1,3}(?:,\d{3})*\.?\d*)\s*%',
        text or '',
        re.IGNORECASE,
    )
    if m:
        val = m.group(1).replace(',', '')
        # Infer sign from verb if missing
        verb = re.search(
            r'(advanced|surged|gained|rose|jumped|fell|dropped|declined|lost|up|down)',
            text or '',
            re.IGNORECASE,
        )
        if verb and not val.startswith(('+', '-')):
            v = verb.group(1).lower()
            if v in ('fell', 'dropped', 'declined', 'lost', 'down'):
                val = '-' + val
            else:
                val = '+' + val
        return val + '%'
    m = re.search(r'([+\-]\d+\.?\d*)\s*%', text or '')
    if m:
        return m.group(1) + '%'
    return ""


def _classify_section(title):
    t = (title or '').lower()
    if any(k in t for k in ('index', 'technical level', 'technical structure', 'technical divergence', 'broad market')):
        return 'indexes'
    if any(k in t for k in ('macro', 'intermarket', 'rate expectation', 'fed expectation', 'inflation')):
        return 'macro'
    if any(k in t for k in ('sector', 'etf')):
        return 'sectors'
    if any(k in t for k in ('stock', 'breakout', 'setup', 'leading stock', 'featured')):
        return 'stocks'
    if any(k in t for k in ('strategy', 'portfolio', 'risk', 'execution', 'weekend', 'catalyst')):
        return 'strategy'
    return 'other'


def _parse_sections(text):
    sections = []
    pattern = re.compile(
        r'###\s*\*?\*?(?:(?:Step\s+)?(\d+)[:.]?\s*)?(.+?)\*?\*?\s*\n',
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    for idx, m in enumerate(matches):
        number = int(m.group(1)) if m.group(1) else idx + 1
        title = m.group(2).strip().strip('*').strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        body = re.sub(r'\n---+\s*$', '', body).strip()
        items = _parse_bullets(body)
        sections.append({
            "number": number,
            "title": title,
            "kind": _classify_section(title),
            "items": items,
        })
    return sections


def _build_indexes(sections):
    rows = []
    for sec in sections:
        if sec['kind'] != 'indexes':
            continue
        for item in sec['items']:
            title = item['title']
            text = item['text']
            # Skip pure analysis bullets without an index name feel
            pct = _pct_from_text(text)
            rows.append({
                "name": title or "Index note",
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
                "pct": _pct_from_text(text),
                "action": text,
            })
    return rows


def _build_stocks(sections):
    """Build stock rows. Nested sub-bullets (Technical Action, Fundamentals)
    without their own ticker are merged into the parent stock's action."""
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
                pct = _pct_from_text(text)
                current = {
                    "ticker": tickers[0],
                    "tickers": tickers,
                    "company": company,
                    "pct": pct,
                    "action": text,
                }
            elif current is not None:
                # Sub-bullet under current stock
                label = title.strip() if title else ""
                chunk = f"{label}: {text}" if label else text
                if current["action"]:
                    current["action"] = f"{current['action']} | {chunk}"
                else:
                    current["action"] = chunk
                if not current["pct"]:
                    current["pct"] = _pct_from_text(text)
            else:
                # Orphan bullet with no parent — keep if it has a body ticker
                body_tickers = _extract_tickers(title, text, title_only=False)
                company = re.sub(r'\s*\([^)]*\)\s*$', '', title).strip() if title else ""
                rows.append({
                    "ticker": body_tickers[0] if body_tickers else "",
                    "tickers": body_tickers,
                    "company": company,
                    "pct": _pct_from_text(text),
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
