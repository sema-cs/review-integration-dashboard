import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from classifier.rule_based import classify


def test_rating_negative_confirmed():
    result = classify({"rating": 1, "content": "아무거나"})
    assert result == {"sentiment": "부정", "confirmed": True, "score": None, "matched_words": [], "method": "rating"}
    print("PASS: test_rating_negative_confirmed")


def test_rating_positive_confirmed():
    result = classify({"rating": 5, "content": "아무거나"})
    assert result["sentiment"] == "긍정" and result["confirmed"] is True and result["method"] == "rating"
    print("PASS: test_rating_positive_confirmed")


def test_rating_neutral_confirmed():
    result = classify({"rating": 3, "content": "아무거나"})
    assert result["sentiment"] == "중립" and result["confirmed"] is True
    print("PASS: test_rating_neutral_confirmed")


def test_text_positive_confirmed():
    result = classify({"rating": None, "content": "정말 친절하고 좋았어요"})
    assert result["sentiment"] == "긍정", f"실제: {result}"
    assert result["confirmed"] is True
    # 2026-09-28 병원 특화 사전에 "좋았"(+1) 추가로 2 -> 3
    assert result["score"] == 3
    assert result["matched_words"] == ["친절", "좋았"]
    print("PASS: test_text_positive_confirmed")


def test_text_negative_confirmed_domain_word():
    result = classify({"rating": None, "content": "직원이 너무 불친절해요"})
    assert result["sentiment"] == "부정", f"실제: {result}"
    assert result["confirmed"] is True
    assert result["score"] == -2
    assert result["matched_words"] == ["불친절"]
    print("PASS: test_text_negative_confirmed_domain_word")


def test_negation_prefix_flips_polarity():
    # "안 친절해요" — 긍정어 "친절" 앞에 부정어 "안"이 있으면 반전돼야 한다
    result = classify({"rating": None, "content": "직원이 안 친절해요"})
    assert result["sentiment"] == "부정", f"실제: {result}"
    assert result["confirmed"] is True
    assert result["score"] == -2
    print("PASS: test_negation_prefix_flips_polarity")


def test_negation_suffix_flips_polarity():
    # "친절하지 않아요" — 긍정어 뒤에 "~하지 않다" 패턴이 오면 반전돼야 한다
    result = classify({"rating": None, "content": "친절하지 않아요"})
    assert result["sentiment"] == "부정", f"실제: {result}"
    assert result["confirmed"] is True
    assert result["score"] == -2
    print("PASS: test_negation_suffix_flips_polarity")


def test_domain_phrase_wait_time():
    result = classify({"rating": None, "content": "대기시간이 길어서 힘들었어요"})
    assert result["sentiment"] == "부정", f"실제: {result}"
    assert result["confirmed"] is True
    assert result["score"] == -4
    print("PASS: test_domain_phrase_wait_time")


def test_ambiguous_text_marked_unconfirmed():
    # 사전에 매칭되는 단어가 거의 없거나 상쇄되는 문장은 무리하게 단정하지 않는다
    result = classify({"rating": None, "content": "네이버 지도 통해 예약하고 방문했습니다 진료 시간 확인 후 왔어요"})
    assert result["confirmed"] is False, f"실제: {result}"
    assert result["sentiment"] == "중립"
    print("PASS: test_ambiguous_text_marked_unconfirmed")


def test_droop_verb_ending_not_treated_as_sentiment_word():
    # 실제 오분류 사례(2026-09-16, 강남제이스타의원): 명백한 긍정 후기인데
    # "처져서"의 활용형 어미 조각 "져서"가 KNU 사전에 독립 단어(-1)로 실려
    # "걱정"(-2)과 합산돼 score -2로 부정 확정됐었다. "져서"를 제외 목록에
    # 넣어 더는 매칭되지 않아야 한다.
    content = (
        "눈이 많이 처져서 고민하다가 수술했어요. 처음엔 붓기 때문에 걱정했는데 "
        "자리 잡고 나니 훨씬 또렷하고 자연스러워졌어요. 진작 할걸 그랬네요ㅎㅎ"
    )
    result = classify({"rating": None, "content": content})
    assert "져서" not in result["matched_words"], f"실제: {result}"
    assert result["sentiment"] != "부정", f"실제: {result}"
    print("PASS: test_droop_verb_ending_not_treated_as_sentiment_word")


def test_absence_after_negative_word_flips_to_positive():
    # 2026-09-28 실제 오분류 유형: "부담 없고 괜찮네요" 긍정 후기가 "부담"(-2)만 잡혀
    # 부정 확정됐었다. 부정 단어 뒤 "없다"는 뜻을 뒤집어야 한다(문장은 재구성한 예시).
    result = classify({"rating": None, "content": "비용이 적당했고 권유도 없어서 부담 없고 괜찮았어요"})
    assert result["sentiment"] == "긍정" and result["confirmed"] is True, f"실제: {result}"
    # 약한 단어(통증 -1)는 뒤집혀도 +1이라 확정 기준 미만 — 부정만 아니면 된다(단정하지 않는 원칙)
    for content in ("시술할 때 통증도 전혀 없었어요", "걱정은 하나도 없었습니다"):
        r = classify({"rating": None, "content": content})
        assert r["sentiment"] != "부정" and r["score"] > 0, f"{content!r} 실제: {r}"
    print("PASS: test_absence_after_negative_word_flips_to_positive")


def test_absence_after_positive_word_flips_to_negative():
    result = classify({"rating": None, "content": "직원들 친절은 전혀 없었어요"})
    assert result["sentiment"] == "부정", f"실제: {result}"
    print("PASS: test_absence_after_positive_word_flips_to_negative")


def test_common_positive_stems():
    result = classify({"rating": None, "content": "가격도 적당하고 결과도 좋았습니다"})
    assert result["sentiment"] == "긍정" and result["confirmed"] is True, f"실제: {result}"
    assert {"적당", "좋았"} <= set(result["matched_words"]), f"실제: {result}"
    # 기존 부정어 규칙과 함께 동작해야 한다
    r = classify({"rating": None, "content": "결과는 안 좋았고 가격도 적당하지 않았어요"})
    assert r["sentiment"] == "부정", f"실제: {r}"
    print("PASS: test_common_positive_stems")


def test_rating_without_text_is_neutral():
    # 2026-10-20 사용자 결정: 글 없는 별점 리뷰는 별점과 무관하게 중립
    for rating in (1, 5):
        for content in ("", "   "):
            result = classify({"rating": rating, "content": content})
            assert result == {"sentiment": "중립", "confirmed": True, "score": None, "matched_words": [], "method": "rating"}, result
    print("PASS: test_rating_without_text_is_neutral")


if __name__ == "__main__":
    test_rating_without_text_is_neutral()
    test_absence_after_negative_word_flips_to_positive()
    test_absence_after_positive_word_flips_to_negative()
    test_common_positive_stems()
    test_rating_negative_confirmed()
    test_rating_positive_confirmed()
    test_rating_neutral_confirmed()
    test_text_positive_confirmed()
    test_text_negative_confirmed_domain_word()
    test_negation_prefix_flips_polarity()
    test_negation_suffix_flips_polarity()
    test_domain_phrase_wait_time()
    test_ambiguous_text_marked_unconfirmed()
    test_droop_verb_ending_not_treated_as_sentiment_word()
