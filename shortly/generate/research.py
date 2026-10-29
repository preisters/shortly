"""주제 관련 웹 조사 — 대본 생성용 팩트/각도 보강.

기본 백엔드: ddgs / duckduckgo-search (API 키 불필요).
미설치·실패 시 빈 결과로 폴백해 파이프라인은 계속 진행합니다.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from ..llm.base import (
    LLMClient,
    LLMError,
    LLMQuotaExceeded,
    LLMResponseParseError,
)
from ..utils import log

try:
    from ddgs import DDGS  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:  # pragma: no cover
        DDGS = None  # type: ignore

_FLUFF = re.compile(
    r"(썰|이야기|얘기|관련|대해서|대해|알려줘|만들어|해줘|부탁|대본|"
    r"영상|쇼츠|유튜브|흥미|자극|바이럴)"
)
_STOP = {
    "때문에", "때문에는", "시장을", "시장", "포기한", "포기", "일본", "독도",
    "한국", "대한", "있는", "없는", "하는", "된", "하는", "이거", "그거",
    "뭔가", "어떤", "요즘", "진짜", "사실",
}

_LOW_QUALITY_HOSTS = (
    "youtube.com",
    "youtu.be",
    "namu.wiki",
    "pinterest.",
    "tistory.com",
    "blog.naver.com",
    "m.blog.naver.com",
    "bobaedream.co.kr",
    "etoland.co.kr",
    "instiz.net",
    "dcinside.com",
    "fmkorea.com",
    "theqoo.net",
    "pornhub.com",
    "xvideos.com",
    "xnxx.com",
    "redtube.com",
    "spankbang.com",
)
_EDITORIAL_HOSTS = (
    "yna.co.kr",
    "newsis.com",
    "chosun.com",
    "joongang.co.kr",
    "donga.com",
    "hani.co.kr",
    "khan.co.kr",
    "hankookilbo.com",
    "kmib.co.kr",
    "seoul.co.kr",
    "mk.co.kr",
    "hankyung.com",
    "sedaily.com",
    "edaily.co.kr",
    "asiae.co.kr",
    "etnews.com",
    "zdnet.co.kr",
    "koreadaily.com",
)
_SPAM_MARKERS = re.compile(
    r"(카지노|토토|바카라|성인\s*(?:방송|만남|용품)|porn|xxx|escort|"
    r"야동|성인물|무료\s*성인|19금|자유게시판|유머\s*게시판|사랑방)",
    re.I,
)


def _clean_query_seed(text: str) -> str:
    """위키/광고/구어 잡음을 줄인 검색 시드 문자열."""
    t = (text or "").strip()
    t = re.sub(r"ADVERTISEMENT|\[[^\]]+\]|#{1,6}\s*", " ", t)
    t = _FLUFF.sub(" ", t)
    t = re.sub(r"\s+", " ", t)
    return t[:220].strip()


def parse_research_focus(value: Optional[str]) -> List[str]:
    """사용자가 지정한 조사 쟁점을 검색 가능한 짧은 항목으로 정규화한다."""
    if not value:
        return []
    items: List[str] = []
    for raw in re.split(r"[\r\n]+", str(value)):
        item = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
        item = re.sub(r"\s+", " ", item).strip()
        if item and item not in items:
            items.append(item)
    return items


def parse_required_content(value: Optional[str]) -> List[str]:
    """Normalize mandatory script beats using the same one-item-per-line contract."""

    items: List[str] = []
    for raw in re.split(r"[\r\n]+", str(value or "")):
        item = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
        item = re.sub(r"\s+", " ", item).strip()
        if item and item not in items:
            items.append(item)
    return items


def build_queries(
    topic: str,
    max_queries: int = 4,
    subject_anchors: Optional[List[str]] = None,
    research_focus: Optional[str] = None,
    required_content: Optional[str] = None,
) -> List[str]:
    """주제에서 사실 확인용 검색 쿼리를 만든다.

    '썰' 같은 구어를 제거하고, 고유명사+사건 키워드 조합을 우선한다.
    """
    seed = _clean_query_seed(topic)
    if not seed:
        return []
    # 수정 요청은 주체·기존 대본 뒤에 붙기 때문에 220자 검색 시드 밖으로
    # 밀릴 수 있다. 검색어 자체는 짧게 유지하되, 사용자가 요구한 결말 방향은
    # 전체 입력에서 읽어 후속 성과 쿼리를 빠뜨리지 않는다.
    intent_text = re.sub(r"\s+", " ", topic or "").strip()
    leading_line = next(
        (line.strip() for line in str(topic or "").splitlines() if line.strip()),
        "",
    )
    first = re.split(r"[.\n!?]", seed)[0].strip() or seed
    first = first[:90]

    primary_subject = next(
        (
            re.sub(r"\s+", " ", str(value or "")).strip()
            for value in subject_anchors or []
            if str(value or "").strip()
        ),
        "",
    )
    if not primary_subject:
        origin_match = re.search(
            r"^\s*([A-Za-z가-힣0-9· ]{2,36}?)의?\s*"
            r"(?:탄생|시작|유래|기원)\s*(?:비화|배경|과정|이야기)?(?:[.。!?]|$)",
            intent_text,
        )
        if origin_match:
            primary_subject = re.sub(
                r"\s+", " ", origin_match.group(1)
            ).strip()
    origin_story = bool(
        re.search(r"(탄생\s*(?:비화|배경|과정|이야기)?|유래|기원|출발점|개척)", leading_line)
        or any(
            keyword in intent_text
            for keyword in (
                "탄생 비화", "탄생 과정", "가난한 농민", "산양", "기술을 배",
            )
        )
    )
    imsil_cheese_story = "임실" in intent_text and "치즈" in intent_text
    if primary_subject:
        if origin_story:
            event_terms = (
                ["탄생", "지정환 신부"]
                if imsil_cheese_story
                else ["탄생", "역사"]
            )
        else:
            event_terms = [
                keyword
                for keyword in (
                    "독도", "일본", "수출", "포기", "쌀과자", "주문", "품절",
                    "대만", "홍콩", "미국", "러시아", "판매량", "현지", "공장",
                    "법인", "차", "케이크", "베리", "잼",
                )
                if keyword in intent_text
            ]
        first = f'"{primary_subject}" {" ".join(event_terms[:5])}'.strip()
    focus_items = parse_research_focus(research_focus)
    required_items = parse_required_content(required_content)
    # All explicit user items must survive; the budget limits inferred extras,
    # never silently drops the last research questions (8 + 5 + seed = 14).
    query_limit = max(1 + len(focus_items) + len(required_items),
                      min(12, max(max_queries, 2 + len(focus_items) + len(required_items))))
    queries: List[str] = [first]

    # 별도 조사 항목은 일반 주제에서 추론한 검색어로 대체하지 않는다. 각 줄을
    # 실제 독립 검색으로 실행해 사용자가 확인하려는 쟁점이 누락되지 않게 한다.
    for item in [*required_items, *focus_items]:
        query = _clean_query_seed(item)[:140].strip()
        if query and query not in queries:
            queries.append(query)

    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,}|[가-힣]{2,}", first)
              if t not in _STOP]
    brand = primary_subject or (tokens[0] if tokens else "")
    if not primary_subject and "성경김" in intent_text:
        brand = "성경김"
    if len(brand) >= 3:
        brand = re.sub(r"(?:은|는|이|가|을|를|의|과|와)$", "", brand)
    core = " ".join(tokens[:4]).strip()

    extras: List[str] = []
    if brand:
        entity = f'"{brand}"'
        if focus_items or required_items:
            official_terms = [
                keyword
                for keyword in ("러시아", "판매량", "현지", "공장", "법인")
                if keyword in intent_text
            ]
            extras.append(
                f"{entity} {' '.join(official_terms[:4])} 공식 보도자료".strip()
            )
        if origin_story:
            detail_terms = [
                keyword
                for keyword in ("산양", "농민", "유럽", "기술", "비누틀", "동굴", "보관소")
                if keyword in intent_text
            ]
            if imsil_cheese_story:
                extras.extend([
                    f"{entity} 지정환 신부 산양 농민",
                    f"{entity} 유럽 치즈 기술 비눗갑 비누틀 실패",
                    f"{entity} 동굴 숙성고 최초 치즈공장",
                ])
            else:
                extras.extend([
                    f"{entity} 탄생 유래 창업자",
                    f"{entity} 초기 실패 기술 개발",
                    f"{entity} 역사 공식 자료",
                ])
            if detail_terms:
                extras.insert(0, f'{entity} {" ".join(detail_terms[:5])}')
        if any(k in intent_text for k in (
            "해외", "미국", "성공", "실적", "잘 팔", "잘나가", "잘 나가",
            "대박", "품절", "없어서 못", "해피엔딩", "수출",
        )):
            extras.extend([
                f"{entity} 해외 수출 실적 매출 비중 미국 유통",
                f"{entity} 미국 수출 판매",
            ])
        if any(k in intent_text for k in ("독도", "일본", "수출")):
            extras.extend([
                # 시점 혼동 방지: 발생 연도/재조명 구분용 (우선)
                f"{entity} 일본 수출 포기 언제",
                f"{entity} 독도 일본 수출",
            ])
        if "유행" in intent_text or "트렌드" in intent_text:
            extras.append(f"{entity} 유행")
        if "가격" in intent_text or "얼마" in intent_text:
            extras.append(f"{entity} 가격")
    if core and core != first:
        extras.append(core)
    if brand == "성경김":
        extras.insert(0, '"성경식품" 독도 일본 수출')

    seen = {first}
    for q in extras:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)
        if len(queries) >= query_limit:
            break
    return queries[:query_limit]


_RELATIVE_TIME = re.compile(
    r"(올해|작년|재작년|최근|요즘|이달|지난달|며칠\s*전|몇\s*년\s*전|수년\s*전)"
)
_ABSOLUTE_YEAR = re.compile(r"(19|20)\d{2}")
_MEASURED_STAT = re.compile(
    r"(?:\d[\d,.]*\s*(?:%|퍼센트|원|달러|명|개|건|배|억|만|조))|"
    r"(?:(?:수출액|매출|점유율|증가율|감소율).{0,24}\d)"
)
_PROJECTION = re.compile(r"(전망|예상|추정|목표|계획|예정)")


def _sanitize_temporal_facts(hard_facts: List[str], avoid: List[str]
                             ) -> Tuple[List[str], List[str]]:
    """상대 시점만 있는 hard_fact 는 제거하고 avoid 로 내린다.

    '올해 초'를 부분 삭제하면 '초 ...' 같은 깨진 문장이 남아 더 위험하다.
    연도 불명 시점은 대본에서 생략하도록 avoid 로만 안내한다.
    """
    kept: List[str] = []
    extra_avoid = list(avoid)
    for hf in hard_facts:
        if _RELATIVE_TIME.search(hf) and not _ABSOLUTE_YEAR.search(hf):
            extra_avoid.append(
                f"상대 시점만 있는 표현은 대본에 쓰지 말 것(연도 불명·주체 혼동 위험): "
                f"{hf[:140]}"
            )
            continue
        # 절대연도가 있어도 '올해'와 섞이면 상대 표현은 쓰지 말라고 경고
        if _RELATIVE_TIME.search(hf):
            extra_avoid.append(
                f"절대연도와 상대시점('올해' 등)이 섞인 문장 — "
                f"대본에는 절대연도만 쓰거나 시점을 생략: {hf[:120]}"
            )
            cleaned = _RELATIVE_TIME.sub("", hf)
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.-")
            if cleaned and len(cleaned) >= 12:
                kept.append(cleaned)
            continue
        kept.append(hf)
    extra_avoid.append(
        "기사 속 '올해/최근'은 보도 시점일 수 있음 — "
        "사건 발생 연도로 단정하지 말 것. 연도 불명이면 시점 표현 생략"
    )
    extra_avoid.append(
        "한 기사에 여러 회사가 나오면(예: 올바름 vs 성경식품) "
        "한쪽의 시점·사건을 다른 쪽에 붙이지 말 것"
    )
    return kept, list(dict.fromkeys(extra_avoid))


def _strip_unscoped_numeric_claims(
    values: List[str],
    avoid: List[str],
) -> Tuple[List[str], List[str]]:
    """Keep measured claims exclusively in validated quantified_facts."""

    kept: List[str] = []
    extra_avoid = list(avoid)
    for value in values:
        if _MEASURED_STAT.search(value):
            extra_avoid.append(
                "구조화된 수치 팩트가 아닌 일반 요약의 수치는 대본에 쓰지 말 것: "
                f"{value[:140]}"
            )
            continue
        kept.append(value)
    return kept, list(dict.fromkeys(extra_avoid))


def _numeric_tokens(value: str) -> List[str]:
    return [
        token.replace(",", "").lstrip("0") or "0"
        for token in re.findall(r"\d[\d,.]*", value or "")
    ]


def _quantified_fact_is_supported(
    fact: Dict[str, str],
    results: List[Dict],
) -> bool:
    """Require the value and period to be visible in the cited snippet."""

    cited = _canonical_source_url(fact.get("source_url", ""))
    source = next(
        (
            row
            for row in results
            if _canonical_source_url(str(row.get("url") or "")) == cited
        ),
        None,
    )
    if not source or str(source.get("source_tier") or "general") == "low":
        return False
    blob = (
        f"{source.get('title', '')} {source.get('snippet', '')} "
        f"{source.get('page_excerpt', '')}"
    ).replace(",", "")
    blob_tokens = set(_numeric_tokens(blob))
    required = _numeric_tokens(
        " ".join(
            [
                fact.get("value", ""),
                fact.get("period", ""),
                fact.get("comparison_value", ""),
                fact.get("comparison_period", ""),
                fact.get("change", ""),
            ]
        )
    )
    return bool(_numeric_tokens(fact.get("value", ""))) and all(
        token in blob_tokens for token in required
    )


def _canonical_source_url(value: str) -> str:
    return _normalize_search_url(value).split("#", 1)[0].split("?", 1)[0].rstrip("/")


def _normalize_search_url(value: str) -> str:
    """Return a direct, stable URL when DDG HTML yielded a redirect link."""

    raw = (value or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlparse(raw)
        if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                raw = unquote(target)
    except ValueError:
        return raw
    return raw


def _host_for(value: str) -> str:
    try:
        return (urlparse(_normalize_search_url(value)).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(host: str, suffix: str) -> bool:
    suffix = suffix.lower()
    if suffix.endswith("."):
        return suffix in host
    return host == suffix or host.endswith("." + suffix)


_RELEVANCE_STOP_TERMS = {
    "공식", "자료", "근거", "관련", "실제", "확인", "항목", "이야기",
    "배경", "과정", "연혁", "시장", "내용", "또는", "그리고", "대한",
    "에서", "으로", "이라는", "했다는", "있는", "없는", "해당",
}


def _relevance_terms(value: str) -> List[str]:
    terms: List[str] = []
    for token in re.findall(
        r"(?:19|20)\d{2}|\d+(?:\.\d+)?%?|[A-Za-z][A-Za-z0-9-]{1,}|[가-힣]{2,}",
        value or "",
    ):
        normalized = token.casefold().strip()
        for suffix in (
            "에서", "으로", "부터", "까지", "에게", "에는", "이라는",
            "했다는", "년도", "년의", "년", "은", "는", "이", "가", "을", "를",
        ):
            if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 2:
                normalized = normalized[: -len(suffix)]
                break
        if (
            len(normalized) >= 2
            and normalized not in _RELEVANCE_STOP_TERMS
            and normalized not in terms
        ):
            terms.append(normalized)
    return terms


def result_relevant_to_query(row: Dict, query: str) -> bool:
    """Reject search hits that only share a generic country or category word."""

    terms = _relevance_terms(query)
    if not terms:
        return True
    blob = re.sub(
        r"\s+",
        "",
        f"{row.get('title', '')} {row.get('snippet', '')} {row.get('page_excerpt', '')}",
    ).casefold()
    matched = [term for term in terms if term.replace(" ", "") in blob]
    required = 1 if len(terms) <= 2 else 2
    numeric_terms = [term for term in terms if any(ch.isdigit() for ch in term)]
    numeric_required = min(2, len(numeric_terms))
    if sum(term in matched for term in numeric_terms) < numeric_required:
        return False
    generic_terms = {
        "한국", "러시아", "일본", "미국", "중국", "베트남", "해외",
        "세계", "시장", "공식", "자료", "근거", "관련", "연혁",
    }
    specific_terms = [
        term
        for term in terms
        if term not in generic_terms and not any(ch.isdigit() for ch in term)
    ]
    specific_required = 0
    if specific_terms:
        specific_required = 1 if len(specific_terms) <= 3 else 2
    if sum(term in matched for term in specific_terms) < specific_required:
        return False
    return len(matched) >= required


def _source_quality(row: Dict) -> Tuple[str, int]:
    """Classify search snippets before they can become factual evidence."""

    host = _host_for(str(row.get("url") or ""))
    blob = f"{row.get('title', '')} {row.get('snippet', '')}"
    if not host or _SPAM_MARKERS.search(blob):
        return "low", 0
    if any(_host_matches(host, value) for value in _LOW_QUALITY_HOSTS):
        return "low", 0
    if host.endswith(".go.kr") or host.endswith(".gov") or host.endswith(".gov.kr"):
        return "authoritative", 4
    if any(_host_matches(host, value) for value in _EDITORIAL_HOSTS):
        return "editorial", 3
    if host.endswith(".ac.kr") or host.endswith(".edu"):
        return "authoritative", 4
    return "general", 2


def row_queries(row: Dict) -> List[str]:
    return list(dict.fromkeys([str(row.get("query") or ""), *(row.get("matched_queries") or [])]))


def rank_search_results(
    rows: List[Dict],
    max_results: int,
) -> List[Dict]:
    """Prefer usable evidence and discard low-quality noise when alternatives exist."""

    normalized: List[Tuple[int, int, Dict]] = []
    seen = {}
    for position, value in enumerate(rows):
        if not isinstance(value, dict):
            continue
        row = dict(value)
        row["url"] = _normalize_search_url(str(row.get("url") or ""))
        key = _canonical_source_url(row["url"]) or str(
            row.get("title") or ""
        ).strip().casefold()
        if not key:
            continue
        if key in seen:
            prior = seen[key]
            prior["matched_queries"] = list(dict.fromkeys([*row_queries(prior), *row_queries(row)]))
            continue
        seen[key] = row
        tier, score = _source_quality(row)
        row["source_tier"] = tier
        row["source_score"] = score
        normalized.append((score, position, row))

    usable = [item for item in normalized if item[0] > 0]
    pool = usable if usable else normalized
    pool.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in pool[:max(0, max_results)]]


def _search_ddgs(query: str, max_results: int) -> List[Dict]:
    if DDGS is None:
        return []
    rows: List[Dict] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, region="kr-kr", max_results=max_results):
                rows.append({
                    "title": (r.get("title") or "").strip(),
                    "snippet": (r.get("body") or r.get("snippet") or "").strip(),
                    "url": (r.get("href") or r.get("link") or "").strip(),
                    "query": query,
                })
    except Exception as exc:  # noqa: BLE001
        log.info("DDG 검색 실패(%s): %s", query[:40], exc)
    return rows


def _search_ddg_html(query: str, max_results: int) -> List[Dict]:
    """패키지 없을 때 DuckDuckGo HTML 결과의 대략적 폴백."""
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 shortly-research"})
    try:
        with urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        log.info("DDG HTML 검색 실패(%s): %s", query[:40], exc)
        return []
    rows: List[Dict] = []
    titles = re.findall(
        r'class="result__a"[^>]*>(.*?)</a>', html, flags=re.I | re.S)
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)', html, flags=re.I | re.S)
    links = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"', html, flags=re.I)
    for i in range(min(max_results, len(titles))):
        title = re.sub(r"<[^>]+>", "", titles[i]).strip()
        snip = ""
        if i < len(snippets):
            snip = re.sub(r"<[^>]+>", "", snippets[i]).strip()
        href = links[i] if i < len(links) else ""
        if title:
            rows.append({
                "title": title, "snippet": snip, "url": href, "query": query})
        if len(rows) >= max_results:
            break
    return rows


def _search_bing_rss(query: str, max_results: int) -> List[Dict]:
    """Secondary no-key backend for periods when DuckDuckGo returns no rows."""

    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 shortly-research"})
    try:
        with urlopen(req, timeout=20) as resp:
            payload = resp.read()
    except Exception as exc:  # noqa: BLE001
        log.info("Bing RSS 검색 실패(%s): %s", query[:40], exc)
        return []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        log.info("Bing RSS 파싱 실패(%s): %s", query[:40], exc)
        return []
    rows: List[Dict] = []
    for item in root.findall(".//item"):
        title = html.unescape(str(item.findtext("title") or "")).strip()
        snippet = html.unescape(str(item.findtext("description") or "")).strip()
        snippet = re.sub(r"<[^>]+>", " ", snippet)
        snippet = re.sub(r"\s+", " ", snippet).strip()
        link = str(item.findtext("link") or "").strip()
        if title and link:
            rows.append(
                {
                    "title": title,
                    "snippet": snippet,
                    "url": link,
                    "query": query,
                }
            )
        if len(rows) >= max_results:
            break
    return rows


def _fetch_page_excerpt(url: str, query: str = "", max_chars: int = 5000) -> str:
    """Read a small HTML excerpt when a search snippet cannot carry key facts."""

    normalized = _normalize_search_url(url)
    if not normalized.startswith(("http://", "https://")):
        return ""
    if re.search(r"\.(?:pdf|hwp|hwpx|docx?|xlsx?|pptx?)(?:$|[?#])", normalized, re.I):
        return ""
    req = Request(normalized, headers={"User-Agent": "Mozilla/5.0 shortly-research"})
    try:
        with urlopen(req, timeout=8) as resp:
            content_type = str(resp.headers.get("Content-Type") or "").lower()
            if "html" not in content_type and "text" not in content_type:
                return ""
            payload = resp.read(250_000)
            charset = resp.headers.get_content_charset() or "utf-8"
    except Exception as exc:  # noqa: BLE001
        log.info("검색 결과 본문 확인 실패(%s): %s", normalized[:60], exc)
        return ""
    text_value = payload.decode(charset, "ignore")
    text_value = re.sub(r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>", " ", text_value, flags=re.I | re.S)
    text_value = re.sub(r"<[^>]+>", " ", text_value)
    text_value = html.unescape(text_value)
    text_value = re.sub(r"\s+", " ", text_value).strip()
    terms = _relevance_terms(query)
    if not terms:
        return text_value[:max_chars]
    windows = []
    lowered = text_value.casefold()
    for term in terms:
        start = 0
        for _ in range(3):
            position = lowered.find(term, start)
            if position < 0:
                break
            left = max(0, position - 350)
            right = min(len(text_value), position + 850)
            excerpt = text_value[left:right].strip()
            score = sum(
                (
                    3
                    if any(ch.isdigit() for ch in candidate)
                    else 1
                    if candidate in {"공식", "자료", "보도자료", "현지", "공장"}
                    else 2
                )
                for candidate in terms
                if candidate in excerpt.casefold()
            )
            windows.append((score, left, excerpt))
            start = position + len(term)
    if not windows:
        return text_value[:max_chars]
    selected = []
    seen_ranges = []
    for _, left, excerpt in sorted(windows, key=lambda item: (-item[0], item[1])):
        if any(abs(left - seen) < 500 for seen in seen_ranges):
            continue
        seen_ranges.append(left)
        selected.append(excerpt)
        if len(" … ".join(selected)) >= max_chars or len(selected) >= 4:
            break
    return " … ".join(selected)[:max_chars]


def web_search(query: str, max_results: int = 5) -> List[Dict]:
    """단일 쿼리 웹 검색."""
    candidate_limit = max(max_results * 2, min(12, max_results + 6))
    rows = _search_ddgs(query, candidate_limit)
    if not rows:
        rows = _search_ddg_html(query, candidate_limit)
    if not rows:
        rows = _search_bing_rss(query, candidate_limit)
    ranked = rank_search_results(rows, max_results)
    if any(marker in query for marker in ("공식", "자료", "연혁")):
        for row in ranked[:2]:
            excerpt = _fetch_page_excerpt(str(row.get("url") or ""), query=query)
            if excerpt:
                row["page_excerpt"] = excerpt
    return ranked


def collect_raw(
    topic: str,
    max_results: int = 8,
    max_queries: int = 4,
    subject_anchors: Optional[List[str]] = None,
    research_focus: Optional[str] = None,
    required_content: Optional[str] = None,
) -> Dict:
    """주제 관련 검색 결과를 쿼리별로 고르게 모아 중복 제거한다."""
    queries = build_queries(
        topic,
        max_queries=max_queries,
        subject_anchors=subject_anchors,
        research_focus=research_focus,
        required_content=required_content,
    )
    focus_items = parse_research_focus(research_focus)
    required_items = parse_required_content(required_content)
    focus_queries = [
        _clean_query_seed(item)[:140].strip()
        for item in focus_items
        if _clean_query_seed(item)[:140].strip()
    ]
    required_queries = [
        _clean_query_seed(item)[:140].strip()
        for item in required_items
        if _clean_query_seed(item)[:140].strip()
    ]
    per_q = max(3, max_results // max(1, len(queries) or 1))
    merged: List[Dict] = []
    seen_urls = {}
    seen_titles = {}
    buckets = [
        web_search(query, max_results=per_q)
        for query in queries
    ]
    max_bucket_size = max((len(bucket) for bucket in buckets), default=0)
    result_limit = max(max_results, len(queries) * 2)
    for result_position in range(max_bucket_size):
        for bucket in buckets:
            if result_position >= len(bucket):
                continue
            row = bucket[result_position]
            key = (row.get("url") or "").split("?")[0] or row.get("title") or ""
            title = row.get("title") or ""
            prior = seen_urls.get(key) or seen_titles.get(title)
            if prior is not None:
                prior["matched_queries"] = list(dict.fromkeys([*row_queries(prior), *row_queries(row)]))
                continue
            if key:
                seen_urls[key] = row
            if title:
                seen_titles[title] = row
            merged.append(row)
            if len(merged) >= result_limit:
                break
        if len(merged) >= result_limit:
            break
    return {
        "queries": queries,
        "results": merged,
        "backend": "ddgs" if DDGS is not None else "html_fallback",
        "research_focus": focus_items,
        "research_focus_queries": focus_queries,
        "required_content": required_items,
        "required_content_queries": required_queries,
    }


def _format_raw_brief(raw: Dict) -> str:
    lines = []
    for i, r in enumerate(raw.get("results") or [], 1):
        line = (
            f"{i}. [{r.get('source_tier', 'general')}] {r.get('title', '')}\n"
            f"   {r.get('snippet', '')}\n"
        )
        if r.get("page_excerpt"):
            line += f"   본문 발췌: {r.get('page_excerpt', '')[:800]}\n"
        line += f"   ({r.get('url', '')})"
        lines.append(line)
    return "\n".join(lines) if lines else "(검색 결과 없음)"


def _snippet_blob(raw: Dict) -> str:
    parts = []
    for r in raw.get("results") or []:
        parts.append(
            f"{r.get('title', '')} {r.get('snippet', '')} {r.get('page_excerpt', '')}"
        )
    return "\n".join(parts)


def _enrich_conflict_facts(hard_facts: List[str], raw: Dict) -> List[str]:
    """스니펫의 '지우라/요구/거절/고수→수출 포기' 갈등을 hard_facts 에 보강."""
    blob = _snippet_blob(raw)
    extras: List[str] = []
    if re.search(r"일본.{0,12}(요구|요청)", blob) and re.search(
            r"(거부|거절|고수|지우느니|지울 수 없)", blob):
        extras.append(
            "일본 측이 독도 표기 관련 수정을 요구·요청했고, "
            "회사는 이를 거부·고수하며 일본 수출/시장을 포기했다."
        )
    elif re.search(r"(독도를 지우|표기를 지우|지울 수 없).{0,24}(수출|시장).{0,12}포기", blob) \
            or re.search(r"지우느니.{0,8}(시장|수출).{0,8}포기", blob):
        extras.append(
            "독도 표기를 지우느니 일본 시장/수출을 포기했다는 보도가 있다."
        )
    out = list(hard_facts)
    joined = " ".join(out)
    for e in extras:
        if any(k in joined for k in ("요구", "요청", "거부", "지우느니")):
            break
        out.insert(0, e)
        joined = e + " " + joined
    return out


def _filter_overcautious_avoids(avoid: List[str], raw: Dict) -> List[str]:
    """스니펫에 근거 있는 '요구·거절'까지 avoid 로 막는 항목을 제거한다."""
    blob = _snippet_blob(raw)
    has_demand = bool(re.search(r"일본.{0,12}(요구|요청)", blob)) and bool(
        re.search(r"(거부|거절|고수|지우느니)", blob))
    out: List[str] = []
    for a in avoid:
        if has_demand and re.search(r"요구|거부|지우라고", a):
            if re.search(r"세부|수치|대화|금액", a):
                out.append(
                    "일본 측 요구·회사 거부의 세부 대화문/금액/날짜를 "
                    "스니펫 이상으로 창작하지 말 것 "
                    "(요구→거부→수출 포기 사건 자체는 사용 가능)"
                )
            # 사건 자체를 통째로 금지하는 항목은 버림
            continue
        out.append(a)
    return list(dict.fromkeys(out))


def _build_brief_text(base: Dict) -> str:
    parts = []
    if base.get("narrative_axis"):
        parts.append(f"[중심 인과축 — 모든 비트가 이 축에 기여해야 함]\n- {base['narrative_axis']}")
    if base.get("research_focus_coverage"):
        lines = []
        for row in base["research_focus_coverage"]:
            status = "검색 결과 있음" if row.get("result_count") else "근거 미확보"
            lines.append(f"- {row.get('item')}: {status}")
        parts.append("[사용자 지정 조사 항목 확인 상태]\n" + "\n".join(lines))
    if base.get("required_content"):
        coverage_by_item = {
            str(row.get("item") or ""): row
            for row in base.get("required_content_coverage") or []
        }
        lines = []
        for item in base["required_content"]:
            row = coverage_by_item.get(str(item), {})
            status = "관련 근거 있음" if row.get("result_count") else "근거 미확보"
            lines.append(f"- [{status}] {item}")
        parts.append(
            "[대본 필수 포함 내용 — 사용자가 직접 지정한 필수 재료이므로 모두 대본에서 회수. "
            "근거 미확보 항목도 입력 범위 그대로 유지하되 세부를 덧붙이지 말고 품질 경고로 남길 것]\n"
            + "\n".join(lines)
        )
    if base.get("quantified_facts"):
        lines = []
        for fact in base["quantified_facts"]:
            comparison = " ".join(
                str(fact.get(key) or "").strip()
                for key in (
                    "comparison_period",
                    "comparison_value",
                    "change",
                )
            ).strip()
            line = (
                f"- 주체·범위: {fact['subject']} / 지표: {fact['metric']} / "
                f"기준시점: {fact['period']} / 값: {fact['value']} {fact['unit']}"
            )
            if comparison:
                line += f" / 비교: {comparison}"
            line += (
                f" / 출처: {fact['source_label']} ({fact['source_url']})"
            )
            lines.append(line)
        parts.append(
            "[출처·범위가 정리된 수치 — 아래 주체·범위·기준시점·출처를 함께 유지할 때만 대본에 사용]\n"
            + "\n".join(lines)
        )
    if base.get("hard_facts"):
        parts.append(
            "[하드 팩트 — 사건으로 적극 사용. 확인된 갈등/대치는 밋밋하게 줄이지 말 것]\n"
            + "\n".join(f"- {x}" for x in base["hard_facts"])
        )
    else:
        parts.append("[하드 팩트]\n- (추출된 확인 사실 없음. 사건·조건·인용 창작 금지)")
        base["thin"] = True
    if base.get("supporting_facts"):
        parts.append(
            "[보조 사실 — 배경 설명용. 훅·반전·결말 재료로 사용 금지]\n"
            + "\n".join(f"- {x}" for x in base["supporting_facts"])
        )
    if base.get("drama_beats"):
        parts.append(
            "[드라마 비트 — 중심 인과축에 직접 기여하는 항목만 선택 사용]\n"
            + "\n".join(f"- {x}" for x in base["drama_beats"])
        )
    if base.get("user_approved_soft_anecdotes"):
        parts.append(
            "[사용자 승인 소프트 일화 — 개인 증언으로만 귀속]\n"
            + "\n".join(
                f"- {x}" for x in base["user_approved_soft_anecdotes"]
            )
        )
    if base.get("soft_angles"):
        parts.append(
            "[소프트 각도(시각 분위기 참고만 — 대사 속 사실·사건으로 사용 금지)]\n"
            + "\n".join(f"- {x}" for x in base["soft_angles"])
        )
    if base.get("curiosity_hooks"):
        parts.append(
            "[호기심 각도(근거 있는 것만)]\n"
            + "\n".join(f"- {x}" for x in base["curiosity_hooks"])
        )
    if base.get("avoid_claims"):
        parts.append("[쓰지 말 것]\n" + "\n".join(f"- {x}" for x in base["avoid_claims"]))
    if base.get("out_of_scope_facts"):
        parts.append(
            "[같은 제품과 관련 있어도 중심 서사 밖이라 쓰지 말 것]\n"
            + "\n".join(f"- {x}" for x in base["out_of_scope_facts"])
        )
    return "\n\n".join(parts)


def distill_research(llm: Optional[LLMClient], topic: str, raw: Dict) -> Dict:
    """검색 결과를 대본용 하드팩트/드라마비트/소프트각도로 압축."""
    raw_txt = _format_raw_brief(raw)
    base: Dict = {
        "queries": raw.get("queries") or [],
        "results": raw.get("results") or [],
        "backend": raw.get("backend"),
        "subject_filter": raw.get("subject_filter"),
        "relevance_filter": raw.get("relevance_filter"),
        "research_focus": raw.get("research_focus") or [],
        "research_focus_queries": raw.get("research_focus_queries") or [],
        "research_focus_coverage": raw.get("research_focus_coverage") or [],
        "required_content": raw.get("required_content") or [],
        "required_content_queries": raw.get("required_content_queries") or [],
        "required_content_coverage": raw.get("required_content_coverage") or [],
        "narrative_axis": "",
        "out_of_scope_facts": [],
        "quantified_facts": [],
        "hard_facts": [],
        "supporting_facts": [],
        "drama_beats": [],
        "soft_angles": [],
        "curiosity_hooks": [],
        "avoid_claims": [],
        "brief_text": raw_txt,
        "thin": not bool(raw.get("results")),
    }
    if not raw.get("results"):
        base["avoid_claims"] = [
            "검색/입력에 없는 협상 조건·요구·발언·인용",
            "검색/입력에 없는 수출 제안 규모·매출 폭증 등 구체 수치 사건",
        ]
        base["brief_text"] = _build_brief_text(base)
        return base

    if llm is None or not getattr(llm, "available", False):
        base["hard_facts"] = _enrich_conflict_facts([], raw)
        if base["hard_facts"]:
            base["drama_beats"] = list(base["hard_facts"][:2])
            base["thin"] = False
        base["brief_text"] = _build_brief_text(base)
        return base

    system = (
        "너는 쇼츠 대본용 팩트 리서처다. 검색 스니펫에 나온 내용만 추출하되, "
        "각 결과 앞의 source_tier를 출처 신뢰도 경계로 사용한다. authoritative와 "
        "editorial을 우선하고 general은 보조 근거로만 쓴다. low 결과는 검색 단서일 "
        "뿐이므로 단독으로 hard_facts·drama_beats·quantified_facts를 만들지 마라. "
        "서로 독립적인 둘 이상의 출처가 같은 사건을 뒷받침하면 더 신뢰한다. "
        "사용자 주제의 주인공 회사·제품만 hard_facts와 drama_beats로 정리한다. "
        "먼저 사용자 요청에서 중심 인과축을 한 문장으로 정하고, 그 축의 원인·갈등·선택·결과에 "
        "직접 기여하는 사실만 hard_facts와 drama_beats로 올려라. 같은 제품이 등장한다는 이유만으로 "
        "시간·장소·인과가 다른 별도 일화(조난 생존담, 연예인 일화, 별도 캠페인 등)를 끼워 넣지 마라. "
        "사용자가 특정 풍습·사건을 핵심으로 지정했다면 명절 선물·결혼 답례품 같은 인접 소비 풍습을 "
        "그 핵심 대신 전면에 내세우지 마라. 직접 인과를 설명하는 데 꼭 필요하지 않으면 제외하라. "
        "사용자가 별도 추가 조사 항목으로 지정한 풍습·장면은 단순 배경 키워드가 아니라 훅과 결말의 "
        "핵심 후보로 평가한다. 그러나 그 장면의 제작법·장식 설명만으로 narrative_axis를 채우지 마라. "
        "제품이 불리한 시장 조건을 깨고 해당 문화 현상까지 도달한 과정이 확인되면 시장 장벽·해결 행동·"
        "성과도 drama_beats로 유지한다. 점유율·매출은 숫자 자체가 아니라 변화의 규모를 증명할 때만 사용하고, "
        "중심 사건과 무관하며 사용자가 요청하지 않은 운영 메타정보만 supporting_facts로 내린다. "
        "특히 결말 재료는 중심 사건의 결과나 훅의 회수여야 하며, 별도 미담으로 교체하지 마라. "
        "사실의 정확성과 서사적 가치를 구분하라. 회사가 화제가 된 장면을 나중에 마케팅·홍보에 "
        "활용했다는 사실, 관계자의 후속 코멘트, 기념 캠페인, 수상 이력은 사실일 수 있어도 대개 "
        "시청자가 기다린 보상이나 반전이 아닐 수 있다. 그러나 검증된 사실 안에서 사용자 지정 필수 결말 "
        "> 중심 사건의 직접적인 결과 > 일반 편집 취향 순으로 우선한다. 사용자 지정 행사나 복귀 파티처럼 "
        "중심 사건의 결과를 보여주는 장면은 drama_beats와 결말 후보에 보존한다. 마케팅이라는 단어만으로 "
        "배제하지 말고 요청되지 않은 무관한 운영·홍보 정보만 supporting_facts로 내린다. "
        "비슷한 사건의 다른 회사가 같은 기사에 등장하면 주체 혼동 방지용으로만 보고, "
        "사용자가 비교를 요청하지 않은 이상 그 회사의 사건을 대본 재료로 올리지 마라. "
        "사용자 입력에 동기·행동·실패·궁여지책·학습 과정이 구체적으로 적혀 있고 스니펫이 "
        "이를 뒷받침하면, 그 항목을 주변 관광·전시·사망 정보보다 먼저 hard_facts와 "
        "drama_beats에 올려라. 탄생 비화 요청에서 박물관·체험장·현재 관광 시설로 개척 과정을 "
        "대체하지 마라. "
        "시청 긴장을 만드는 '확인된 갈등'(누가 무엇을 요구/거절/고수했는지)은 "
        "hard_facts 와 drama_beats 에 반드시 넣어라. "
        "스니펫에 '일본 측 요구를 거부', '독도를 지우느니 시장 포기'처럼 나오면 "
        "그걸 avoid 로 올리지 말고 hard_facts/drama_beats 로 올려라. "
        "avoid 에는 스니펫에 없는 세부 대화문·금액·날짜·주체 뒤집기·'인정하라' 창작만 넣는다. "
        "스니펫에 없는 인과를 보강하지 마라. soft_angles 는 시각 분위기 참고 전용이다. "
        "soft_angles의 소품·장소·연출을 대사 속 사실이나 새 사건으로 승격하지 말고, 스니펫에 "
        "명시되지 않은 선물 상자·장식·행사 장면 같은 구체물을 만들지 마라.\n"
        "curiosity_hooks에서 '왜 하필'을 범용 호기심 공식으로 사용하지 마라. '하필'은 원치 않은 "
        "선택이나 불운한 우연 자체가 확인된 사건일 때만 쓰고, 긍정적인 의외성·제품 정체·성공에는 "
        "중립적인 '왜'나 '어떻게'를 사용한다.\n"
        "수치(매출·수출액·비율·증가율·가격·인원 등)는 별도 quantified_facts로 추출한다. "
        "주체/지역/품목 범위, 정확한 지표명, 값과 단위, 절대 기준연도·기간, 출처 URL이 "
        "모두 확인되는 경우에만 넣는다. 기사 게시연도와 통계 기준연도를 구분하고, "
        "전국 김 수출과 특정 지역 김 수출, 전체 김과 조미김처럼 범위가 다른 통계를 섞지 마라. "
        "업계 전체 수치를 특정 회사의 실적으로 표현하지 마라. 불완전한 수치는 quantified_facts와 "
        "hard_facts 양쪽에서 모두 제외하고 avoid_claims에 이유를 남겨라.\n"
        "시점·주체(매우 중요):\n"
        "- hard_facts 의 시점에는 가능하면 절대 연도(예: 2011년)를 붙여라. "
        "'올해/작년/최근'만 있으면 그 문장을 hard_facts 에 넣지 말고 avoid 에 "
        "'상대 시점 단정 금지'로 올려라.\n"
        "- 보도일·재조명·품절·돈쭐 시점과 사건 발생 시점을 섞지 마라. "
        "2024년 재조명 기사라도 수출 포기가 그해라는 뜻이 아니다.\n"
        "- 한 스니펫/기사에 회사 A·B가 같이 나오면(예: 올바름 + 성경식품) "
        "문장 주어를 엄격히 분리해라. A의 '올해 초 수출 불발'을 B hard_facts 에 넣지 마라."
    )
    schema = (
        '{"narrative_axis": string(사용자 요청의 중심 사건과 결말을 잇는 한 문장), '
        '"out_of_scope_facts": 최대 4개 string[](같은 제품 관련이지만 중심 인과축 밖인 별도 일화), '
        '"quantified_facts": 최대 4개 [{"subject": string(통계 주체·지역·품목 범위), '
        '"metric": string, "value": string, "unit": string, '
        '"period": string(절대연도·기간), "comparison_value": string, '
        '"comparison_period": string, "change": string, '
        '"source_label": string, "source_url": string}], '
        '"hard_facts": 최대 6개 string[](스니펫 근거 중심 사건·행동·결과, 주체 명시), '
        '"supporting_facts": 최대 4개 string[](사실이지만 배경·기업 홍보·후속 운영 정보라 결말 가치가 낮은 항목), '
        '"drama_beats": 최대 3개 string[](스니펫 근거 갈등/대치/한 방), '
        '"soft_angles": 최대 2개 string[](분위기 과장만), '
        '"curiosity_hooks": 최대 2개 string[](근거 있는 궁금증), '
        '"avoid_claims": 최대 6개 string[](스니펫에 없는 창작·상대시점 단정·주체혼동)}. '
        "모든 문자열은 160자 이내로 간결하게 쓴다."
    )
    user = (
        f"[사용자 주제/메모]\n{topic}\n\n"
        f"[대본 필수 포함 내용 — 검색 근거 여부와 관계없이 사용자 입력 범위 그대로 빠뜨리지 말 것]\n"
        f"{chr(10).join('- ' + x for x in (raw.get('required_content') or [])) or '- (없음)'}\n\n"
        f"[사용자가 지정한 추가 조사 항목 — 각 항목의 검색 결과를 별도로 확인]\n"
        f"{chr(10).join('- ' + x for x in (raw.get('research_focus') or [])) or '- (없음)'}\n\n"
        f"[웹 검색 스니펫 — 여기 없는 내용은 사실이 아님]\n{raw_txt}\n\n"
        f"스키마: {schema}"
    )
    try:
        data = llm.complete_json(system, user, temperature=0.1, max_tokens=3500)
    except LLMResponseParseError as exc:
        log.info("리서치 요약 JSON 재시도: %s", exc)
        retry_system = (
            system
            + "\n이전 응답이 너무 길거나 불완전했다. 각 배열의 최대 개수를 지키고 "
            "중복을 제거해 2,400 토큰 이내의 완전한 JSON 하나로만 압축하라."
        )
        try:
            data = llm.complete_json(
                retry_system,
                user,
                temperature=0.0,
                max_tokens=2800,
            )
        except LLMQuotaExceeded:
            raise
        except LLMError as retry_exc:
            log.info("리서치 요약 LLM 재시도 실패: %s", retry_exc)
            base["hard_facts"] = _enrich_conflict_facts([], raw)
            if base["hard_facts"]:
                base["drama_beats"] = list(base["hard_facts"][:2])
                base["thin"] = False
            base["brief_text"] = _build_brief_text(base)
            return base
    except LLMQuotaExceeded:
        raise
    except LLMError as exc:
        log.info("리서치 요약 LLM 실패: %s", exc)
        base["hard_facts"] = _enrich_conflict_facts([], raw)
        if base["hard_facts"]:
            base["drama_beats"] = list(base["hard_facts"][:2])
            base["thin"] = False
        base["brief_text"] = _build_brief_text(base)
        return base

    if not isinstance(data, dict):
        base["hard_facts"] = _enrich_conflict_facts([], raw)
        base["brief_text"] = _build_brief_text(base)
        return base

    base["narrative_axis"] = str(data.get("narrative_axis") or "").strip()[:240]
    out_of_scope = data.get("out_of_scope_facts") or []
    if isinstance(out_of_scope, list):
        base["out_of_scope_facts"] = [
            str(value).strip()[:240]
            for value in out_of_scope
            if str(value).strip()
        ][:4]

    for key in ("hard_facts", "supporting_facts", "drama_beats", "soft_angles",
                "curiosity_hooks", "avoid_claims"):
        val = data.get(key) or []
        if isinstance(val, list):
            limit = {
                "hard_facts": 6,
                "supporting_facts": 4,
                "drama_beats": 3,
                "soft_angles": 2,
                "curiosity_hooks": 2,
                "avoid_claims": 6,
            }[key]
            base[key] = [
                str(x).strip()[:240]
                for x in val
                if str(x).strip()
            ][:limit]

    quantified = data.get("quantified_facts") or []
    allowed_source_urls = {
        _canonical_source_url(str(row.get("url") or ""))
        for row in base["results"]
        if (
            isinstance(row, dict)
            and str(row.get("url") or "").strip()
            and str(row.get("source_tier") or "general") != "low"
        )
    }
    if isinstance(quantified, list):
        for value in quantified:
            if not isinstance(value, dict):
                continue
            normalized = {
                key: str(value.get(key) or "").strip()
                for key in (
                    "subject",
                    "metric",
                    "value",
                    "unit",
                    "period",
                    "comparison_value",
                    "comparison_period",
                    "change",
                    "source_label",
                    "source_url",
                )
            }
            required = (
                "subject",
                "metric",
                "value",
                "unit",
                "period",
                "source_label",
                "source_url",
            )
            if all(normalized[key] for key in required) and (
                _ABSOLUTE_YEAR.search(normalized["period"])
                and not _RELATIVE_TIME.search(normalized["period"])
                and not _PROJECTION.search(
                    " ".join(normalized.values())
                )
                and normalized["source_url"].startswith(("http://", "https://"))
                and _canonical_source_url(normalized["source_url"])
                in allowed_source_urls
                and (
                    not (
                        normalized["comparison_value"]
                        or normalized["change"]
                    )
                    or (
                        bool(normalized["comparison_period"])
                        and bool(
                            _ABSOLUTE_YEAR.search(
                                normalized["comparison_period"]
                            )
                        )
                    )
                )
                and _quantified_fact_is_supported(
                    normalized,
                    base["results"],
                )
            ):
                base["quantified_facts"].append(normalized)
            if len(base["quantified_facts"]) >= 4:
                break

    base["hard_facts"] = _enrich_conflict_facts(base["hard_facts"], raw)
    base["hard_facts"], base["avoid_claims"] = _sanitize_temporal_facts(
        base["hard_facts"], base["avoid_claims"])
    for key in (
        "hard_facts",
        "drama_beats",
        "soft_angles",
        "curiosity_hooks",
    ):
        base[key], base["avoid_claims"] = _strip_unscoped_numeric_claims(
            base[key],
            base["avoid_claims"],
        )
    if base["hard_facts"] and not base["drama_beats"]:
        base["drama_beats"] = [
            h for h in base["hard_facts"]
            if any(k in h for k in ("요구", "요청", "거부", "거절", "지우", "고수", "포기"))
        ][:3]
    base["avoid_claims"] = _filter_overcautious_avoids(
        list(dict.fromkeys(base["avoid_claims"] + [
            "검색에 없는 '인정하라/사과하라' 식의 상대국 요구 창작",
            "검색에 없는 협상 금액·매출 수치·세부 대화문 창작",
            "행위 주체 뒤집기(예: 요청한 쪽과 거절한 쪽을 바꿈)",
            "보도/재조명 시점을 사건 발생 시점으로 단정",
            "다른 회사 기사의 시점·사건을 주제에 붙이기",
        ])),
        raw,
    )
    base["thin"] = not bool(base["hard_facts"])
    base["brief_text"] = _build_brief_text(base)
    return base


def research_topic(
    topic: str,
    llm: Optional[LLMClient] = None,
    max_results: int = 8,
    max_queries: int = 4,
    subject_anchors: Optional[List[str]] = None,
    seed_results: Optional[List[Dict]] = None,
    research_focus: Optional[str] = None,
    required_content: Optional[str] = None,
) -> Dict:
    """주제 웹 조사 + (가능하면) LLM 요약. 실패해도 빈 구조 반환."""
    topic = (topic or "").strip()
    if not topic:
        return {
            "queries": [], "results": [], "backend": "none",
            "quantified_facts": [], "hard_facts": [], "drama_beats": [],
            "soft_angles": [],
            "curiosity_hooks": [], "avoid_claims": [],
            "brief_text": "", "thin": True,
        }
    log.info("웹 조사 시작: %s", topic[:60])
    raw = collect_raw(
        topic,
        max_results=max_results,
        max_queries=max_queries,
        subject_anchors=subject_anchors,
        research_focus=research_focus,
        required_content=required_content,
    )
    if seed_results:
        combined = [*seed_results, *(raw.get("results") or [])]
        raw["results"] = rank_search_results(combined, len(combined))
        raw["seeded_results"] = len(seed_results)
    unfiltered_results = list(raw.get("results") or [])
    relevant_results = [
        row
        for row in unfiltered_results
        if any(result_relevant_to_query(row, query) for query in row_queries(row))
    ]
    raw["relevance_filter"] = {
        "kept": len(relevant_results),
        "excluded": len(unfiltered_results) - len(relevant_results),
    }
    raw["results"] = rank_search_results(
        relevant_results,
        max(8, len(relevant_results)),
    )
    anchors = [
        re.sub(r"\s+", "", str(value or "")).casefold()
        for value in subject_anchors or []
        if str(value or "").strip()
    ]
    if anchors:
        original_results = list(raw.get("results") or [])
        filtered_results = []
        for row in original_results:
            blob = re.sub(
                r"\s+",
                "",
                f"{row.get('title', '')} {row.get('snippet', '')}",
            ).casefold()
            # 사용자가 명시한 조사 쟁점은 주인공 이름이 없는 문화·관습 자료일 수
            # 있으므로 주체 필터로 버리지 않는다.
            if (
                any(anchor in blob for anchor in anchors)
                or set(row_queries(row)) & set([*(raw.get("research_focus_queries") or []),
                        *(raw.get("required_content_queries") or [])])
            ):
                filtered_results.append(row)
        raw["results"] = filtered_results
        raw["subject_filter"] = {
            "anchors": list(subject_anchors or []),
            "excluded_results": len(original_results) - len(filtered_results),
        }
        raw["results"] = filtered_results
        log.info(
            "웹 조사 주체 필터: anchors=%s kept=%d excluded=%d",
            subject_anchors,
            len(raw["results"]),
            len(original_results) - len(filtered_results),
        )
    # Keep a source for each explicit query before filling the general budget.
    ranked = raw.get("results") or []
    chosen = []
    for query in [*(raw.get("required_content_queries") or []), *(raw.get("research_focus_queries") or [])]:
        match = next((row for row in ranked if query in row_queries(row)), None)
        if match is not None and match not in chosen:
            chosen.append(match)
    limit = max(8, max_results * 2, len(chosen))
    for row in ranked:
        if len(chosen) >= limit:
            break
        if row not in chosen:
            chosen.append(row)
    raw["results"] = chosen
    raw["research_focus_coverage"] = [
        {
            "item": item,
            "query": query,
            "result_count": sum(
                1
                for row in raw.get("results") or []
                if query in row_queries(row)
            ),
            "source_urls": [
                str(row.get("url") or "")
                for row in raw.get("results") or []
                if query in row_queries(row)
                and str(row.get("url") or "")
            ][:3],
        }
        for item, query in zip(
            raw.get("research_focus") or [],
            raw.get("research_focus_queries") or [],
        )
    ]
    raw["required_content_coverage"] = [
        {
            "item": item,
            "query": query,
            "result_count": sum(
                1
                for row in raw.get("results") or []
                if query in row_queries(row)
            ),
            "source_urls": [
                str(row.get("url") or "")
                for row in raw.get("results") or []
                if query in row_queries(row)
                and str(row.get("url") or "")
            ][:3],
        }
        for item, query in zip(
            raw.get("required_content") or [],
            raw.get("required_content_queries") or [],
        )
    ]
    log.info("웹 조사 결과 %d건 (backend=%s)",
             len(raw.get("results") or []), raw.get("backend"))
    return distill_research(llm, topic, raw)
