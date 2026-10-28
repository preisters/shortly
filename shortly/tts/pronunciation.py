"""Conservative, context-aware Korean spelling for the speech copy only.

This is an allowlist, not a general Korean G2P engine. Unknown words, names,
and unrecognised inflections stay intact rather than guessing morphology.
The canonical script/captions must never be replaced with this output.
"""
from __future__ import annotations

import re


# noun: (remaining first coda, liaison onset, isolated spelling, nasal spelling)
_NOUNS = {
    "닭": ("달", "ㄱ", "닥", "당"),
    "흙": ("흘", "ㄱ", "흑", "흥"),
    "값": ("갑", "ㅆ", "갑", "감"),
    "몫": ("목", "ㅆ", "목", "몽"),
    "삯": ("삭", "ㅆ", "삭", "상"),
}
_ONSETS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_PARTICLES = ("", "이", "을", "은", "에", "에서", "에게", "으로", "으로는",
              "이라", "이라고", "이라는", "이라면", "이랑", "이나", "처럼",
              "까지", "부터", "보다", "과", "과는", "도", "만", "만큼")


def _noun_forms() -> dict[str, str]:
    forms = {}
    for noun, (left, onset, isolated, nasal) in _NOUNS.items():
        for suffix in _PARTICLES:
            if not suffix:
                spoken = isolated
            elif suffix[0] in "이을은에으":
                # Split the cluster only across a recognised particle, never
                # across whitespace or a lexical compound such as 닭 앞.
                offset = ord(suffix[0]) - 0xAC00
                syllable = chr(0xAC00 + _ONSETS.index(onset) * 588 + offset % 588)
                spoken = left + syllable + suffix[1:]
            elif suffix.startswith("만"):
                spoken = nasal + suffix
            else:
                first = {"과": "꽈", "도": "또", "부": "뿌", "보": "뽀"}.get(suffix[0], suffix[0])
                spoken = isolated + first + suffix[1:]
            forms[noun + suffix] = spoken
    return forms


_FORMS = _noun_forms()
_FORMS.update({
    "닭고기": "닥꼬기", "닭갈비": "닥깔비", "닭발": "닥빨",
    "읽다": "익따", "읽고": "일꼬", "읽어": "일거", "읽은": "일근",
    "읽는": "잉는", "읽지": "익찌",
    "맑다": "막따", "맑고": "말꼬", "맑은": "말근",
    "밝다": "박따", "밝고": "발꼬", "밝은": "발근",
    "늙다": "늑따", "늙고": "늘꼬", "늙은": "늘근",
    "앉다": "안따", "앉고": "안꼬", "앉아": "안자", "앉은": "안즌",
    "앉는": "안는",
    "삶다": "삼따", "삶고": "삼꼬", "삶아": "살마", "삶은": "살믄",
    "젊다": "점따", "젊고": "점꼬", "젊은": "절믄",
    "얇다": "얄따", "얇고": "얄꼬", "얇은": "얄븐",
    "짧다": "짤따", "짧고": "짤꼬", "짧은": "짤븐",
    "넓다": "널따", "넓고": "널꼬", "넓은": "널븐",
    "밟다": "밥따", "밟고": "밥꼬", "밟아": "발바", "밟은": "발븐",
    "없다": "업따", "없고": "업꼬", "없어": "업써", "없는": "엄는",
    "많다": "만타", "많고": "만코", "많아": "마나", "많은": "마는",
    "많이": "마니", "않다": "안타", "않고": "안코", "않아": "아나",
    "싫다": "실타", "싫고": "실코", "싫어": "시러",
})
# Recognised noun compounds can take the same common particles. Do not apply
# these rewrites inside an unrelated proper noun or an unknown longer token.
for _word in ("닭고기", "닭갈비", "닭발"):
    for _particle in _PARTICLES:
        _FORMS.setdefault(_word + _particle, _FORMS[_word] + _particle)

# Reviewed lexical tensification, not a blanket rewrite of every 가/價.
# KBS WORLD Korean, '문구의 발음' (2011-12-28):
# https://world.kbs.co.kr/service/contents_view.htm?lang=k&board_seq=229609
# These are the price/nutritional-value readings. Homographs in historical or
# literary scripts still need editorial review; this is not semantic G2P.
_VALUE_NOUNS = {"원가": "원까", "정가": "정까", "영양가": "영양까"}
_VOWEL_NOUN_PARTICLES = (
    "", "가", "를", "는", "의", "에", "에서", "에게", "로", "로는",
    "라고", "라는", "라면", "랑", "나", "처럼", "까지", "부터", "보다",
    "와", "와는", "도", "만", "만큼",
)
for _word, _spoken in _VALUE_NOUNS.items():
    for _particle in _VOWEL_NOUN_PARTICLES:
        _FORMS[_word + _particle] = _spoken + _particle

_TOKEN = re.compile("[가-힣]+")
# Reviewed speech-only exception: Typecast can insert an unnatural phrase
# break here. Do not generalise this to all dependent nouns or strip spaces
# globally; punctuation and deliberate line breaks must still signal pauses.
_FLOW_PHRASE = re.compile(r"(?<![가-힣])들렸던[ \t]+거임(?![가-힣])")


def korean_speech_text(text: str) -> str:
    """Return a speech copy; preserve unknowns and unreviewed spacing.

Examples: 닭 → 닥, 닭에 → 달게, 닭만 → 당만, 읽고 → 일꼬.
Exact token matching also keeps the transform idempotent (값이 → 갑씨).
"""
    source = _FLOW_PHRASE.sub("들렸던거임", text)
    return _TOKEN.sub(lambda match: _FORMS.get(match[0], match[0]), source)
