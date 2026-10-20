"""규칙 기반 감성분류 엔진 (유료 API 미사용 — CLAUDE.md 원칙).

1) 별점이 있으면(카카오·구글) 별점만으로 즉시 확정한다.
2) 별점이 없으면(네이버) KNU 감성사전+병원 특화 사전으로 텍스트를 스캔해
   점수를 합산한다. 부정어("안", "~하지 않다")가 붙으면 부호를 반전한다.
3) 점수 절댓값이 임계값 미만이면 무리하게 단정하지 않고 confirmed=False로
   정직하게 표기한다(설계문서 5절 5항).

사전은 길이 2자 이상 항목만 쓴다 — 1글자 항목이 조사/어미와 뒤섞여 생기는
오탐을 실제 시뮬레이션으로 확인했다(2026-09-16, 구현계획_수집기분류엔진 Task 7 참고).
"""

import json
import re
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"

_NEGATIVE_RATING_MAX = 2
_POSITIVE_RATING_MIN = 4

_CONFIRM_THRESHOLD = 2

_PRE_NEGATION_RE = re.compile(r"(안|못)\s*$")
_POST_NEGATION_RE = re.compile(r"^\s*(하지|치|지)?\s*(않|안)")
# "부담 없고", "걱정은 전혀 없었", "통증도 없이"처럼 감성 단어 뒤에 "없다"가 오면 뜻이 뒤집힌다
# (2026-09-28, "부담 없고 괜찮네요" 긍정 후기가 "부담"(-2)만 잡혀 부정 확정되던 사례).
_POST_ABSENCE_RE = re.compile(r"^\s*(이|가|은|는|도|이나)?\s*(전혀|하나도|별로|딱히)?\s*없")

# KNU 사전에 그 자체로 실려 있지만, 실제로는 독립된 감성 단어가 아니라
# 동사 활용형 어미 조각이라 무관한 문맥에서 오탐을 일으키는 항목들.
# 형태소 분석기 없이는 일반화된 해결이 어려워 실제 오분류가 확인된 항목만
# 개별 제외한다(2026-09-16, "눈이 많이 처져서 고민하다가..." 긍정 후기가
# "처져서"의 "져서"(-1)+"걱정"(-2)+"ㅎㅎ"(+1)=score -2로 부정 확정되던 사례).
_EXCLUDED_WORDS = {"져서"}


def _load_lexicon():
    with open(_DATA_DIR / "knu_sentiment_lexicon.json", encoding="utf-8") as f:
        lexicon = json.load(f)
    with open(_DATA_DIR / "hospital_domain_lexicon.json", encoding="utf-8") as f:
        domain = json.load(f)
    lexicon.update(domain)  # 병원 특화 사전이 KNU 기본값을 덮어씀
    return {
        word: polarity
        for word, polarity in lexicon.items()
        if len(word) >= 2 and word not in _EXCLUDED_WORDS
    }


_LEXICON = _load_lexicon()
_WORDS_BY_LENGTH_DESC = sorted(_LEXICON, key=len, reverse=True)


def _score_text(content):
    """사전 단어를 긴 것부터 찾아 겹치지 않게 매칭하고, 부정어 반전을 적용해
    점수를 합산한다."""
    matches = []
    for word in _WORDS_BY_LENGTH_DESC:
        start = 0
        while True:
            idx = content.find(word, start)
            if idx == -1:
                break
            matches.append((idx, idx + len(word), word))
            start = idx + len(word)
    matches.sort(key=lambda m: m[0])

    covered = [False] * len(content)
    score = 0
    matched_words = []
    for start, end, word in matches:
        if any(covered[start:end]):
            continue
        for i in range(start, end):
            covered[i] = True
        polarity = _LEXICON[word]
        pre = content[max(0, start - 6):start]
        post = content[end:end + 8]
        negated = (bool(_PRE_NEGATION_RE.search(pre)) or bool(_POST_NEGATION_RE.match(post))
                   or bool(_POST_ABSENCE_RE.match(post)))
        if negated:
            polarity = -polarity
        score += polarity
        matched_words.append(word)
    return score, matched_words


def classify(review):
    """review: {"rating": int|None, "content": str, ...}
    반환: {"sentiment", "confirmed", "score", "matched_words", "method"}"""
    rating = review.get("rating")
    if rating is not None:
        # 글 없는 별점 리뷰는 별점과 무관하게 중립(2026-10-20 사용자 결정 — 리뷰 수가 적은 지점에서
        # 사유 없는 1점 하나가 부정 비율을 크게 흔들었다)
        if not str(review.get("content") or "").strip():
            sentiment = "중립"
        elif rating <= _NEGATIVE_RATING_MAX:
            sentiment = "부정"
        elif rating >= _POSITIVE_RATING_MIN:
            sentiment = "긍정"
        else:
            sentiment = "중립"
        return {"sentiment": sentiment, "confirmed": True, "score": None, "matched_words": [], "method": "rating"}

    content = review.get("content") or ""
    score, matched_words = _score_text(content)
    if score >= _CONFIRM_THRESHOLD:
        sentiment, confirmed = "긍정", True
    elif score <= -_CONFIRM_THRESHOLD:
        sentiment, confirmed = "부정", True
    else:
        sentiment, confirmed = "중립", False
    return {"sentiment": sentiment, "confirmed": confirmed, "score": score, "matched_words": matched_words, "method": "lexicon"}
