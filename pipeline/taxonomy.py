"""Fixed labels. The analysis is only comparable across runs if every post is
labelled against the same closed set, so the model is forced to pick from these.
"""

THEMES = {
    "market_commentary": "Weekly market view, macro, price action, sentiment (e.g. The Weekly)",
    "research_report": "A Re7 research paper or framework, usually with a named partner (Clear Street, Zodia)",
    "media_appearance": "Re7 people on TV, podcasts or in press (e.g. Bloomberg Crypto Minutes, 1000x)",
    "speaking_event": "Re7 people speaking at or attending a conference or summit",
    "labs_product": "Re7 Labs vaults, integrations, curation, protocol partnerships",
    "partner_announcement": "A commercial or custody partnership for the fund (e.g. Zodia, MIRAI)",
    "chart_data": "A standalone chart or data point with brief commentary",
    "hiring_culture": "Job posts, team news, culture",
    "award_recognition": "Awards, rankings, shortlists",
    "other": "Anything that does not fit above (replies, housekeeping, promos)",
}

FORMATS = ["text", "link", "image", "video", "repost", "quote", "thread", "document"]

ATTRIBUTES = {
    "names_person": "Post names or tags a specific person (e.g. Evgeny Gokhberg, a guest, a journalist)",
    "names_partner": "Post names or tags a partner firm, outlet or protocol (e.g. Bloomberg, Clear Street, Morpho)",
}

# Compliance: anything matching these needs a human check before scheduling.
COMPLIANCE_TRIGGERS = {
    "yield_or_performance": ["%", "apy", "apr", "yield", "return", "performance", "outperform"],
    "third_party_named": [],  # filled dynamically from names_partner
    "promissory_language": ["guaranteed", "safe", "risk-free", "earn", "invest now", "dm us", "join"],
}
