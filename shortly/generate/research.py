"""주제 관련 웹 조사 — 대본 생성용 팩트/각도 보강.

기본 백엔드: ddgs / duckduckgo-search (API 키 불필요).
미설치·실패 시 빈 결과로 폴백해 파이프라인은 계속 진행합니다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from ..llm.base import LLMClient, LLMError, LLMResponseParseError
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
    r"자유게시판|유머\s*게시판|사랑방)",
    re.I,
)


def _clean_query_seed(text: str) -> str:
    """위키/광고/구어 잡음을 줄인 검색 시드 문자열."""
    t = (text or "").strip()
    t = re.sub(r"ADVERTISEMENT|\[[^\]]+\]|#{1,6}\s*", " ", t)
    t = _FLUFF.sub(" ", t)
    t = re.sub(r"\s+", " ", t)
    return t[:220].strip()


def build_queries(topic: str, max_queries: int = 4) -> List[str]:
    """주제에서 사실 확인용 검색 쿼리를 만든다.

    '썰' 같은 구어를 제거하고, 고유명사+사건 키워드 조합을 우선한다.
    """
    seed = _clean_query_seed(topic)
    if not seed:
        return []
    first = re.split(r"[.\n!?]", seed)[0].strip() or seed
    first = first[:90]
    queries: List[str] = [first]

    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,}|[가-힣]{2,}", first)
              if t not in _STOP]
    brand = tokens[0] if tokens else ""
    if len(brand) >= 3:
        brand = re.sub(r"(?:은|는|이|가|을|를|의|과|와)$", "", brand)
    core = " ".join(tokens[:4]).strip()

    extras: List[str] = []
    if brand:
        entity = f'"{brand}"'
        if any(k in seed for k in ("해외", "미국", "성공", "실적", "잘 팔", "수출")):
            extras.extend([
                f"{entity} 해외 수출 실적",
                f"{entity} 미국 수출 판매",
            ])
        if any(k in seed for k in ("독도", "일본", "수출")):
            extras.extend([
                # 시점 혼동 방지: 발생 연도/재조명 구분용 (우선)
                f"{entity} 일본 수출 포기 언제",
                f"{entity} 독도 일본 수출",
            ])
        extras.extend([f"{entity} 유행", f"{entity} 가격"])
    if core and core != first:
        extras.append(core)
    if "성경김" in first or brand == "성경김":
        extras.insert(0, '"성경식품" 독도 일본 수출')

    seen = {first}
    for q in extras:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)
        if len(queries) >= max_queries:
            break
    return queries[:max_queries]


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
        f"{source.get('title', '')} {source.get('snippet', '')}"
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


def rank_search_results(
    rows: List[Dict],
    max_results: int,
) -> List[Dict]:
    """Prefer usable evidence and discard low-quality noise when alternatives exist."""

    normalized: List[Tuple[int, int, Dict]] = []
    seen = set()
    for position, value in enumerate(rows):
        if not isinstance(value, dict):
            continue
        row = dict(value)
        row["url"] = _normalize_search_url(str(row.get("url") or ""))
        key = _canonical_source_url(row["url"]) or str(
            row.get("title") or ""
        ).strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
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


def web_search(query: str, max_results: int = 5) -> List[Dict]:
    """단일 쿼리 웹 검색."""
    candidate_limit = max(max_results * 2, min(12, max_results + 6))
    rows = _search_ddgs(query, candidate_limit)
    if not rows:
        rows = _search_ddg_html(query, candidate_limit)
    return rank_search_results(rows, max_results)


def collect_raw(topic: str, max_results: int = 8,
                max_queries: int = 4) -> Dict:
    """주제 관련 검색 결과를 쿼리별로 고르게 모아 중복 제거한다."""
    queries = build_queries(topic, max_queries=max_queries)
    per_q = max(3, max_results // max(1, len(queries) or 1))
    merged: List[Dict] = []
    seen_urls = set()
    seen_titles = set()
    buckets = [
        web_search(query, max_results=per_q)
        for query in queries
    ]
    max_bucket_size = max((len(bucket) for bucket in buckets), default=0)
    for result_position in range(max_bucket_size):
        for bucket in buckets:
            if result_position >= len(bucket):
                continue
            row = bucket[result_position]
            key = (row.get("url") or "").split("?")[0] or row.get("title") or ""
            title = row.get("title") or ""
            if key and key in seen_urls:
                continue
            if title and title in seen_titles:
                continue
            if key:
                seen_urls.add(key)
            if title:
                seen_titles.add(title)
            merged.append(row)
            if len(merged) >= max_results:
                break
        if len(merged) >= max_results:
            break
    return {
        "queries": queries,
        "results": merged,
        "backend": "ddgs" if DDGS is not None else "html_fallback",
    }


def _format_raw_brief(raw: Dict) -> str:
    lines = []
    for i, r in enumerate(raw.get("results") or [], 1):
        lines.append(
            f"{i}. [{r.get('source_tier', 'general')}] {r.get('title', '')}\n"
            f"   {r.get('snippet', '')}\n"
            f"   ({r.get('url', '')})"
        )
    return "\n".join(lines) if lines else "(검색 결과 없음)"


def _snippet_blob(raw: Dict) -> str:
    parts = []
    for r in raw.get("results") or []:
        parts.append(f"{r.get('title', '')} {r.get('snippet', '')}")
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
    if base.get("drama_beats"):
        parts.append(
            "[드라마 비트 — 쇼츠 긴장용(스니펫 근거). 반드시 살릴 것]\n"
            + "\n".join(f"- {x}" for x in base["drama_beats"])
        )
    if base.get("soft_angles"):
        parts.append(
            "[소프트 각도(분위기 과장만)]\n"
            + "\n".join(f"- {x}" for x in base["soft_angles"])
        )
    if base.get("curiosity_hooks"):
        parts.append(
            "[호기심 각도(근거 있는 것만)]\n"
            + "\n".join(f"- {x}" for x in base["curiosity_hooks"])
        )
    if base.get("avoid_claims"):
        parts.append("[쓰지 말 것]\n" + "\n".join(f"- {x}" for x in base["avoid_claims"]))
    return "\n\n".join(parts)


def distill_research(llm: Optional[LLMClient], topic: str, raw: Dict) -> Dict:
    """검색 결과를 대본용 하드팩트/드라마비트/소프트각도로 압축."""
    raw_txt = _format_raw_brief(raw)
    base: Dict = {
        "queries": raw.get("queries") or [],
        "results": raw.get("results") or [],
        "backend": raw.get("backend"),
        "subject_filter": raw.get("subject_filter"),
        "quantified_facts": [],
        "hard_facts": [],
        "drama_beats": [],
        "soft_angles": [],
        "curiosity_hooks": [],
        "avoid_claims": [],
        "brief_text": raw_txt,
        "thin": not bool(raw.get("results")),
    }
    if not raw.get("results"):
        base["brief_text"] = (
            "(검색 결과 없음 — 하드 팩트·인과·조건·인용을 창작하지 말 것. "
            "사용자 입력에 명시된 내용만 짧게 써라.)"
        )
        base["avoid_claims"] = [
            "검색/입력에 없는 협상 조건·요구·발언·인용",
            "검색/입력에 없는 수출 제안 규모·매출 폭증 등 구체 수치 사건",
        ]
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
        "비슷한 사건의 다른 회사가 같은 기사에 등장하면 주체 혼동 방지용으로만 보고, "
        "사용자가 비교를 요청하지 않은 이상 그 회사의 사건을 대본 재료로 올리지 마라. "
        "시청 긴장을 만드는 '확인된 갈등'(누가 무엇을 요구/거절/고수했는지)은 "
        "hard_facts 와 drama_beats 에 반드시 넣어라. "
        "스니펫에 '일본 측 요구를 거부', '독도를 지우느니 시장 포기'처럼 나오면 "
        "그걸 avoid 로 올리지 말고 hard_facts/drama_beats 로 올려라. "
        "avoid 에는 스니펫에 없는 세부 대화문·금액·날짜·주체 뒤집기·'인정하라' 창작만 넣는다. "
        "스니펫에 없는 인과를 보강하지 마라. soft_angles 는 분위기만.\n"
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
        '{"quantified_facts": 최대 4개 [{"subject": string(통계 주체·지역·품목 범위), '
        '"metric": string, "value": string, "unit": string, '
        '"period": string(절대연도·기간), "comparison_value": string, '
        '"comparison_period": string, "change": string, '
        '"source_label": string, "source_url": string}], '
        '"hard_facts": 최대 6개 string[](스니펫 근거 사건·제품·절대연도 가능하면 포함, 주체 명시), '
        '"drama_beats": 최대 3개 string[](스니펫 근거 갈등/대치/한 방), '
        '"soft_angles": 최대 2개 string[](분위기 과장만), '
        '"curiosity_hooks": 최대 2개 string[](근거 있는 궁금증), '
        '"avoid_claims": 최대 6개 string[](스니펫에 없는 창작·상대시점 단정·주체혼동)}. '
        "모든 문자열은 160자 이내로 간결하게 쓴다."
    )
    user = (
        f"[사용자 주제/메모]\n{topic[:2000]}\n\n"
        f"[웹 검색 스니펫 — 여기 없는 내용은 사실이 아님]\n{raw_txt[:4500]}\n\n"
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
        except LLMError as retry_exc:
            log.info("리서치 요약 LLM 재시도 실패: %s", retry_exc)
            base["hard_facts"] = _enrich_conflict_facts([], raw)
            if base["hard_facts"]:
                base["drama_beats"] = list(base["hard_facts"][:2])
                base["thin"] = False
            base["brief_text"] = _build_brief_text(base)
            return base
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

    for key in ("hard_facts", "drama_beats", "soft_angles",
                "curiosity_hooks", "avoid_claims"):
        val = data.get(key) or []
        if isinstance(val, list):
            limit = {
                "hard_facts": 6,
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
    raw = collect_raw(topic, max_results=max_results, max_queries=max_queries)
    if seed_results:
        combined_results = []
        seen = set()
        for row in [*seed_results, *(raw.get("results") or [])]:
            if not isinstance(row, dict):
                continue
            key = _canonical_source_url(str(row.get("url") or "")) or str(
                row.get("title") or ""
            ).strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            combined_results.append(row)
        raw["results"] = combined_results
        raw["seeded_results"] = len(seed_results)
    raw["results"] = rank_search_results(
        list(raw.get("results") or []),
        max(8, max_results * 2),
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
            if any(anchor in blob for anchor in anchors):
                filtered_results.append(row)
        raw["results"] = filtered_results
        raw["subject_filter"] = {
            "anchors": list(subject_anchors or []),
            "excluded_results": len(original_results) - len(filtered_results),
        }
        raw["results"] = filtered_results[: max(8, max_results * 2)]
        log.info(
            "웹 조사 주체 필터: anchors=%s kept=%d excluded=%d",
            subject_anchors,
            len(raw["results"]),
            len(original_results) - len(filtered_results),
        )
    elif len(raw.get("results") or []) > max(8, max_results * 2):
        raw["results"] = list(raw["results"])[: max(8, max_results * 2)]
    log.info("웹 조사 결과 %d건 (backend=%s)",
             len(raw.get("results") or []), raw.get("backend"))
    return distill_research(llm, topic, raw)
