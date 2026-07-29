"""Read and write the three cache layers."""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from renov_market_scan.cache.schema import SCHEMA_SQL
from renov_market_scan.models import Condition, Listing, RawResponse


def open_store(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the cache database with its schema applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_SQL)
    connection.commit()
    return connection


def save_raw(connection: sqlite3.Connection, response: RawResponse) -> None:
    """Persist an untouched API response in its own transaction."""
    connection.execute(
        "INSERT OR REPLACE INTO raw_search "
        "(search_key, source, phrase_index, collected_on, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            response.search_key,
            response.source,
            response.phrase_index,
            response.collected_on,
            json.dumps(response.payload, ensure_ascii=False),
            response.status,
        ),
    )
    connection.commit()


def has_raw(
    connection: sqlite3.Connection,
    search_key: str,
    source: str,
    phrase_index: int,
    collected_on: str,
) -> bool:
    """True when this exact search was already performed on this day."""
    row = connection.execute(
        "SELECT 1 FROM raw_search "
        "WHERE search_key = ? AND source = ? AND phrase_index = ? AND collected_on = ?",
        (search_key, source, phrase_index, collected_on),
    ).fetchone()
    return row is not None


def count_raw(connection: sqlite3.Connection, collected_on: str) -> int:
    """How many raw responses are cached for a given day."""
    row = connection.execute(
        "SELECT COUNT(*) FROM raw_search WHERE collected_on = ?", (collected_on,)
    ).fetchone()
    return int(row[0])


def save_listings(
    connection: sqlite3.Connection,
    search_key: str,
    source: str,
    collected_on: str,
    listings: list[Listing],
) -> None:
    """Replace the extracted listings for one (key, source, day).

    Key columns (search_key, source) come from the function parameters, not from
    the listing objects, so the DELETE and INSERT operate on the same row identity.
    This prevents orphaned rows when a listing's own key differs from the arguments.
    """
    connection.execute(
        "DELETE FROM listing WHERE search_key = ? AND source = ? AND collected_on = ?",
        (search_key, source, collected_on),
    )
    connection.executemany(
        "INSERT INTO listing "
        "(search_key, source, collected_on, title, price_brl, condition, url, "
        " captured_at, cited_text, flag_5g_divergent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                search_key,
                source,
                collected_on,
                item.title,
                item.price_brl,
                item.condition,
                item.url,
                item.captured_at,
                item.cited_text,
                int(item.flag_5g_divergent),
            )
            for item in listings
        ],
    )
    connection.commit()


def load_listings(connection: sqlite3.Connection, collected_on: str) -> dict[str, list[Listing]]:
    """Every cached listing for a day, grouped by search key."""
    rows = connection.execute(
        "SELECT search_key, source, title, price_brl, condition, url, captured_at, "
        "       cited_text, flag_5g_divergent "
        "FROM listing WHERE collected_on = ? ORDER BY rowid",
        (collected_on,),
    ).fetchall()
    grouped: dict[str, list[Listing]] = defaultdict(list)
    for row in rows:
        condition: Condition = row[4]
        grouped[row[0]].append(
            Listing(
                search_key=row[0],
                source=row[1],
                title=row[2],
                price_brl=row[3],
                condition=condition,
                url=row[5],
                captured_at=row[6],
                cited_text=row[7],
                flag_5g_divergent=bool(row[8]),
            )
        )
    return dict(grouped)
