#!/usr/bin/env python3
"""
Zotero Bulk ISBN & DOI Importer
================================
Fetches bibliographic metadata for a list of ISBNs (via Open Library API)
and DOIs (via CrossRef API), then uploads them to your Zotero library
using the Zotero Web API v3. Must sign up for a Zotero account at Zotero.org and obtain an API key to use this script.

Setup
-----
1. Install dependencies:
       pip install requests

2. Get your Zotero credentials from https://www.zotero.org/settings/security:
       - USER_ID  : shown under "Your userID for use in API calls"
       - API_KEY  : create a key with Read/Write library access

3. Fill in the configuration block below (or set as environment variables).

Usage
-----
    python zotero_bulk_import.py

Edit the ISBN_LIST and DOI_LIST constants, or replace them with file-based
input (see "Loading from a file" comments below).
"""

import os
import time
import json
import requests

# ─────────────────────────────────────────────
#  CONFIGURATION — fill these in
# ─────────────────────────────────────────────
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "YOUR_USER_ID_HERE")
ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "YOUR_API_KEY_HERE")

# Optional: add items to a specific Zotero collection.
# Leave as None to add to the root library.
# To find a collection key: GET https://api.zotero.org/users/<ID>/collections
ZOTERO_COLLECTION_KEY = None  # e.g. "ABCD1234"

# How many items to POST to Zotero per request (max allowed by API is 50)
ZOTERO_BATCH_SIZE = 25

# Seconds to wait between metadata lookups (be polite to free APIs)
LOOKUP_DELAY = 0.5

# ─────────────────────────────────────────────
#  YOUR IDENTIFIERS
# ─────────────────────────────────────────────
ISBN_LIST = [
    "9780385333481",   # example: The Road — Cormac McCarthy
    "9780062316097",   # example: Sapiens — Yuval Noah Harari
    "9780525559474",   # example: The Midnight Library — Matt Haig
    # Add more ISBNs here (dashes optional, both ISBN-10 and ISBN-13 work)
]

DOI_LIST = [
    "10.1038/nature12373",    # example: a Nature paper
    "10.1126/science.1259855", # example: a Science paper
    # Add more DOIs here
]

# ─────────────────────────────────────────────
#  Loading from a file (optional)
# ─────────────────────────────────────────────
# Uncomment and edit these lines to read identifiers from plain-text files
# (one identifier per line):
#
# with open("isbns.txt") as f:
#     ISBN_LIST = [line.strip() for line in f if line.strip()]
#
# with open("dois.txt") as f:
#     DOI_LIST = [line.strip() for line in f if line.strip()]


# ═══════════════════════════════════════════════════════════════════════════
#  METADATA LOOKUP FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def lookup_isbn(isbn: str) -> dict | None:
    """
    Fetch book metadata from the Open Library API for a given ISBN.
    Returns a Zotero-formatted item dict, or None on failure.

    Open Library API docs: https://openlibrary.org/dev/docs/api#anchor_books
    """
    isbn_clean = isbn.replace("-", "").replace(" ", "")
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn_clean}&format=json&jscmd=data"

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  [ISBN {isbn}] Network error: {e}")
        return None

    key = f"ISBN:{isbn_clean}"
    if key not in data:
        print(f"  [ISBN {isbn}] Not found in Open Library.")
        return None

    book = data[key]

    # --- Parse authors ---
    creators = []
    for author in book.get("authors", []):
        name = author.get("name", "")
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            creators.append({
                "creatorType": "author",
                "firstName": parts[0],
                "lastName": parts[1],
            })
        else:
            creators.append({"creatorType": "author", "name": name})

    # --- Parse publishers ---
    publishers = [p.get("name", "") for p in book.get("publishers", [])]
    publisher = "; ".join(publishers)

    # --- Parse publish places ---
    places = [p.get("name", "") for p in book.get("publish_places", [])]
    place = "; ".join(places)

    # --- Parse date ---
    date = book.get("publish_date", "")

    # --- Parse page count ---
    num_pages = str(book.get("number_of_pages", ""))

    # --- Build Zotero item ---
    item = {
        "itemType": "book",
        "title": book.get("title", ""),
        "creators": creators,
        "publisher": publisher,
        "place": place,
        "date": date,
        "numPages": num_pages,
        "ISBN": isbn_clean,
        "url": book.get("url", ""),
        "tags": [],
        "collections": [ZOTERO_COLLECTION_KEY] if ZOTERO_COLLECTION_KEY else [],
        "relations": {},
    }

    # Add subtitle if present
    subtitle = book.get("subtitle")
    if subtitle:
        item["title"] = f"{item['title']}: {subtitle}"

    return item


