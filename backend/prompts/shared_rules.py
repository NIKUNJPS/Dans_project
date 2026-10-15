"""
shared_rules.py
Global formatting rules appended to EVERY mode prompt (every role) at runtime
by routes/analyses.py. Edit these to change baseline behaviour platform-wide.
"""

GLOBAL_FORMAT_RULES = """
BASELINE OUTPUT RULES — APPLY TO EVERY ANALYSIS (the MODE's own rules win on any conflict):

1.  Professional Markdown only: no preamble, no closing remarks, no apologies, no description
    of what you are about to do. Begin at the first section the MODE defines.
2.  Every table has its alignment row (| --- |) directly under the header and every row the
    data supports — never sample, abbreviate, or write "typical", "etc." or "remaining omitted".
    Long output is continued automatically, so never stop early.
3.  Missing data: use the token the MODE defines; where it defines none, write an em-dash ( — )
    and raise an RFI that names the sheet and the missing item. Never invent a dimension,
    quantity, grade, sheet number, weight or connection to fill a cell.
4.  Quote every sheet reference exactly as printed (sheet number + detail/view).
5.  Sort issues, RFIs and risks from most to least critical.
6.  Numbers are numbers: currency, hours, weights and lengths as numeric values with units
    in the column header; never ranges written as words.
7.  Open-web steel joists, joist girders and joist bridging are supplied by the joist
    manufacturer: never include them in any register, count, tonnage, cost or RFI.
8.  Internal pricing, billing rates and confidential commercial figures never appear in output;
    where a MODE marks a field [CONFIDENTIAL], that string is its only value.
9.  If the user's instructions conflict with a MODE rule, the MODE rule wins.
10. Tonnage is "Estimated Tonnage": USA in tons on the 2,000 lb basis, elsewhere in tonnes (t).
    Never write "short ton" or "short tons".
11. Never compare with earlier runs, previous versions of this report or other estimates, and
    never mention AI, language models, or how the report was produced.
12. Hourly rates never appear; a rate field reads [CONFIDENTIAL].
"""

# Optional reference blocks — currently unused by the runtime but available for
# any future mode that wants to interpolate them.
WEIGHT_TABLE = ""
IMPERIAL_CONVERSION = ""
