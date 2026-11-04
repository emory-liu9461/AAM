"""Put back spaces that OCR drops in all-caps bank descriptions.

OCR often reads "STRIPE TRANSFER ACME" as "STRIPETRANSFERACME". This splits
such runs into known words using a vocabulary of banking terms, common words
and the merchant names the categorizer knows. Parts it doesn't recognize
(company or person names) are kept intact, and a run is only split when most
of it is recognized, so normal words are never broken up.
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORDS = """
account accounts acct ach activity add added adjustment adj advance after agreement airline airlines alert all
allowance amount annual apple app application apply applied april arrival assessment atm august authorized auto
automatic available avg average back balance bank banking beginning bill billing bills bonus book branch business
buy by cable call card cards cash cashback cashier center central charge charges check checking checks chk city
claim clearing client clients closing club co collection com commercial company consulting contribution corp
corporate cost counter coverage credit credits cross customer daily date day debit debits december dep deposit
deposits description design detail digital direct discount dist distribution dividend draft draw due earned
electronic employee ending energy entry eom escrow estimated exchange expense express external fax fed federal fee
fees fidelity fin finance financial fixed for foreign from fuel fund funds gas general gift global group health
hold home hourly in inc incoming insurance int interest internal international internet inv invoice item items
january july june llc loan local lockbox ltd maintenance management march market marketing max may medical
member merchant method min minimum misc mobile monthly mortgage my net network new nonsufficient november number
october of office on online open opening order orig origin other out outgoing overdraft owner paid pay payable
payee payment payments payroll pd pending period personal phone pmt pos post posted posting preauthorized premium
prev previous principal processing product professional purchase purchases quarterly real rebate received receipt
recurring ref reference refund regular remote rent rental repair repayment return returned returns reversal
reversed review salary sale sales sav saving savings schedule school security send sent service services settlement
share shipping shop software statement stop store subscription summary supplies supply support sweep tax taxes
teller telephone the to total transaction transactions transfer transfers travel treasury trn trust type united
us usa utilities utility vendor via visa wage wages web weekly wire wired withdraw withdrawal withdrawals with
year yearly zelle venmo cashapp paypal stripe square shopify gusto adp paychex intuit quickbooks amazon amzn
google microsoft adobe apple github dropbox zoom slack notion canva figma uber lyft airbnb delta united southwest
marriott hilton expedia starbucks doordash grubhub chipotle mcdonalds shell chevron exxon mobil valero costco
walmart target staples depot lowes comcast xfinity verizon att tmobile spectrum electric water power edison
insurance geico progressive allstate fedex ups usps irs treasury dept state franchise license licenses rent lease
creative cloud workspace web services monthly annual plan pro premium basic consulting labs studio partners
dental clinic school district logistics architects cafe supply printing furniture
""".split()

_SHORT_OK = {"to", "of", "at", "by", "on", "in", "pd", "co", "us", "my"}


@lru_cache(maxsize=1)
def vocabulary() -> frozenset[str]:
    words = {w for w in _WORDS if len(w) >= 3 or w in _SHORT_OK}
    try:  # merchant names from the built-in categorization keywords
        from ..categorizer import BUILTIN_KEYWORDS

        for pattern, _key, _direction in BUILTIN_KEYWORDS:
            for w in re.findall(r"[a-z]{3,}", pattern):
                words.add(w)
    except Exception:  # noqa: BLE001
        pass
    return frozenset(words)


def split_run(run: str) -> str:
    """'STRIPETRANSFERACME' -> 'STRIPE TRANSFER ACME'; unchanged if it can't be split confidently."""
    s = run.lower()
    vocab = vocabulary()
    n = len(s)
    # best[i] = (cost, pieces) for s[:i]; known word = 1, unknown run = 2 + 0.25/char
    best: list[tuple[float, list[tuple[str, bool]]] | None] = [None] * (n + 1)
    best[0] = (0.0, [])
    for i in range(1, n + 1):
        for j in range(max(0, i - 20), i):
            if best[j] is None:
                continue
            piece = s[j:i]
            known = piece in vocab
            prev_cost, prev = best[j]
            if known:
                cost = prev_cost + 1.0
                pieces = prev + [(piece, True)]
            elif prev and not prev[-1][1]:
                continue  # extend unknown runs in one go only
            else:
                cost = prev_cost + 2.0 + 0.25 * len(piece)
                pieces = prev + [(piece, False)]
            if best[i] is None or cost < best[i][0]:
                best[i] = (cost, pieces)
    pieces = best[n][1] if best[n] else [(s, False)]
    known_chars = sum(len(p) for p, k in pieces if k)
    longest_known = max((len(p) for p, k in pieces if k), default=0)
    if len(pieces) < 2 or known_chars < 0.45 * n or longest_known < 4:
        return run
    out, pos = [], 0
    for p, _k in pieces:
        out.append(run[pos:pos + len(p)])
        pos += len(p)
    return " ".join(out)


_TOKEN_RE = re.compile(r"[A-Z&]{8,}")


def respace(line: str) -> str:
    """Re-insert spaces in long all-caps runs of an OCR line ('INV1043' digits are split off too)."""
    line = re.sub(r"(?<=[A-Z]{3})(?=\d)|(?<=\d)(?=[A-Z]{3})", " ", line)
    return _TOKEN_RE.sub(lambda m: split_run(m.group(0)), line)
