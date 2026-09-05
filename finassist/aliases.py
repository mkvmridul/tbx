"""finassist/aliases.py — the acronym map.

No string rule can know that "TCS" is Tata Consultancy Services or that "L AND T" is
Larsen and Toubro. Those are facts about the world, not properties of the text, so they
live in a table a finance team edits rather than in the parser.

This is seeded with common Indian corporate short forms. In production it is the file the
finance ops team owns: when the assistant reports two vendors that are really one, someone
adds a line here and re-runs the enrichment.

Keys and values are BOTH canon_key outputs (upper-case, no spaces, suffixes stripped).
Left side is the abbreviation actually seen in narrations; right side is the group it
belongs to. Verify a mapping before adding it -- a wrong line here silently merges two
real vendors into one wrong total, which is exactly the failure this whole layer exists
to prevent.
"""

ACRONYMS = {
    "TCS":            "TATACONSULTANCYSERVICES",
    "LT":             "LARSENTOUBRO",
    "AIRTEL":         "BHARTIAIRTEL",
    "INDIGOAIRLINES": "INTERGLOBEAVIATION",
    "BESCOM":         "BESCOMBANGALORE",
    "HDFCLIFE":       "HDFCLIFEINSURANCE",
    "ICICILOMBARD":   "ICICILOMBARDGENERALINSURANCE",
    "SBI":            "STATEBANKOFINDIA",
    "BOB":            "BANKOFBARODA",
    "PNB":            "PUNJABNATIONALBANK",
    "MMT":            "MAKEMYTRIP",
    "RIL":            "RELIANCEINDUSTRIES",
}

# Deliberately NOT mapped, with the reason, so nobody "fixes" them later:
#   AMAZONWEBSERVICES -> AMAZONSELLERSERVICES
#     AWS and Amazon Seller Services are different businesses that happen to share a
#     parent. Merging them would overstate either one's spend. If your chart of accounts
#     treats them as one supplier, add the line -- but that is an accounting decision.
