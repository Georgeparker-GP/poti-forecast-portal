"""
resilient_fetch.py
------------------
Fault-tolerant weather aggregation layer.

მთავარი პრინციპი: ერთი წყაროს ჩავარდნა არ კლავს პორტალს.
თითოეული წყარო იზოლირებულია. კონსენსუსი ითვლება იმ წყაროებით,
რომლებმაც წარმატებით დააბრუნეს მონაცემი.

ინტეგრაცია შენს fetch.py-ში:
    from resilient_fetch import gather_sources, build_consensus, SourceResult

    results = gather_sources({
        "open-meteo": fetch_open_meteo,   # <- შენი არსებული ფუნქციები
        "yr.no":      fetch_yr,
        "windy":      fetch_windy,
        "owm":        fetch_owm,
        "stormglass": fetch_stormglass,
    })
    consensus = build_consensus(results, min_sources=1)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("resilient_fetch")


# ---------------------------------------------------------------------------
# 1. მდგრადი HTTP session — მოკლე, შეზღუდული retry + timeout
# ---------------------------------------------------------------------------
def make_session(
    total_retries: int = 2,          # 2 ცდა, არა უსასრულო
    backoff_factor: float = 0.8,     # 0.8s, 1.6s ... — ზრდადი დაყოვნება
    status_forcelist=(429, 500, 502, 503, 504),
) -> requests.Session:
    """აბრუნებს Session-ს, რომელიც transient შეცდომებზე ცოტას ცდის და ჩერდება."""
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# 2. ერთი წყაროს შედეგი — ან data, ან შეცდომა. არასდროს raise ზევით.
# ---------------------------------------------------------------------------
@dataclass
class SourceResult:
    name: str
    ok: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    elapsed_s: float = 0.0


def fetch_source(
    name: str,
    fn: Callable[[], Any],
    timeout_s: float = 20.0,
) -> SourceResult:
    """
    ერთი წყაროს იზოლირებული გამოძახება.
    ნებისმიერი exception იჭერება — მთავარ ციკლში ვერასდროს „ამოხტება".
    """
    start = time.monotonic()
    try:
        data = fn()
        elapsed = time.monotonic() - start
        if data is None:
            log.warning("[%s] ცარიელი პასუხი (%.1fs) — გამოტოვებულია", name, elapsed)
            return SourceResult(name, ok=False, error="empty response", elapsed_s=elapsed)
        log.info("[%s] OK (%.1fs)", name, elapsed)
        return SourceResult(name, ok=True, data=data, elapsed_s=elapsed)
    except requests.exceptions.Timeout:
        elapsed = time.monotonic() - start
        log.warning("[%s] Timeout (%.1fs) — გამოტოვებულია, პორტალი აგრძელებს", name, elapsed)
        return SourceResult(name, ok=False, error="timeout", elapsed_s=elapsed)
    except requests.exceptions.ConnectionError as e:
        elapsed = time.monotonic() - start
        log.warning("[%s] Connection error — გამოტოვებულია (%s)", name, e)
        return SourceResult(name, ok=False, error=f"connection: {e}", elapsed_s=elapsed)
    except Exception as e:  # noqa: BLE001 — ბოლო კედელი: არაფერი გავიდეს ზევით
        elapsed = time.monotonic() - start
        log.warning("[%s] მოულოდნელი შეცდომა — გამოტოვებულია (%s)", name, e)
        return SourceResult(name, ok=False, error=str(e), elapsed_s=elapsed)


# ---------------------------------------------------------------------------
# 3. ყველა წყაროს შეგროვება — ცალ-ცალკე, ჩავარდნის იზოლაციით
# ---------------------------------------------------------------------------
def gather_sources(
    sources: Dict[str, Callable[[], Any]],
) -> List[SourceResult]:
    """
    sources: {"open-meteo": fn, "yr.no": fn, ...}
    აბრუნებს ყველა შედეგს — წარმატებულსაც და ჩავარდნილსაც.
    """
    results: List[SourceResult] = []
    for name, fn in sources.items():
        results.append(fetch_source(name, fn))

    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    log.info("წყაროები: %d/%d იმუშავა | წარმატებული: %s | ჩავარდნილი: %s",
             len(ok), len(results),
             ", ".join(r.name for r in ok) or "—",
             ", ".join(r.name for r in bad) or "—")
    return results


# ---------------------------------------------------------------------------
# 4. კონსენსუსი — მხოლოდ გადარჩენილი წყაროებით
# ---------------------------------------------------------------------------
@dataclass
class Consensus:
    ok: bool
    used_sources: List[str] = field(default_factory=list)
    failed_sources: List[str] = field(default_factory=list)
    values: List[Any] = field(default_factory=list)
    reason: Optional[str] = None


def build_consensus(
    results: List[SourceResult],
    min_sources: int = 1,
) -> Consensus:
    """
    კონსენსუსი გამოითვლება იმ წყაროებით, რომლებმაც იმუშავა.
    min_sources — მინიმალური რაოდენობა, რომ შედეგი ვალიდურად ჩაითვალოს.
    თუ ამაზე ნაკლებმა იმუშავა → ok=False (მხოლოდ მაშინ ჩავარდეს პორტალი).
    """
    good = [r for r in results if r.ok]
    used = [r.name for r in good]
    failed = [r.name for r in results if not r.ok]

    if len(good) < min_sources:
        return Consensus(
            ok=False,
            used_sources=used,
            failed_sources=failed,
            reason=f"მუშა წყარო {len(good)} < მინიმუმი {min_sources}",
        )

    return Consensus(
        ok=True,
        used_sources=used,
        failed_sources=failed,
        values=[r.data for r in good],
    )
