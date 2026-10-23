"""JPX official TOPIX 500 component snapshot loader.

Uses JPX's October 7, 2026 index-classification announcement. The announcement
provides both the old classification (as of Oct 7) and the NEW classification
that becomes effective at the close of October 30, 2026.

No approximation by market cap and no silent fallback to five sample symbols.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

ROOT = Path(__file__).resolve().parent
SNAPSHOT_DIR = ROOT / 'data' / 'universes' / 'JP'
SOURCE_URL = 'https://www.jpx.co.jp/news/6030/t13vrt00000262wd-att/mei2_12_size.pdf'
CHANGE_DATE = date(2026, 10, 30)
# Classification on the official document is 'old (as at Oct 7)' vs
# 'new (effective Oct 30)'. The October 2026 snapshots have 493 (pre) and 501 (post) components.
SNAPSHOTS = {
    'pre': SNAPSHOT_DIR / 'topix500_pre_20261030.csv',
    'post': SNAPSHOT_DIR / 'topix500_from_20261030.csv',
}
VALID_GROUPS = ('TOPIX Core30', 'TOPIX Large70', 'TOPIX Mid400')
# Verified from the JPX 2026-10-07 table and its 2026-10-30 additions/removals.
# TOPIX 500 is an index NAME; its live constituent count need not equal 500.
# Before: 31 + 68 + 394 = 493.
# Effective Oct 30: (31 + 4 - 5) + (68 + 10 - 8) + (394 + 21 - 14)
#                   = 30 + 70 + 401 = 501.
EXPECTED_COUNTS = {
    'pre': {'TOPIX Core30': 31, 'TOPIX Large70': 68, 'TOPIX Mid400': 394},
    'post': {'TOPIX Core30': 30, 'TOPIX Large70': 70, 'TOPIX Mid400': 401},
}

CODE_RE = re.compile(r'^(?:\d{4}|\d{3}[A-Z])$')


def _clean(value):
    if value is None:
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip()


def _group(value):
    txt = _clean(value).lower().replace(' ', '')
    if 'core30' in txt:
        return 'TOPIX Core30'
    if 'large70' in txt:
        return 'TOPIX Large70'
    if 'mid400' in txt:
        return 'TOPIX Mid400'
    return ''


def parse_jpx_pdf(data: bytes):
    """Return (old, new) mappings from JPX's five-column classification table.

    Rows are [No., stock code, name, OLD class, NEW class]. The first two pages
    contain change summaries and must never be interpreted as constituent rows.
    """
    old, new = {}, {}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) < 4:
            raise ValueError('JPX PDF layout changed: unexpectedly few pages')
        for page in pdf.pages[2:]:
            tables = page.extract_tables(table_settings={
                'vertical_strategy': 'lines',
                'horizontal_strategy': 'lines',
                'snap_tolerance': 3,
                'join_tolerance': 3,
                'intersection_tolerance': 3,
            })
            for table in tables:
                for row in table:
                    if not row or len(row) < 5:
                        continue
                    cells = [_clean(x) for x in row]
                    # The document's constituent table is 5 columns. Avoid
                    # accidental re-interpretation of summary tables.
                    if len(cells) != 5 or not cells[0].isdigit():
                        continue
                    code = cells[1].upper().replace(' ', '')
                    if not CODE_RE.fullmatch(code):
                        continue
                    old_group, new_group = _group(cells[3]), _group(cells[4])
                    if old_group:
                        if code in old and old[code] != old_group:
                            raise ValueError(f'Duplicate inconsistent old classification for {code}')
                        old[code] = old_group
                    if new_group:
                        if code in new and new[code] != new_group:
                            raise ValueError(f'Duplicate inconsistent new classification for {code}')
                        new[code] = new_group
    for label, groups in [('pre', old), ('post', new)]:
        sizes = {group: sum(x == group for x in groups.values()) for group in VALID_GROUPS}
        if sizes != EXPECTED_COUNTS[label]:
            raise ValueError(
                f'Official TOPIX500 {label} extraction failed validation: '
                f'{len(groups)} tickers and {sizes}; expected {EXPECTED_COUNTS[label]}. ' \
                'Refusing silent fallback.'
            )
    return old, new


def download_pdf():
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; AcademicStockForecast/1.3)',
        'Accept': 'application/pdf,*/*',
    }
    response = requests.get(SOURCE_URL, headers=headers, timeout=90)
    response.raise_for_status()
    if not response.content.startswith(b'%PDF'):
        raise ValueError('JPX endpoint did not return a PDF')
    return response.content


def _write_snapshot(path: Path, groups: dict[str, str], pdf_sha256: str):
    rows = [{'ticker': f'{code}.T', 'code': code, 'index_group': group,
             'source': 'JPX', 'source_document_date': '2026-10-07',
             'source_pdf_sha256': pdf_sha256}
            for code, group in sorted(groups.items())]
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_csv(path, dtype=str)
        if (set(zip(existing['ticker'], existing['index_group']))
                != set(zip(df['ticker'], df['index_group']))):
            raise RuntimeError(
                f'Existing TOPIX 500 snapshot differs: {path}. '
                'A membership snapshot is immutable; investigate rather than overwrite.'
            )
        return
    df.to_csv(path, index=False, mode='x', encoding='utf-8')


def snapshot_path(as_of: date):
    return SNAPSHOTS['post' if as_of >= CHANGE_DATE else 'pre']


def validate_snapshot(path: Path):
    """Validate membership against the documented JPX October 2026 counts.

    Do not pad the official universe with stocks outside the index merely
    to force a round number of 500.
    """
    path = Path(path)
    if path == SNAPSHOTS['pre']:
        phase = 'pre'
    elif path == SNAPSHOTS['post']:
        phase = 'post'
    else:
        raise ValueError(f'Unrecognized official TOPIX 500 snapshot path: {path}')
    df = pd.read_csv(path, dtype=str)
    expected_columns = {'ticker', 'index_group'}
    if not expected_columns.issubset(df.columns):
        raise ValueError(f'{path}: missing required columns {expected_columns - set(df.columns)}')
    expected_counts = EXPECTED_COUNTS[phase]
    expected_total = sum(expected_counts.values())
    if len(df) != expected_total or df.ticker.nunique() != expected_total:
        raise ValueError(
            f'{path}: expected {expected_total} distinct tickers per JPX, '
            f'got {len(df)} rows and {df.ticker.nunique()} distinct tickers')
    sizes = df.index_group.value_counts().to_dict()
    if sizes != expected_counts:
        raise ValueError(
            f'{path}: JPX official index group counts mismatch: '
            f'found {sizes}, expected {expected_counts}')
    return df


def ensure_snapshot(as_of: date | None = None):
    if as_of is None:
        # The JP workflow runs after JPX close; use the Japanese market date.
        from zoneinfo import ZoneInfo
        as_of = datetime.now(ZoneInfo('Asia/Tokyo')).date()
    chosen = snapshot_path(as_of)
    if chosen.exists():
        df = validate_snapshot(chosen)
        return chosen, df
    data = download_pdf()
    old, new = parse_jpx_pdf(data)
    pdf_sha256 = hashlib.sha256(data).hexdigest()
    _write_snapshot(SNAPSHOTS['pre'], old, pdf_sha256)
    _write_snapshot(SNAPSHOTS['post'], new, pdf_sha256)
    df = validate_snapshot(chosen)
    return chosen, df


def get_tickers(as_of: date | None = None):
    path, df = ensure_snapshot(as_of)
    return df.ticker.tolist(), str(path.relative_to(ROOT))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Prepare verified JPX TOPIX 500 universe')
    ap.add_argument('--as-of', type=date.fromisoformat)
    args = ap.parse_args()
    path, df = ensure_snapshot(args.as_of)
    print(json.dumps({
        'status': 'ok', 'universe': 'TOPIX500', 'count': len(df),
        'snapshot': str(path.relative_to(ROOT)),
        'source': SOURCE_URL,
    }, ensure_ascii=False, indent=2))
