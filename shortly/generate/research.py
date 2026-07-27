"""주제 관련 웹 조사 — 대본 생성용 팩트/각도 보강.

기본 백엔드: ddgs / duckduckgo-search (API 키 불필요).
미설치·실패 시 빈 결과로 폴백해 파이프라인은 계속 진행합니다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from ..llm.base import LLMClient, LLMError
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
    core = " ".join(tokens[:4]).strip()

    extras: List[str] = []
    if brand:
        if any(k in seed for k in ("독도", "일본", "수출")):
            extras.extend([
                f"{brand} 독도 일본 수출",
                f"{brand} 일본 수출 포기",
                # 시점 혼동 방지: 발생 연도/재조명 구분용 (우선)
                f"{brand} 일본 수출 포기 언제",
                f"{brand} 독도 수출 연도",
                f"{brand} 독도 포장",
            ])
        extras.extend([f"{brand} 유행", f"{brand} 가격"])
    if core and core != first:
        extras.append(core)
    if "성경김" in first or brand == "성경김":
        extras.insert(0, "성경식품 독도 일본 수출")
        extras.insert(1, "성경식품 독도 포장지")
        extras.insert(2, "성경식품 일본 수출 포기 언제 연도")

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
    rows = _search_ddgs(query, max_results)
    if not rows:
        rows = _search_ddg_html(query, max_results)
    return rows


def collect_raw(topic: str, max_results: int = 8,
                max_queries: int = 4) -> Dict:
    """주제 관련 검색 결과를 모아 중복 제거한다."""
    queries = build_queries(topic, max_queries=max_queries)
    per_q = max(3, max_results // max(1, len(queries) or 1))
    merged: List[Dict] = []
    seen_urls = set()
    seen_titles = set()
    for q in queries:
        for row in web_search(q, max_results=per_q):
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
            f"{i}. {r.get('title', '')}\n"
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
        "시청 긴장을 만드는 '확인된 갈등'(누가 무엇을 요구/거절/고수했는지)은 "
        "hard_facts 와 drama_beats 에 반드시 넣어라. "
        "스니펫에 '일본 측 요구를 거부', '독도를 지우느니 시장 포기'처럼 나오면 "
        "그걸 avoid 로 올리지 말고 hard_facts/drama_beats 로 올려라. "
        "avoid 에는 스니펫에 없는 세부 대화문·금액·날짜·주체 뒤집기·'인정하라' 창작만 넣는다. "
        "스니펫에 없는 인과를 보강하지 마라. soft_angles 는 분위기만.\n"
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
        '{"hard_facts": string[](스니펫 근거 사건·제품·절대연도 가능하면 포함, 주체 명시), '
        '"drama_beats": string[](스니펫 근거 갈등/대치/한 방), '
        '"soft_angles": string[](분위기 과장만), '
        '"curiosity_hooks": string[](근거 있는 궁금증), '
        '"avoid_claims": string[](스니펫에 없는 창작·상대시점 단정·주체혼동)}'
    )
    user = (
        f"[사용자 주제/메모]\n{topic[:2000]}\n\n"
        f"[웹 검색 스니펫 — 여기 없는 내용은 사실이 아님]\n{raw_txt[:4500]}\n\n"
        f"스키마: {schema}"
    )
    try:
        data = llm.complete_json(system, user, temperature=0.1, max_tokens=2500)
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
            base[key] = [str(x).strip() for x in val if str(x).strip()]

    base["hard_facts"] = _enrich_conflict_facts(base["hard_facts"], raw)
    base["hard_facts"], base["avoid_claims"] = _sanitize_temporal_facts(
        base["hard_facts"], base["avoid_claims"])
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


def research_topic(topic: str, llm: Optional[LLMClient] = None,
                   max_results: int = 8, max_queries: int = 4) -> Dict:
    """주제 웹 조사 + (가능하면) LLM 요약. 실패해도 빈 구조 반환."""
    topic = (topic or "").strip()
    if not topic:
        return {
            "queries": [], "results": [], "backend": "none",
            "hard_facts": [], "drama_beats": [], "soft_angles": [],
            "curiosity_hooks": [], "avoid_claims": [],
            "brief_text": "", "thin": True,
        }
    log.info("웹 조사 시작: %s", topic[:60])
    raw = collect_raw(topic, max_results=max_results, max_queries=max_queries)
    log.info("웹 조사 결과 %d건 (backend=%s)",
             len(raw.get("results") or []), raw.get("backend"))
    return distill_research(llm, topic, raw)
