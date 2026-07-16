"""Honesty guardrail for screening questions — the product's core promise.

Only auto-answers a question when the profile PROVES the answer; everything else
routes to the human (NEEDS_HUMAN). Two layers, because the AI is never trusted to
police itself:

  Layer 1 — known-question resolver (`resolve`): a small table of universal Yes/No
    questions, each answered from ONE explicit answer-bank field. A missing/empty
    field, or a question not in the table, -> NEEDS_HUMAN. Deterministic, no AI.

  Layer 2 — receipt check (`verify_ai_answer`): when an AI proposes an answer it
    must cite the answer-bank field it used. Code verifies that field exists, that it
    is actually RELEVANT to the question (via the same _RULES table Layer 1 uses --
    a real field proves nothing if it doesn't apply to what was asked), AND that its
    value actually supports the Yes/No; if any of those fail, the AI is OVERRIDDEN to
    NEEDS_HUMAN regardless of confidence. Because Layer 1 already resolves every case
    a _RULES pattern covers, Layer 2 currently can't produce a live answer for a field
    outside that table (employer-specific fields always route to the human) -- a
    deliberate, safe default until relevance-mapping for those is properly designed.

Nothing here guesses. NEEDS_HUMAN is the safe, expected default. Same philosophy as
the resume fact-validator: the code demands a receipt, it doesn't trust the model.
"""
from __future__ import annotations

import re

NEEDS_HUMAN = "NEEDS_HUMAN"

# Human answer banks store Yes/No as strings, so bool("No") == True would flip a
# stored "No" into a confident "Yes". Normalize known false-strings before coercing,
# and refuse to guess on anything we can't read as a clean bool/known token.
_FALSE_TOKENS = {"no", "false", "0", "n", "f"}
_TRUE_TOKENS = {"yes", "true", "1", "y", "t"}


