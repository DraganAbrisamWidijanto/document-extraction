"""Kleister-Charity extraction prompt + schema constants.

Schema: 8 fields from UK charity annual reports (registered charity info +
annual income/spending + report date). Used per-page: each VLM call reads ONE
rendered page and returns whatever fields are visible on it; the multi-page
pipeline merges page results later.
"""

KLEISTER_PROMPT = """You are a document extraction system for UK charity annual reports. Extract structured data from the PAGE IMAGE shown.

Return ONLY valid JSON (no markdown fences, no commentary) with this exact schema:

{
  "charity_name": "registered charity name as printed, or null",
  "charity_number": "registered charity number (numeric, as string), or null",
  "address_post_town": "post town of the registered office, or null",
  "address_postcode": "full postcode e.g. 'WR12 7NL', or null",
  "address_street_line": "street address line (building/street), or null",
  "income_annually_in_british_pounds": <integer GBP or null>,
  "spending_annually_in_british_pounds": <integer GBP or null>,
  "report_date": "report date as YYYY-MM-DD, or null",
  "confidence": {
    "charity_name": 0-1,
    "charity_number": 0-1,
    "address_post_town": 0-1,
    "address_postcode": 0-1,
    "address_street_line": 0-1,
    "income_annually_in_british_pounds": 0-1,
    "spending_annually_in_british_pounds": 0-1,
    "report_date": 0-1
  }
}

IMPORTANT RULES:
- The page image is the source of truth. Only extract fields VISIBLE on THIS page.
- If a field is not present on this page and cannot be unambiguously derived, use null. NEVER invent values.
- null means absent/illegible on this page. 0 means the field is explicitly printed as 0.
- Monetary fields: full annual figure in British pounds, integer only (no decimals, no £, no commas). E.g. '£10,348,000' -> 10348000. If the financial statements table shows figures in £'000 (thousands), multiply by 1000 to get the full GBP amount. Use the exact figures shown, not approximations: NEVER round to a round number. If the page only shows rounded/approximate figures (e.g. '£11.7m') and not the exact amount, return null rather than an approximation.
- Financial tables often have MULTIPLE columns (e.g. 'Unrestricted'/'Restricted'/'TOTAL 2018'/'TOTAL 2017'). For income and spending, use the CURRENT report year's TOTAL column (the most recent year), never the prior-year comparative column, never a sub-total of only unrestricted or restricted funds.
- charity_number: numeric registration number as a plain string, e.g. "1155074".
- report_date: the financial period end date, ISO format YYYY-MM-DD. Use the date following 'year ended'/'year ending'/'period ended' (e.g. 'Year ended 31 March 2016' -> 2016-03-31). NEVER use audit/sign-off dates, signature dates, or printing/cover dates.
- address fields: Kleister GT records the charity's REGISTERED OFFICE address. UK charity reports often print MORE THAN ONE address block (registered office, head office, correspondence address, venue/museum address). Extract post_town, postcode and street_line from the block labeled 'Registered Office' when present; otherwise the primary correspondence address. NEVER use the public venue/visitor address (e.g. the museum site itself) when a registered office block exists.
- address_street_line: the building and street, e.g. 'Interchurch House, 35 Lower Marsh' — include the building name if printed. Do not include post town or postcode in this field.
- If the page lists a TABLE of multiple office addresses (regional offices, appendices), extract ONLY the one labeled 'Head Office' or 'Registered Office' — ignore the other regional offices. If neither label appears, extract the first/primary address listed for the charity.
- confidence: how sure you are the extracted value is correct, 0.0-1.0. Set 0.0 for any field set to null.
"""

# Field order = deterministic for merging/eval.
KLEISTER_FIELDS = [
    "charity_name",
    "charity_number",
    "address_post_town",
    "address_postcode",
    "address_street_line",
    "income_annually_in_british_pounds",
    "spending_annually_in_british_pounds",
    "report_date",
]

# Monetary fields compared numerically; the rest are compared as strings.
KLEISTER_NUMERIC = {
    "income_annually_in_british_pounds",
    "spending_annually_in_british_pounds",
}

# Keywords for scoring which PDF pages carry useful signal. Keyed by field
# group; a page's score = number of keyword hits across all groups (page 1
# always included).
PAGE_SCORE_KEYWORDS = {
    "charity": [
        "charity name", "charity number", "registered charity",
        "company number", "registered company", "charitable",
        "report and financial statements", "trustees",
    ],
    "address": [
        "address", "registered office", "postcode", "post town",
        "correspondence address", "street", "limited by guarantee",
        "company number", "registered charity",
    ],
    "income": [
        "income", "revenue", "receipts", "incoming resources",
        "turnover", "donations", "total income", "incoming",
        "legacies", "grants", "fundraising", "statement of financial activities",
        "total funds",
    ],
    "spending": [
        "expenditure", "spending", "expenses", "resources expended",
        "costs", "charitable expenditure", "total expenditure", "funds expended",
    ],
    "date": [
        "year ended", "year ending", "report date", "for the year",
        "financial statements", "annual report", "period ended",
        "31 march", "31 december",
    ],
}