def lookup_doi(doi: str) -> dict | None:
    """
    Fetch article metadata from the CrossRef API for a given DOI.
    Returns a Zotero-formatted item dict, or None on failure.

    CrossRef API docs: https://api.crossref.org/swagger-ui/index.html
    """
    url = f"https://api.crossref.org/works/{doi}"
    headers = {"User-Agent": "ZoteroBulkImporter/1.0 (mailto:your@email.com)"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("message", {})
    except requests.RequestException as e:
        print(f"  [DOI {doi}] Network error: {e}")
        return None

    # --- Detect item type ---
    crossref_type = data.get("type", "journal-article")
    zotero_type = {
        "journal-article": "journalArticle",
        "book":            "book",
        "book-chapter":    "bookSection",
        "proceedings-article": "conferencePaper",
        "dataset":         "dataset",
        "report":          "report",
        "dissertation":    "thesis",
    }.get(crossref_type, "journalArticle")

    # --- Parse authors ---
    creators = []
    for author in data.get("author", []):
        creators.append({
            "creatorType": "author",
            "firstName": author.get("given", ""),
            "lastName": author.get("family", ""),
        })

    # --- Parse date ---
    date_parts = (
        data.get("published-print") or
        data.get("published-online") or
        data.get("issued") or {}
    ).get("date-parts", [[]])[0]
    date = "-".join(str(p) for p in date_parts) if date_parts else ""

    # --- Parse pages ---
    pages = data.get("page", "")

    # --- Build base item ---
    item = {
        "itemType": zotero_type,
        "title": data.get("title", [""])[0],
        "creators": creators,
        "date": date,
        "pages": pages,
        "DOI": doi,
        "url": f"https://doi.org/{doi}",
        "tags": [],
        "collections": [ZOTERO_COLLECTION_KEY] if ZOTERO_COLLECTION_KEY else [],
        "relations": {},
    }

    # --- Type-specific fields ---
    if zotero_type == "journalArticle":
        item["publicationTitle"] = (data.get("container-title") or [""])[0]
        item["volume"] = data.get("volume", "")
        item["issue"] = data.get("issue", "")
        item["ISSN"] = (data.get("ISSN") or [""])[0]
    elif zotero_type in ("book", "bookSection"):
        item["publisher"] = (data.get("publisher") or "")
        item["ISBN"] = (data.get("ISBN") or [""])[0]
        if zotero_type == "bookSection":
            item["bookTitle"] = (data.get("container-title") or [""])[0]
    elif zotero_type == "conferencePaper":
        item["proceedingsTitle"] = (data.get("container-title") or [""])[0]

    # Remove empty string fields to keep the payload clean
    item = {k: v for k, v in item.items() if v != ""}

    return item


# ═══════════════════════════════════════════════════════════════════════════
#  ZOTERO API FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

ZOTERO_BASE = "https://api.zotero.org"

def zotero_headers() -> dict:
    return {
        "Zotero-API-Key": ZOTERO_API_KEY,
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
    }


def get_library_version() -> int:
    """Retrieve the current library version number (needed for write requests)."""
    url = f"{ZOTERO_BASE}/users/{ZOTERO_USER_ID}/items?limit=1"
    resp = requests.get(url, headers=zotero_headers(), timeout=10)
    resp.raise_for_status()
    return int(resp.headers.get("Last-Modified-Version", 0))


def post_items_to_zotero(items: list[dict]) -> dict:
    """
    POST a batch of up to 50 items to the Zotero Web API.

    The API returns a summary of successes, failures, and unchanged items.
    Reference: https://www.zotero.org/support/dev/web_api/v3/write_requests
    """
    url = f"{ZOTERO_BASE}/users/{ZOTERO_USER_ID}/items"
    library_version = get_library_version()

    resp = requests.post(
        url,
        headers={**zotero_headers(), "If-Unmodified-Since-Version": str(library_version)},
        data=json.dumps(items),
        timeout=30,
    )

    if resp.status_code == 403:
        raise PermissionError(
            "Zotero API returned 403 Forbidden. "
            "Check your API key has write access."
        )
    resp.raise_for_status()
    return resp.json()


def upload_in_batches(items: list[dict]) -> None:
    """Split items into batches and upload each to Zotero."""
    total = len(items)
    uploaded = 0
    failed = 0

    for i in range(0, total, ZOTERO_BATCH_SIZE):
        batch = items[i : i + ZOTERO_BATCH_SIZE]
        batch_num = i // ZOTERO_BATCH_SIZE + 1
        print(f"\n→ Uploading batch {batch_num} ({len(batch)} items)...")

        try:
            result = post_items_to_zotero(batch)
        except requests.HTTPError as e:
            print(f"  ERROR uploading batch: {e}")
            failed += len(batch)
            continue

        success_count = len(result.get("successful", {}))
        failed_count  = len(result.get("failed", {}))
        uploaded += success_count
        failed   += failed_count

        print(f"  ✓ {success_count} succeeded, {failed_count} failed")

        # Report any individual failures
        for key, info in result.get("failed", {}).items():
            title = batch[int(key)].get("title", "?")
            print(f"    Failed item [{key}]: '{title}' — {info.get('message', '')}")

        # Brief pause between batches to be kind to the API
        if i + ZOTERO_BATCH_SIZE < total:
            time.sleep(1)

    print(f"\n{'═'*50}")
    print(f"Upload complete: {uploaded} added, {failed} failed, {total} total")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("Zotero Bulk Importer")
    print("=" * 50)

    if ZOTERO_USER_ID == "YOUR_USER_ID_HERE" or ZOTERO_API_KEY == "YOUR_API_KEY_HERE":
        print("ERROR: Please set ZOTERO_USER_ID and ZOTERO_API_KEY in the script "
              "or as environment variables before running.")
        return

    zotero_items = []

    # ── Step 1: Resolve ISBNs ─────────────────────────────────────────────
    if ISBN_LIST:
        print(f"\nLooking up {len(ISBN_LIST)} ISBN(s) via Open Library...")
        for isbn in ISBN_LIST:
            print(f"  ISBN: {isbn}", end=" ... ")
            item = lookup_isbn(isbn)
            if item:
                print(f"✓ Found: \"{item['title']}\"")
                zotero_items.append(item)
            time.sleep(LOOKUP_DELAY)

    # ── Step 2: Resolve DOIs ──────────────────────────────────────────────
    if DOI_LIST:
        print(f"\nLooking up {len(DOI_LIST)} DOI(s) via CrossRef...")
        for doi in DOI_LIST:
            print(f"  DOI: {doi}", end=" ... ")
            item = lookup_doi(doi)
            if item:
                print(f"✓ Found: \"{item.get('title', '?')}\"")
                zotero_items.append(item)
            time.sleep(LOOKUP_DELAY)

    # ── Step 3: Upload to Zotero ──────────────────────────────────────────
    if not zotero_items:
        print("\nNo items resolved. Nothing to upload.")
        return

    print(f"\nResolved {len(zotero_items)} item(s) total. Uploading to Zotero...")
    upload_in_batches(zotero_items)

    print("\nDone! Open Zotero (or refresh your web library) to see the new items.")
    print("Remember to spot-check metadata — automated lookups can have gaps.")


if __name__ == "__main__":
    main()