def _field_answer(value) -> str:
    """Map a backing field value to "Yes"/"No", or NEEDS_HUMAN if it isn't a clean
    truth value. bool() alone is wrong here: bool("No") is True. So real bools coerce
    directly, known yes/no tokens map explicitly, and anything ambiguous routes to the
    human rather than being guessed."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value in (None, ""):
        return NEEDS_HUMAN
    token = str(value).strip().lower()
    if token in _TRUE_TOKENS:
        return "Yes"
    if token in _FALSE_TOKENS:
        return "No"
    return NEEDS_HUMAN  # unreadable value -> human, never guess

# (regex over the lowercased question, answer-bank field that proves it).
# answer = "Yes" if bool(answers[field]) else "No".
# ONLY universal questions whose answer is a hard, reusable profile fact live here.
# Employer-specific ones (relatives at Citi, ex-KPMG, SGO/SCP referrals) are absent
# on purpose, so they correctly fall through to NEEDS_HUMAN.
# ponytail: a table, not an ontology — add a row when a universal question recurs
# across employers; anything unknown is already safe (routed to the human).
#
# Bounded/context-anchored on purpose -- found live, 2026-07-09, by an overnight
# adversarial audit: the old unanchored patterns (bare "sponsor", bare "\b18\b",
# bare "\bmilitary\b"/"\breserves\b", an unbounded "authoriz.*work") fired on
# topically UNRELATED real questions and auto-answered them with ZERO human
# review -- Layer 1 has no receipt check at all once a field is non-empty, so a
# false match here is worse than the Layer 2 gap fixed alongside this one.
# Confirmed live: "Do you authorize Citi to conduct a background check..."
# resolved via work_authorized; "...start work on 18 June..." resolved via
# is_over_18; "Are you a sponsor... of charitable events" resolved via
# needs_sponsorship; a felony/weapons question resolved via is_veteran purely
# because it mentioned "military-grade weapons". Every pattern below was
# re-verified against the project's own real Citi question set (this file's
# and test_screening.py's fixtures) to still match everything it must.
# ORDER MATTERS: a question that mentions BOTH sponsorship AND work authorization
# ("require sponsorship for work authorization" -- real PNC phrasing, found live
# 2026-07-10 wiring this into the filler) is a SPONSORSHIP question, so
# needs_sponsorship must be tried BEFORE work_authorized. Otherwise the "work authoriz"
# inside it matches the work-auth rule first and answers "Yes" ("I'm authorized") to
# "do you require sponsorship" -- a false, self-sabotaging claim that Ethan needs a
# visa. Both rules stay tightly anchored, so a pure work-auth question (which never
# says "sponsor") is unaffected by the order.
_RULES: list[tuple[str, str]] = [
    # "immigration" added 2026-07-14 (found live on SoFi): "require SoFi to commence
    # ('sponsor') an immigration case in order to employ you" is a sponsorship question.
    (r"\bsponsor\w*\b.{0,30}(?:employment|work authoriz|visa|work permit|immigration)"
     r"|(?:employment|work authoriz|visa|work permit|immigration).{0,30}\bsponsor\w*\b", "needs_sponsorship"),
    # Only the VERB question "are you authorized/eligible to work" -- NOT the noun
    # phrase "work authorization", which appears in status questions this fact cannot
    # answer ("is your work authorization through STEM OPT", "when does it expire").
    # Those must park for the human, never auto-"Yes". (Found live 2026-07-10 on PNC:
    # the old "work.{0,25}authoriz" alt answered "Yes" to the STEM OPT question.)
    # Rewordings that say "eligible/entitled/permitted to work" instead of "authorized"
    # (found live on SoFi 2026-07-14: "Are you currently eligible to work legally in the
    # United States of America?"). Every added alt is anchored to a legality/country
    # context ("...legally", "...lawfully", "...in the US/country") so it CANNOT fire on
    # availability questions like "eligible to work weekends/overtime/nights".
    (r"authoriz.{0,25}work|verification.{0,40}(identity|work)"
     r"|identity.{0,25}work"
     r"|(?:eligible|entitled|permitted|allowed)\s+to\s+(?:legally\s+|lawfully\s+)?work\s+(?:legally|lawfully)"
     r"|(?:eligible|entitled|permitted|allowed|authoriz\w+)\s+to\s+work\s+in\s+(?:the\s+)?"
     r"(?:u\.?s\.?\b|united states|this country|the country|any\s+country)"
     r"|legally\s+(?:eligible|authoriz\w+|entitled|permitted|allowed)\s+to\s+work", "work_authorized"),
    # AGE only — must not fire on "18 years of experience/service/months" (found live
    # 2026-07-12: "Do you have at least 18 years of experience?" resolved "Yes" off the
    # age fact). So "18" only counts when tied to an age word (of age / old / or older / +).
    (r"18\s*(?:\+|or older|or above|years?\s*of\s*age|years?\s*old)"
     r"|(?:age\s*(?:of\s*)?|older\s*than\s*)18\b"
     r"|eighteen\s*(?:or older|or above|years?\s*of\s*age|years?\s*old)", "is_over_18"),
    # The PERSON's own veteran/service status — not "veteran-owned business" (a supplier
    # question) or "military spouse / armed forces community" (a family question), both of
    # which is_veteran cannot answer (found live 2026-07-12). "armed forces"/"military"
    # only count when tied to serving/duty; a bare "military-grade weapons" stays human.
    (r"\bveteran\b(?!\s*[- ]?owned)"
     r"|(?:serv\w*|duty).{0,20}(?:armed forces|military|uniformed services)"
     r"|(?:armed forces|military|uniformed services).{0,25}(?:serv\w*|duty|veteran)"
     r"|active duty", "is_veteran"),
]
_COMPILED = [(re.compile(pat, re.IGNORECASE), field) for pat, field in _RULES]


def resolve(question: str, answers: dict) -> str:
    """Yes/No if a KNOWN universal question is proven by the answer bank, else NEEDS_HUMAN.

    A known question whose backing field is missing or empty is flagged, not guessed
    — that is the enforcement: an empty required field always means a human answers.
    """
    q = " ".join(str(question).lower().split())
    for rx, field in _COMPILED:
        if rx.search(q):
            if field not in answers or answers[field] in (None, ""):
                return NEEDS_HUMAN
            return _field_answer(answers[field])
    return NEEDS_HUMAN  # unknown question -> human, never guess


def verify_ai_answer(question: str, answer: str, cited_field: str, answers: dict) -> str:
    """Layer 2: keep an AI's Yes/No only if it cited a real answer-bank field, that
    field is actually RELEVANT to this question, AND its VALUE supports the answer;
    otherwise override to NEEDS_HUMAN.

    A present-but-unchecked citation is not a receipt: a fabricating AI could return a
    confident "Yes"/"No" and cite ANY populated field, including one that has nothing
    to do with what was asked (e.g. citing is_veteran=False to answer a felony/
    background-check question -- found live, 2026-07-09, by an overnight adversarial
    audit; the old code only checked the field's VALUE, never whether it applied to
    the question at all). So the code first confirms the field is relevant, THEN
    re-derives the answer from it and keeps the AI's answer only when the two agree.

    Relevance is checked against the same _RULES table resolve() uses -- deliberately
    conservative: only a field one of _RULES' own patterns maps to THIS question can
    ever be confirmed here. Since resolve() (Layer 1) already answers every case where
    a _RULES pattern matches AND the field is populated, this means Layer 2 currently
    can never produce a live answer for a field outside that small universal table --
    any employer-specific field (veteran_status, how_did_you_hear, eeo, etc. -- real
    fields in workday_answers.json) is NEVER confirmable here and always routes to the
    human. That's a deliberate, safe default, not an oversight: extending AI+receipt
    coverage to employer-specific fields needs a real relevance-mapping design (what
    makes a field topically relevant to a question), which is a product decision, not
    something to guess at blind. Until that's designed, "always ask the human" for
    anything outside the universal table is the correct, safe behavior.
    """
    ans = str(answer).strip()
    if ans.upper() == NEEDS_HUMAN:
        return NEEDS_HUMAN
    field = str(cited_field or "").strip()
    if not field or field not in answers:
        return NEEDS_HUMAN
    q = " ".join(str(question).lower().split())
    if not any(f == field and rx.search(q) for rx, f in _COMPILED):
        return NEEDS_HUMAN  # real field, but not one THIS question's own rule uses
    proven = _field_answer(answers[field])
    if proven == NEEDS_HUMAN:
        return NEEDS_HUMAN
    return ans if ans.lower() == proven.lower() else NEEDS_HUMAN


# ===========================================================================
# Grounded facts beyond the simple Yes/No universal table. Same honesty contract:
# answer ONLY from a fact you supplied, else NEEDS_HUMAN. Added 2026-07-14 from
# Ethan's review of 33 parked questions -- values he already gave (education, home
# country), "ever worked at X" (from a FULL job history, since a resume shows only
# some jobs), "currently a <company> employee", and conflict-of-interest "related
# party" questions. Nothing here guesses; an unproven question parks for the human.
# ===========================================================================

# VALUE questions (answer is a value you supplied, not Yes/No): regex -> answer-bank
# field(s) to read, first non-empty wins.
_VALUE_RULES: list[tuple[str, tuple[str, ...]]] = [
    (r"highest\s+(level\s+of\s+)?(education|degree|schooling)"
     r"|(level|type)\s+of\s+education\s+(completed|attained|achieved|obtained|received)"
     r"|what\s+is\s+your\s+highest\s+(education|degree|level)", ("highest_education",)),
    # Home/residential address country -- NOT "authorized to work in the country" (that's
    # the work-auth rule); anchored to home/residence/mailing so the two never collide.
    (r"(home|residential|residence|mailing|permanent|current)\s+(address\s+)?country"
     r"|country\s+of\s+residence", ("home_country", "country")),
    # State / province of residence -- from your address on file.
    (r"\bstate\b.{0,30}(?:reside|residence|\blive\b|located)"
     r"|(?:reside|residence|located).{0,30}\bstate\b"
     r"|state\s+or\s+(?:canadian\s+)?province", ("home_state", "state")),
]
_VALUE_COMPILED = [(re.compile(p, re.I), f) for p, f in _VALUE_RULES]


def value_answer(question: str, answers: dict) -> str:
    """A grounded VALUE (highest education, home-address country) from a field you supplied,
    or NEEDS_HUMAN. A recognized question with no fact on file still parks -- never guessed."""
    q = " ".join(str(question).lower().split())
    for rx, fields in _VALUE_COMPILED:
        if rx.search(q):
            for f in fields:
                v = answers.get(f)
                if v not in (None, ""):
                    return str(v).strip()
            return NEEDS_HUMAN
    return NEEDS_HUMAN


_CO_NOISE = re.compile(
    r"\b(llp|llc|inc|incorporated|corp|corporation|co|company|companies|ltd|limited"
    r"|group|holdings|plc|lp|na|the)\b\.?", re.I)


def _norm_co(s: str) -> str:
    """Normalize a company name for matching: drop legal suffixes/noise, keep core words."""
    s = _CO_NOISE.sub(" ", str(s).lower())
    return " ".join(re.findall(r"[a-z0-9]+", s))


def _named_in(names: list, q: str) -> bool:
    """True if any company `names` appears as a whole normalized phrase in question `q`."""
    padded = f" {_norm_co(q)} "
    for n in names:
        nn = _norm_co(n)
        if nn and f" {nn} " in padded:
            return True
    return False


def _employers(answers: dict) -> list:
    e = answers.get("employers_all")
    return [str(x) for x in e if str(x).strip()] if isinstance(e, list) else []


def ever_worked_answer(question: str, answers: dict) -> str:
    """'Have you ever worked at / been employed by / consulted for X?' Grounded from your
    affirmed-COMPLETE employment history: a listed employer named in the question -> Yes;
    none named AND the history is marked complete -> No; else park. A resume shows only some
    jobs, so it is NOT used here -- only the full `employers_all` list you affirm."""
    q = " ".join(str(question).lower().split())
    # A restrictive-agreement question ("subject to any agreement with a former employer,
    # such as a non-compete/non-solicitation") is NOT an employment-history question -- it
    # asks about a CONTRACT, and only Ethan knows it. Park it. (Found 2026-07-14 reviewing
    # real parked questions: it was answering "No" off "former employer".)
    if re.search(r"non-?compet|non-?solicit|restrictive\s+covenant|garden\s+leave", q):
        return NEEDS_HUMAN
    # Require PAST / employment framing as the MAIN question -- deliberately NOT a bare
    # "work at/for" (fires on "willing to work at our NYC office"), NOT a bare "employed by"
    # (fires on a conditional "...FINRA licenses if employed by SoFi"), and "former EMPLOYEE"
    # not "former EMPLOY" (so "a former employER" in a non-compete can't trigger it).
    if not re.search(r"\b(ever|previously|formerly|in\s+the\s+past)\b.{0,40}(work|employ)"
                     r"|\bbeen\s+employed\b"
                     r"|\bhave\s+you\b[^?]{0,40}\bwork\w*\s+(at|for)\b"
                     r"|(current\s+or\s+former|former)\s+employee\b"
                     r"|consult\w*\s+for\b", q):
        return NEEDS_HUMAN
    names = _employers(answers)
    if _named_in(names, q):
        return "Yes"
    if answers.get("employment_history_complete") is True and names:
        return "No"
    return NEEDS_HUMAN


def current_employer_answer(question: str, answers: dict) -> str:
    """'Are you currently an employee of <this company>?' Grounded only as a safe 'No': if the
    company named isn't anywhere in your COMPLETE employment history you certainly don't work
    there now -> No. If it IS in your history (past vs present is ambiguous from a dropdown),
    park. NEVER auto-'Yes'. Excludes work-authorization phrasings ('currently authorized to
    work') so it can't collide with the work-auth rule."""
    q = " ".join(str(question).lower().split())
    if re.search(r"authoriz|eligible|legally|lawfully|\bvisa\b|sponsor", q):
        return NEEDS_HUMAN
    # A question about a RELATIVE's employment ("relatives currently working for X") is NOT
    # about the applicant's own current employer -- let related_party handle it. (Found
    # 2026-07-14: Citi's "relatives working for Citi" was resolving here for the wrong reason.)
    if re.search(r"\b(relative|relatives|family|spouse|household|covered\s+relationship"
                 r"|family\s+member|immediate\s+family)\b", q):
        return NEEDS_HUMAN
    # "currently" must sit RIGHT NEXT TO the employment word -- a loose window let a
    # conditional "currently hold ... licenses if employed by SoFi" slip through and
    # answer "No" (2026-07-14 self-check). Require adjacency to "employed"/"a <co>
    # employee"/"work for".
    if not re.search(r"(current(ly)?|presently|now)\s+(be\s+|being\s+)?employ"
                     r"|current(ly)?\s+(a|an)\s+[^?]{0,30}\bemployee\b"
                     r"|\bare\s+you\s+(a|an)\s+(current|present)[^?]{0,30}\bemployee\b"
                     r"|currently\s+work(ing)?\s+(for|at)\b", q):
        return NEEDS_HUMAN
    if answers.get("employment_history_complete") is True and not _named_in(_employers(answers), q):
        return "No"
    return NEEDS_HUMAN


# Related-party / conflict-of-interest phrasings. Broadened 2026-07-14 per Ethan: an
# "insider" is an officer/director/10%+ owner of a PUBLICLY TRADED company, and the same
# question is asked about YOU or a relative in many wordings. `insiders` (self + family)
# is one list; a family OR a self-insider question is answered "No" only when that list is
# empty AND you've affirmed it's complete.
_FAMILY = (r"\b(relatives?|family|spouse|domestic\s+partner|household|immediate\s+family"
           r"|covered\s+relationship|family\s+member|close\s+personal\s+relationship"
           r"|related\s+to\s+(?:any|an|a\s+current|someone))")
_ROLE_OR_EMPLOY = (r"(employ|work|insider|officer|director|senior\s+management|board"
                   r"|executive|shareholder|owner|policy[\s-]?making|affiliated|control)")
# Ownership thresholds vary by employer -- 5% (SEC 13D/G "significant holder", the more
# common one) and 10% (Section 16 statutory insider) both appear -- so match ANY percent,
# not a hardcoded 10%. Confirmed against real questions 2026-07-14 (Robinhood asks ">5% of
# the outstanding shares of a publicly-traded company"; Citi references Section 16 Officers).
_INSIDER_ROLE = (r"(insider|officer|director|board\s+member|executive|\d{1,3}\s*%"
                 r"|(?:five|ten|twenty|\d{1,3})\s+percent|outstanding\s+shares"
                 r"|beneficial\s+owner|control(?:ling)?\s+(?:person|shareholder)"
                 r"|policy[\s-]?making|restricted\s+person|affiliated\s+person|section\s+16"
                 r"|reporting\s+person|principal\s+shareholder|significant\s+(?:share|stock)holder)")
_PUBLIC_CO = (r"(public(?:ly)?[\s-]*(?:traded|listed|held)?\s*compan|publicly\s+traded"
              r"|listed\s+(?:company|entity|issuer)|public\s+company|reporting\s+(?:company|issuer)"
              r"|\bissuer\b|sec\s+reporting|exchange[\s-]?listed)")
_GOV_OFFICIAL = (r"government\s+official|public\s+official|politically\s+exposed|\bpep\b"
                 r"|senior\s+(?:foreign\s+)?(?:government|political)\s+(?:official|figure)"
                 r"|hold\w*\s+(?:public|elected)\s+office|elected\s+official|government\s+employee\b")


def related_party_answer(question: str, answers: dict) -> str:
    """Conflict-of-interest 'related party' questions -- you or a relative being an insider
    (officer/director/10%+ owner) of a PUBLIC company, or you/family being a government
    official. Answered only when your affirmed-COMPLETE list PROVES there's nothing to
    disclose: the matching list empty AND related_party_complete -> No. Any potential match
    (a non-empty list) PARKS so you add the specifics -- a bare dropdown 'Yes' can't carry
    the required detail (name, company, and the ownership amount these disclosures need).
    Not proven -> park. Recognizes many wordings of the same question."""
    q = " ".join(str(question).lower().split())
    complete = answers.get("related_party_complete") is True

    # Compound conflict questions that ALSO ask about dimensions we can't ground (outside
    # business activities, IP ownership, private-company investments) must PARK -- a blanket
    # "No" would wrongly deny those too. (Found reviewing Robinhood's multi-part question,
    # 2026-07-14: a/relatives b/outside business c/>5% public d/private competitor e/IP.)
    if re.search(r"outside\s+business|business\s+activit|intellectual\s+property|\bpatent"
                 r"|trademark|copyright|\binvention\b|private\s+company|privately[\s-]held", q):
        return NEEDS_HUMAN

    # A family-relationship question ("relatives working for / who are insiders of the
    # company") OR a self insider-of-a-public-company question. The family form also covers
    # relatives who simply WORK at the hiring company (not just public-company insiders), so
    # it must see BOTH the insider list AND the employer-relatives list empty to answer "No".
    family_q = bool(re.search(_FAMILY, q) and re.search(_ROLE_OR_EMPLOY, q))
    insider_q = bool((re.search(_INSIDER_ROLE, q) and re.search(_PUBLIC_CO, q))
                     or re.search(r"insider\s+of\b", q))
    if family_q or insider_q:
        disclosures = list(answers.get("insiders") or [])
        if family_q:
            disclosures += list(answers.get("employer_relatives") or [])
        if complete and not disclosures:
            return "No"
        return NEEDS_HUMAN

    # GOVERNMENT / public official (you or family) -- a PEP disclosure.
    if re.search(_GOV_OFFICIAL, q):
        if complete and not answers.get("government_officials"):
            return "No"
        return NEEDS_HUMAN

    return NEEDS_HUMAN


# Standard finance-application "professional conflict" disclosures -- often bundled into one
# compound question (Robinhood's a-e). Outside business activities, intellectual property to
# retain, or private/competitor investments. (FINRA licenses are handled by finra_answer,
# NOT here -- a license is a "Yes/which-ones", never a blanket "No".)
_DISCLOSURE = (r"outside\s+business|business\s+activit|intellectual\s+property|\bpatent\b|trademark"
               r"|copyright|\binvention\b|private\s+company|privately[\s-]held")


def disclosure_answer(question: str, answers: dict) -> str:
    """Professional-conflict disclosures (outside business, IP, private/competitor
    investments). Answered 'No' only when you've affirmed you have NOTHING to disclose --
    `professional_disclosures` empty AND every conflict list (insiders, employer_relatives,
    government_officials) empty AND related_party_complete. Anything on file -> park, so a
    real disclosure is never silently denied."""
    q = " ".join(str(question).lower().split())
    if not re.search(_DISCLOSURE, q):
        return NEEDS_HUMAN
    lists = ("professional_disclosures", "insiders", "employer_relatives", "government_officials")
    if answers.get("related_party_complete") is True and not any(answers.get(k) for k in lists):
        return "No"
    return NEEDS_HUMAN


def finra_answer(question: str, answers: dict) -> str:
    """The FINRA/securities-license YES/NO question ("do you hold, or intend to hold, any
    FINRA licenses?"). 'Yes' when you've listed any license in `finra_licenses`; otherwise
    PARK -- never a false 'No', because holding a license and denying it is a real problem.
    The separate "WHICH license(s)?" checkbox list is filled by the browser filler, so this
    defers on what/which phrasings."""
    q = " ".join(str(question).lower().split())
    if not re.search(r"\bfinra\b|registered\s+representative|securities\s+(?:license|registration)"
                     r"|series\s*\d+", q):
        return NEEDS_HUMAN
    if re.search(r"\b(what|which|list)\b", q):
        return NEEDS_HUMAN   # the "which licenses" list -> checkbox filler, not a Yes/No
    # "Yes" only when a genuine FINRA credential (a Series exam or the SIE) is declared -- a
    # non-FINRA license (e.g. a state insurance producer) must NOT answer a FINRA question Yes.
    has_finra = any(re.search(r"series|(?<![a-z0-9])s\d|\bsie\b", str(lic).lower())
                    for lic in (answers.get("finra_licenses") or []))
    return "Yes" if has_finra else NEEDS_HUMAN
