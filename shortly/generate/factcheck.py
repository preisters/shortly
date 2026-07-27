"""생성 대본 최종 팩트체크 — 특히 시점·주체 혼동을 잡아 수정한다.

웹 조사 스니펫의 '올해 초' 같은 상대 시점이 다른 회사 이야기에 붙어 있거나,
재조명 보도 시점을 사건 발생 시점으로 오인하는 경우가 잦다.
생성 직후 한 번 더 검증·수정한다.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ..llm.base import LLMClient, LLMError
from ..utils import log
from .research import web_search

_TEMPORAL = re.compile(
    r"(올해|작년|재작년|최근|요즘|이달|지난달|며칠\s*전|"
    r"\d{1,2}\s*월|"
    r"\d{4}\s*년|"
    r"\d{2,4}\s*년대|"
    r"초반|중반|후반|"
    r"며칠|몇\s*년|수년|"
    r"절호의\s*기회|불발)"
)
_RELATIVE_ONLY = re.compile(
    r"(올해|작년|재작년|최근|요즘|이달|지난달|며칠\s*전|"
    r"며칠\s*전|몇\s*년\s*전|수년\s*전)"
)
_YEAR = re.compile(r"(19|20)\d{2}")


def _script_lines(script: Dict) -> List[str]:
    lines: List[str] = []
    for seg in script.get("segments") or []:
        if isinstance(seg, dict):
            t = (seg.get("text") or "").strip()
            if t:
                lines.append(t)
    return lines


def _has_temporal_claim(script: Dict) -> bool:
    blob = " ".join(_script_lines(script))
    blob += " " + str(script.get("title") or "") + " " + str(script.get("hook") or "")
    return bool(_TEMPORAL.search(blob))


def _format_research(research: Optional[Dict]) -> str:
    if not research:
        return "(조사 메모 없음)"
    parts: List[str] = []
    if research.get("hard_facts"):
        parts.append("[하드 팩트]\n" + "\n".join(
            f"- {x}" for x in research["hard_facts"]))
    if research.get("drama_beats"):
        parts.append("[드라마 비트]\n" + "\n".join(
            f"- {x}" for x in research["drama_beats"]))
    if research.get("avoid_claims"):
        parts.append("[쓰지 말 것]\n" + "\n".join(
            f"- {x}" for x in research["avoid_claims"]))
    results = research.get("results") or []
    if results:
        snips = []
        for i, r in enumerate(results[:10], 1):
            snips.append(
                f"{i}. {r.get('title', '')}\n"
                f"   {r.get('snippet', '')}\n"
                f"   ({r.get('url', '')})"
            )
        parts.append("[원 스니펫]\n" + "\n".join(snips))
    return "\n\n".join(parts) if parts else "(조사 메모 비어 있음)"


def _subject_seed(topic: str, script: Dict) -> str:
    title = (script.get("title") or script.get("hook") or "").strip()
    seed = (topic or "").strip() or title
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,}|[가-힣]{2,}", seed)
    stop = {"때문에", "일본", "시장", "포기", "독도", "수출", "제품", "관련"}
    brand = next((t for t in tokens if t not in stop), "")
    return brand or (tokens[0] if tokens else seed[:40])


def _extra_temporal_search(topic: str, script: Dict,
                           max_results: int = 6) -> List[Dict]:
    """사건 발생 시점 확인용 추가 검색."""
    brand = _subject_seed(topic, script)
    queries = [
        f"{brand} 일본 수출 포기 언제",
        f"{brand} 독도 수출 연도",
        f"{brand} 일본 수출 2010 OR 2011 OR 2012 OR 2013",
    ]
    merged: List[Dict] = []
    seen = set()
    for q in queries:
        for row in web_search(q, max_results=3):
            key = (row.get("url") or "").split("?")[0] or row.get("title") or ""
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= max_results:
                return merged
    return merged


def _heuristic_flags(script: Dict, research: Optional[Dict]) -> List[str]:
    """규칙 기반 위험 신호 (LLM 보조)."""
    flags: List[str] = []
    lines = _script_lines(script)
    research_blob = _format_research(research)
    for i, line in enumerate(lines, 1):
        if _RELATIVE_ONLY.search(line) and not _YEAR.search(line):
            flags.append(
                f"문장{i}: 상대 시점('{_RELATIVE_ONLY.search(line).group(0)}')이 "
                "절대 연도 없이 쓰임 - 보도 시점과 사건 시점을 혼동하기 쉬움"
            )
        if "절호의 기회" in line or "수출이 불발" in line:
            # 아시아경제 스니펫은 올바름 이야기인데 성경식품에 자주 붙음
            if "올바름" in research_blob and (
                    "성경" in " ".join(lines) or "성경" in research_blob):
                flags.append(
                    f"문장{i}: '절호의 기회/수출 불발' 표현은 다른 회사(올바름) "
                    "기사에서 자주 나오므로 주체 혼동 여부 확인 필요"
                )
    # hard_facts 자체에 상대 시점이 섞여 있으면 경고
    for hf in (research or {}).get("hard_facts") or []:
        if _RELATIVE_ONLY.search(hf) and not _YEAR.search(hf):
            flags.append(
                f"조사 hard_fact에 상대 시점만 있음(절대연도 없음): {hf[:80]}"
            )
    return flags


def factcheck_script(
    llm: Optional[LLMClient],
    script: Dict,
    research: Optional[Dict] = None,
    topic: str = "",
    *,
    force: bool = False,
) -> Dict:
    """대본을 조사 메모·추가 시점 검색으로 재검증하고 필요 시 수정한다.

    반환: {
      script, changed, issues, fixes, extra_results, skipped
    }
    """
    out: Dict = {
        "script": script,
        "changed": False,
        "issues": [],
        "fixes": [],
        "extra_results": [],
        "skipped": False,
    }
    if not script or not isinstance(script.get("segments"), list):
        out["skipped"] = True
        return out

    flags = _heuristic_flags(script, research)
    need = force or bool(flags) or _has_temporal_claim(script)
    # 주제 모드에서는 거의 항상 한 번 돌린다(시점 환각이 치명적이므로)
    if not need and research and (research.get("results") or research.get("hard_facts")):
        need = True
    if not need:
        out["skipped"] = True
        return out

    extra = _extra_temporal_search(topic, script)
    out["extra_results"] = extra
    extra_txt = ""
    if extra:
        extra_txt = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')} ({r.get('url', '')})"
            for r in extra
        )

    if llm is None or not getattr(llm, "available", False):
        # LLM 없으면 휴리스틱만: 상대 시점 문장을 완화 표현으로 못 고침 → 이슈만 기록
        out["issues"] = flags or ["LLM 없음 — 자동 수정 스킵"]
        log.info("팩트체크: LLM 없음, 이슈 %d건만 기록", len(out["issues"]))
        return out

    system = (
        "너는 쇼츠 대본 팩트체커다. 목표: 환각·주체 혼동·시점 오류만 고친다.\n"
        "규칙:\n"
        "1) 시점: '올해/작년/최근/이달' 같은 상대 시점은 스니펫에 "
        "절대 연도(예: 2011년)가 명확히 그 주체·사건에 붙어 있을 때만 유지. "
        "보도일(예: 2024년 기사)과 사건 발생일을 혼동하지 마라. "
        "재조명·품절·돈쭐 보도 시점을 수출 포기 시점으로 쓰지 마라.\n"
        "2) 주체: 한 기사에 회사 A·B가 같이 나오면 A의 시점/사건을 B에 붙이지 마라. "
        "예: '올해 초 수출 불발'이 올바름 이야기면 성경식품에 쓰지 마라.\n"
        "3) 연도가 불확실하면 연도·'올해 초'를 빼고 "
        "'독도 표기 때문에 일본 수출을 포기했다'처럼 확인된 골격만 남겨라.\n"
        "4) 말투·훅 임팩트·세그먼트 수·intent 는 최대한 유지. "
        "재미를 위해 새 사건을 만들지 마라.\n"
        "5) 문제 없으면 changed=false, script는 원문 그대로.\n"
        "JSON만 출력."
    )
    schema = (
        '{"changed": boolean, '
        '"issues": string[], '
        '"fixes": string[], '
        '"script": {"title": string, "hook": string, '
        '"segments": [{"index": number, "text": string, "intent": string}]}}'
    )
    user = (
        f"[주제/입력]\n{(topic or '')[:1500]}\n\n"
        f"[생성 대본 JSON]\n{json.dumps(script, ensure_ascii=False)[:3500]}\n\n"
        f"[조사 메모·스니펫]\n{_format_research(research)[:4500]}\n\n"
        f"[시점 추가 검색]\n{extra_txt[:2500] or '(없음)'}\n\n"
        f"[규칙 기반 위험 신호]\n"
        + ("\n".join(f"- {x}" for x in flags) if flags else "(없음)")
        + f"\n\n스키마: {schema}"
    )
    try:
        data = llm.complete_json(system, user, temperature=0.05, max_tokens=3500)
    except LLMError as exc:
        log.info("팩트체크 LLM 실패: %s", exc)
        out["issues"] = flags + [f"팩트체크 LLM 실패: {exc}"]
        return out

    if not isinstance(data, dict):
        out["issues"] = flags + ["팩트체크 응답 형식 오류"]
        return out

    issues = [str(x).strip() for x in (data.get("issues") or []) if str(x).strip()]
    fixes = [str(x).strip() for x in (data.get("fixes") or []) if str(x).strip()]
    changed = bool(data.get("changed"))
    new_script = data.get("script")
    if changed and isinstance(new_script, dict) and isinstance(
            new_script.get("segments"), list) and new_script["segments"]:
        # 원본 메타 유지
        merged = dict(script)
        merged["title"] = (new_script.get("title") or script.get("title") or "").strip()
        merged["hook"] = (new_script.get("hook") or script.get("hook") or "").strip()
        if merged["title"] and not merged["hook"]:
            merged["hook"] = merged["title"]
        if merged["hook"] and not merged["title"]:
            merged["title"] = merged["hook"]
        segs = []
        for i, seg in enumerate(new_script["segments"], 1):
            if not isinstance(seg, dict):
                continue
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            segs.append({
                "index": int(seg.get("index") or i),
                "text": text,
                "intent": (seg.get("intent") or "").strip() or "beat",
            })
        if segs:
            merged["segments"] = segs
            merged["_source"] = (script.get("_source") or "llm") + "+factcheck"
            out["script"] = merged
            out["changed"] = True
        else:
            changed = False
            issues.append("수정 세그먼트가 비어 원본 유지")
    else:
        changed = False

    out["changed"] = changed
    out["issues"] = issues or flags
    out["fixes"] = fixes
    log.info(
        "팩트체크 완료 changed=%s issues=%d fixes=%d",
        changed, len(out["issues"]), len(out["fixes"]),
    )
    return out
