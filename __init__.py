"""Collectors. Each returns a list[Post] or raises; the orchestrator decides the fallback.

Live sources (no scraping of logged-in pages):
  telegram  - public channel preview page t.me/s/<handle>, no key needed
  x         - Apify actor (configurable), needs APIFY_TOKEN
  linkedin  - Apify actor for a public company page (configurable), needs APIFY_TOKEN;
              in production replaced by the Page admin analytics export (csv_import handles that file)
"""
