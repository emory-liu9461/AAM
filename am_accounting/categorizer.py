"""Automatic transaction categorization.

Three layers, tried in order:

1. **Your rules** - "if the description contains X, use account Y". Highest priority.
2. **Learned** - transactions from the same merchant you already categorized
   by hand (or by rule). The app gets better the more you use it.
3. **Built-in keywords** - common merchants and bank phrases (fees, payroll,
   SaaS, fuel, ...), used as a starting suggestion.

Transactions you categorized by hand are never overwritten.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .books import Books, Rule, Transaction

# (regex, account code, direction) - direction: "in", "out" or "any". Order matters.
BUILTIN_KEYWORDS: list[tuple[str, str, str]] = [
    (r"\b(transfer|xfer|trnsfr)\b.*\b(to|from)\b.*\b(sav|chk|checking|savings|acct|account)", "1090", "any"),
    (r"online (banking )?transfer|internal transfer|zelle to self", "1090", "any"),
    (r"interest (paid|earned|credit|payment)|\bint(erest)? pd\b|interest$", "4800", "in"),
    (r"interest (charge|expense)|finance charge|loan interest", "6900", "out"),
    (r"overdraft|\bnsf\b|service (charge|fee)|monthly (maintenance )?fee|wire fee|atm fee|\bfee\b|stripe fee|merchant fee", "6050", "out"),
    (r"facebook|fb\.me|meta ads|meta platforms|google ads|adwords|linkedin ads|mailchimp|yelp|tiktok ads|bing ads", "6000", "out"),
    (r"adobe|microsoft|msft|google ?(workspace|gsuite|cloud|\*)|aws|amazon web services|github|dropbox|zoom\.us|\bzoom\b|slack|notion|atlassian|openai|anthropic|claude\.ai|intuit|quickbooks|xero|canva|figma|shopify|squarespace|wix|godaddy|namecheap|apple\.com/bill|icloud|netflix|spotify|hubspot|salesforce|docusign", "6500", "out"),
    (r"gusto|\badp\b|paychex|payroll|rippling|justworks", "6750", "out"),
    (r"\birs\b|usataxpymt|eftps|franchise tax|dept of revenue|department of revenue|state tax|tax payment|\bdmv\b|secretary of state|business license", "6550", "out"),
    (r"uber(?! ?eats)|lyft|airbnb|delta air|united air|american air|southwest|jetblue|alaska air|marriott|hilton|hyatt|expedia|booking\.com|amtrak|hertz|avis|enterprise rent", "6650", "out"),
    (r"uber ?eats|doordash|grubhub|starbucks|mcdonald|chipotle|restaurant|\bcafe\b|coffee|pizza|burger|taco|sushi|kitchen|grill|bistro|diner|panera|subway", "6250", "out"),
    (r"shell|chevron|exxon|mobil|\bbp\b|arco|valero|texaco|sunoco|76 |circle k|speedway|wawa|fuel|gas station|parking|toll|fastrak|ez ?pass|jiffy lube|car wash", "6800", "out"),
    (r"staples|office ?depot|officemax|best buy|amazon|amzn", "6300", "out"),
    (r"comcast|xfinity|verizon|at&t|\batt\b|t-mobile|tmobile|spectrum|cox comm|centurylink|ringcentral|twilio|google fi", "6600", "out"),
    (r"pg&e|pge|edison|con ?ed|duke energy|water (dept|district|utility)|electric|power co|gas co|utilit|waste management|republic services", "6700", "out"),
    (r"usps|fedex|\bups\b|dhl|stamps\.com|pirate ship|shipstation", "6850", "out"),
    (r"insurance|geico|state farm|progressive|allstate|hiscox|next insurance|the hartford|liberty mutual", "6150", "out"),
    (r"\brent\b|lease|property management|wework|regus", "6400", "out"),
    (r"cpa|accounting|bookkeep|law (office|firm)|attorney|legal|legalzoom", "6350", "out"),
    (r"upwork|fiverr|contractor|1099", "6100", "out"),
    (r"home depot|lowe'?s|ace hardware|repair|plumb|hvac|maintenance", "6450", "out"),
    (r"(credit )?card (payment|pymt|pmt)|payment to (chase|amex|american express|capital one|citi|discover)|amex epayment|applecard|crd epay|cardmember serv", "2100", "out"),
    (r"loan (payment|pmt)|sba loan|\bloan\b", "2500", "out"),
    (r"owner (draw|distribution)|distribution to owner", "3100", "out"),
    (r"owner (contribution|investment)|capital contribution", "3000", "in"),
    (r"refund|return|reversal|chargeback", "4500", "in"),
    (r"stripe|square|shopify payments|paypal|venmo|zelle from|etsy|amazon (payments|marketplace)|merchant (deposit|settlement)|invoice|client|customer", "4000", "in"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), code, d) for p, code, d in BUILTIN_KEYWORDS]


@dataclass
class Suggestion:
    account_id: int
    source: str  # rule | learned | keyword
    detail: str = ""


def _direction_ok(direction: str, amount_cents: int) -> bool:
    return direction == "any" or (direction == "in" and amount_cents > 0) or (direction == "out" and amount_cents < 0)


def rule_matches(rule: Rule, description: str, amount_cents: int) -> bool:
    if not _direction_ok(rule.direction, amount_cents):
        return False
    d = description.lower()
    p = rule.pattern.lower()
    if rule.match_type == "contains":
        return p in d
    if rule.match_type == "exact":
        return d.strip() == p.strip()
    try:
        return re.search(rule.pattern, description, re.IGNORECASE) is not None
    except re.error:
        return False


class Categorizer:
    def __init__(self, books: Books):
        self.books = books
        self.rules = books.rules()
        self._learned = self._build_learned()
        # Built-in suggestions point at the standard accounts by permanent key, so they keep
        # working after you rename or renumber those accounts.
        self._key_to_id = {a.key: a.id for a in books.accounts(active_only=True) if a.key}
        self._builtin = books.builtin_suggestions

    def _build_learned(self) -> dict[tuple[str, bool], Counter]:
        learned: dict[tuple[str, bool], Counter] = defaultdict(Counter)
        rows = self.books.conn.execute(
            "SELECT merchant_key, amount_cents, category_id FROM transactions "
            "WHERE category_id IS NOT NULL AND category_source IN ('manual','rule') AND merchant_key != ''"
        )
        for key, amount, cat in rows:
            learned[(key, amount > 0)][cat] += 1
        return learned

    def suggest(self, tx: Transaction) -> Suggestion | None:
        for rule in self.rules:
            if rule.account_id != tx.bank_account_id and rule_matches(rule, tx.description, tx.amount_cents):
                return Suggestion(rule.account_id, "rule", rule.pattern)

        counts = self._learned.get((tx.merchant_key, tx.amount_cents > 0))
        if counts:
            cat, n = counts.most_common(1)[0]
            if cat != tx.bank_account_id:
                return Suggestion(cat, "learned", f"{n} similar")

        if not self._builtin:
            return None
        for rx, key, direction in _COMPILED:
            if _direction_ok(direction, tx.amount_cents) and rx.search(tx.description):
                acct_id = self._key_to_id.get(key)
                if acct_id is not None and acct_id != tx.bank_account_id:
                    return Suggestion(acct_id, "keyword", rx.pattern[:40])
        return None

    def run(self, transactions: list[Transaction] | None = None, *, overwrite_auto: bool = False) -> dict[str, int]:
        """Categorize transactions. Manual categories are never changed.

        By default only uncategorized transactions are touched; with
        ``overwrite_auto`` previously auto-categorized ones are refreshed too
        (useful after adding rules).
        """
        if transactions is None:
            transactions = self.books.transactions()
        stats = Counter()
        with self.books.batch():
            for tx in transactions:
                if tx.category_source == "manual" or tx.reviewed:
                    continue
                if tx.category_id is not None and not overwrite_auto:
                    continue
                s = self.suggest(tx)
                if s is None:
                    stats["unmatched"] += 1
                    continue
                if s.account_id != tx.category_id or s.source != tx.category_source:
                    self.books.set_category(tx.id, s.account_id, source=s.source)
                stats[s.source] += 1
        return dict(stats)


def auto_categorize(books: Books, *, overwrite_auto: bool = False) -> dict[str, int]:
    return Categorizer(books).run(overwrite_auto=overwrite_auto)
