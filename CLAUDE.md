# CLAUDE.md — 리뷰 통합 시트

이 폴더 전용 작업 지침이다. 전체 아키텍처·배경은 루트 CLAUDE.md와 상위 메모리
(`project_review_integration_sheet`, `project_review_reclassify_step_design`)를 참고한다.

## 이 폴더의 두 파이프라인

1. **`main.py`** — 매일 13:00 KST GitHub Actions가 완전 무인으로 실행하는 수집
   파이프라인(collectors → dedup → `classifier/rule_based.py` → Sheets append).
   수집 채널·스케줄·Actions 워크플로우는 설계상 고정이다. 적재(중복 제거 → 분류 → append)는
   `review_ingest.ingest` 공통 함수로, 크롬 확장 브리지와 같은 경로를 쓴다(2026-09-29).
2. **`reclassify.py`** — Claude Code 세션이 켜져 있을 때만 도는 검수(재분류)
   파이프라인. `method=="lexicon"`인 시트 행(규칙기반 감성분류)을 Claude가 다시
   판단해 `sentiment`/`confirmed`/`method` 컬럼을 갱신하고, 긍정·부정 리뷰(별점 판정 포함)에는
   주요 칭찬/불만 포인트를 `key_points` 컬럼에 적는다(대시보드 긍정·부정 키워드 TOP 5의 원천,
   2026-09-29). 아래는 이 파이프라인 전용 절차다.

## `/loop` 주기마다 수행할 절차

```
python reclassify.py --export
    → reclassify_work/[재분류요청].json 생성 (후보 0건이면 파일 없이 종료)

Claude Code가 JSON을 읽고 판단        ← Claude의 몫
    → reclassify_work/[재분류결과].json 작성

python reclassify.py --apply
    → 결과를 시트에 반영, reclassify_work/reclassify_log.jsonl에 변경 로그 추가
```

`GOOGLE_SHEET_ID`/`GCP_SA_KEY` 환경변수가 필요하다(`main.py`와 동일).
`[재분류요청].json`이 없으면(후보 0건) 그 주기는 판단 없이 건너뛴다.

## `[재분류요청].json` 형식

```json
{
  "generated_at": "2026-09-21T10:00:00+09:00",
  "existing_key_points": {"부정": {"의료진 태도": 24, "대기 시간": 20}, "긍정": {"직원 친절": 30}},
  "candidates": [
    {
      "id": 0,
      "hospital_id": "gangnam_jstar",
      "channel": "네이버",
      "content": "피부에 염증성 트러블 올라올 때마다...",
      "current_sentiment": "부정",
      "current_score": -4,
      "current_matched_words": ["염증", "염증", "스트레스", "친절"],
      "tasks": ["sentiment", "key_points"]
    }
  ]
}
```

## `[재분류결과].json` 형식 (Claude가 작성)

```json
{
  "reviewed_at": "2026-09-21T10:05:00+09:00",
  "results": [
    {
      "id": 0,
      "sentiment": "긍정",
      "confirmed": true,
      "note": "염증/스트레스는 내원 사유 설명이며 전체 흐름은 재방문 의사로 끝남"
    },
    {
      "id": 1,
      "key_points": ["주차"],
      "note": "건물 주차장 입구를 막은 데 대한 불만"
    }
  ]
}
```

- `id`는 요청 파일의 `id`와 그대로 매칭한다(인덱스 매칭 — 실제 시트 행 찾기는
  `reclassify.py`가 `(channel, content)`로 별도 처리한다).
- `sentiment`: `"긍정"|"중립"|"부정"` 중 하나.
- `confirmed`: 항상 `true`로 쓴다. 애매하면 `"중립"`으로 판단하고 `confirmed=true`
  — 이 단계는 사람 대신 최종 판단을 내리는 단계이므로 "판단 보류" 상태를 두지 않는다.
- `tasks`: 후보마다 할 일. `["sentiment", "key_points"]`(규칙기반 행 — 감성 판정, 결과가
  긍정·부정이면 주요 포인트도) 또는 `["key_points"]`(이미 긍정·부정으로 확정된 행 — 감성은 쓰지
  않고 주요 포인트만). `key_points`만인 후보에는 `sentiment`/`confirmed`를 쓰지 않는다.
- `key_points`: 최종 판단이 긍정·부정일 때 쓴다(중립은 쓰지 않음). 아래 "주요 포인트 작성 규칙"을 따른다.
- `note`: 로그에만 남고 시트에는 안 쓴다. 판단 근거를 반드시 적는다.
- `results`에 없는 `id`(판단을 건너뛴 후보)는 다음 주기에 다시 후보로 나온다.

## 판단 기준 (실측된 규칙기반 분류기의 오분류 패턴)

`classifier/rule_based.py`는 형태소분석·문맥 이해 없는 bag-of-words 감성사전
합산 방식이라, 2026-09-21 실측으로 아래 3가지 오분류가 확인되었다. 재분류 시
반드시 이 기준으로 판단한다.

1. **증상·통증 어휘는 문맥으로 구분한다**: 통증/염증/걱정/스트레스 등은 "서비스에
   대한 불만"이 아니라 "내원 사유 설명"일 수 있다(예: "염증 때문에 병원 갔는데
   친절해서 좋았어요"). 후자는 부정 신호로 보지 않는다.
2. **양보구문은 결론(뒤 절)을 따른다**: "아팠지만 참을만했다/괜찮았다" 같은 문장은
   앞 절의 부정 단어가 아니라 뒤 절의 결론으로 감성을 판단한다.
3. **전체 흐름이 우선이다**: 재방문 의사·추천·감사 표현으로 끝나면, 중간에 부정
   단어가 섞여 있어도 전체는 긍정으로 판단한다.

애매하되 위 세 패턴에 해당하지 않으면 `"중립"`으로 판단한다(추측으로 긍정/부정을
단정하지 않는다 — 루트 CLAUDE.md의 "데이터 없음의 정직한 표기" 원칙과 동일선상).

## 주요 포인트 작성 규칙 (`key_points`)

- 그 리뷰에서 **가장 주요한 불만(부정) 또는 칭찬(긍정) 포인트** 1~2개, 각 2~8자 정도의 명사구.
  가장 강하게 말한 것부터 쓴다(부정 예: "주차", "의료진 태도", "대기 시간", "가격" / 긍정 예:
  "직원 친절", "상담 설명", "시술 결과", "시설 청결").
- `existing_key_points`의 **같은 감성 목록**에 같은 뜻의 표현이 있으면 그 표현을 그대로 쓴다
  ("주차"와 "주차 문제"를 따로 만들지 않는다 — TOP 5 집계가 쪼개진다).
- "좋아요", "굿", "최고"처럼 **무엇이 좋은지/나쁜지 없는 리뷰는 `["-"]`**. 감성(칭찬/불만)으로는
  그대로 집계되지만 키워드에는 넣지 않는다(2026-09-29 사용자 결정).
- 병원이 개선에 쓸 수 있게 대상이 드러나게 쓴다("불친절"보다 "상담 태도"/"접수 응대"). 본문이
  그 이상을 말하지 않으면 "불친절"도 된다.
- 본문에 없는 내용을 추측하지 않는다. 구체적 포인트를 찾을 수 없으면 `["-"]`(다시 후보로 나오지
  않고, 대시보드는 집계하지 않는다).

## 주간 특이사항 갱신 (`weekly_notes.json`)

관리자 첫 화면(`?admin=1` "마스터 대시보드")의 "주간 특이사항 요약" 컬럼 원천이다. 2026-10-12
사용자 결정으로 **요청 시 Claude Code가 작성**한다(유료 API 자동 생성 아님). "주간 특이사항 갱신"
요청을 받으면:

1. 시트(공개 CSV 또는 `--export`와 같은 읽기)에서 자사 병원(`hospitals_meta.json` 키)별로 최근 7일
   (오늘 포함) 리뷰를 모은다. 날짜 정규화는 `index.html`의 `normalizeDate`와 같은 규칙을 쓴다.
   재분류(`reclassify.py`)를 먼저 끝내 sentiment/key_points가 확정된 상태에서 쓴다.
2. 지점별로 리뷰를 직접 읽고 1문장 요약을 쓴다. 본문에 없는 내용은 쓰지 않는다(추측 금지). 7일 리뷰가
   없으면 "최근 7일 새 리뷰가 없습니다(마지막 리뷰 M월 D일)."처럼 사실만 쓴다.
3. `flag`: `"risk"`=이번 주 부정 리뷰로 드러난 실제 불만 이슈, `"watch"`=칭찬 리뷰 속 개선 요청
   (예: 대기 시간 언급), `"none"`=특이사항 없음. `count`=7일 리뷰 수.
4. 형식:
   ```json
   {"generated_at": "...+09:00", "period_start": "YYYY-MM-DD", "period_end": "YYYY-MM-DD",
    "notes": {"<hospital_id>": {"flag": "none|watch|risk", "count": 0, "summary": "1문장"}}}
   ```
5. 커밋·push하면 대시보드에 반영된다. 저장소가 public이라 요약도 공개된다(리뷰 원문이 이미 공개
   시트에 있어 노출 수준은 같음). 환자 실명·연락처 등은 요약에 넣지 않는다.

정렬 규칙(`index.html` `buildSummaryRows`): 리스크(부정 급증·`risk`, 또는 조회 기간 리뷰 5건 이상에서
부정 비율 10% 이상) → 주의(`watch`) → 부정 비율 순. 포털 바로가기는 `config/hospitals_config.json`의
채널 주소를 그대로 쓴다(구글은 `google_place_id`로 지도 링크 생성).

## 자사 병원별 인근 경쟁 병원 선정·수집 (2026-10-13~)

설계서는 `docs/superpowers/specs/2026-10-13-competitor-expansion-design.md`에 있다.

- **기준(사용자 확정)**
  - 같은 행정구역 + 같은 진료과 병원 중에서 고른다. 행정구역은 도 소속이면 시, 특별·광역시면 구다.
  - 카카오맵 검색 상위 노출 45곳을 1차 후보로 받는다. 15곳 이하면 전부, 넘으면 노출 순위와 거리를 반씩 반영한 점수로 상위 15곳을 고른다.
  - 강남제이스타의 22곳(9-22 실측 목록)은 그대로 둔다.
- **자사 병원을 새로 추가했을 때** 아래 순서로 실행한다. 모두 Claude 세션에서 한다.
  ```
  python find_competitors.py search     # 카카오맵 검색(화면 응답 읽기, 차단 징후 시 중단)
  python find_competitors.py select     # 15곳 선정 → competitor_onboarding_work/selected.json
  python find_competitors.py channels   # 네이버·구글 대조(주소+이름 일치만), 중단돼도 이어서 진행
  python find_competitors.py apply      # config/competitors_config.json 갱신(백업 생성)
  python build_meta.py                  # hospitals_meta.json의 병원별 competitors 재생성
  ```
  `test_build_meta.py`가 meta와 config가 어긋나면 실패한다. 커밋 전에 꼭 돌린다.
- **매일 수집 (`main.py`)**
  - 자사 병원은 매일 전부 수집한다.
  - 경쟁 병원은 `COMPETITOR_DAILY_CAPACITY`(80곳)씩 날짜 순번 그룹으로 수집하고, 90분 예산을 넘기면 남은 곳은 건너뛴다(Actions에 `::warning`이 남음). 경고가 반복되면 정원을 낮춘다.
  - 수동 실행할 때 `competitor_group` 입력에 `all`을 넣으면 전체를, 숫자를 넣으면 그 그룹을 수집한다.
- 사용자가 특정 병원의 경쟁 병원을 바꿔 달라고 하면 `competitors_config.json`의 해당 항목 `for_hospitals`를 고친 뒤 `build_meta.py`를 다시 실행한다.

## 경쟁 병원 온보딩 — 네이버 플레이스 ID

> 2026-10-12부터 `m.search.naver.com` 일반 검색은 캡차 없이 열린다. `find_competitors.py channels`가 이 경로로 ID를 찾는다. 아래 수동 절차는 그 방법으로도 못 찾은 곳을 위한 예비 수단이다.

네이버는 스크립트 검색에 캡차를 요구해(2026-09-28 실측) 플레이스 ID 자동 조회가
불가능하다. 우회하지 않는다. 신규 경쟁 병원을 `config/competitors_config.json`에
추가하면(`naver_place_url`은 빈 값) 다음 순서로 채운다.

```
python find_naver_place_ids.py --make-manual naver_place_manual.json
    → naver_place_manual.json + naver_place_manual.html(브라우저 확인 페이지) 생성
사람이 .html을 브라우저로 열어 병원별 ID 확인·입력 → [결과 JSON 복사] → .json 덮어쓰기
python find_naver_place_ids.py --manual naver_place_manual.json   # 형식 검증 후 반영(백업 생성)
```

- ID가 없는 동안에도 수집은 멈추지 않는다(네이버 채널만 건너뛰고 카카오·구글은 수집).
- 예약 URL(`booking.naver.com`)·단축 URL(`naver.me`)은 플레이스 ID가 아니다.
  `collectors/naver.py`가 해당 채널을 실패로 기록하고 Actions에 경고를 띄운다.
- 자사 고객 병원은 온보딩 시 병원에 스마트플레이스 URL을 직접 받는다.

## 네이버 리뷰 보완 수집 — 크롬 확장 연동 (병행)

채널별 수집 방식(2026-09-28 확정):
- **네이버**: `main.py`(Actions) GraphQL 자동 수집을 유지하고, 차단·실패한 병원은 담당자
  브라우저의 크롬 확장("PEI 네이버 리뷰 수집(내부용)")으로 보완한다. 확장은 사용자가 연 탭에서
  동작하고 캡차는 사람이 푼다 — 은폐·우회 코드 없음.
- **카카오맵**: 기존 방식 유지(`KAKAO_SLEEP_RANGE` 긴 대기로 정중하게 수집).
- **구글맵**: 기존 Python(Playwright) 자동 수집 유지.

```
python naver_extension_bridge.py export-targets
    → naver_extension_work/step2_targets.json (확장 batch.html에서 불러오기)
확장으로 수집 → 네이버리뷰수집_<병원명>_<YYYYMMDD>.xlsx 다운로드
python naver_extension_bridge.py import <다운로드 폴더>          # 미리보기
python naver_extension_bridge.py import <다운로드 폴더> --apply  # 시트 반영
```

- `--apply`는 `GOOGLE_SHEET_ID`/`GCP_SA_KEY` 환경변수 필요(`reclassify.py`와 동일).
- 중복 판정은 `dedup.py` 공통 규칙을 따른다: 네이버는 (공백 정규화 본문, 작성일), 한쪽이라도
  작성일을 모르면 본문만 비교. 카카오·구글은 (channel, content) 완전 일치. 확장 엑셀엔 작성자·
  답글 정보가 없어 빈 값으로 둔다.
- 확장도 플레이스 URL이 있어야 수집할 수 있다 — 플레이스 ID 확보는 위 온보딩 절차를 따른다.

## 보안

이 저장소(`review-integration-dashboard`)는 GitHub Pages 무료 호스팅을 위해
**public**이다. `reclassify_work/`에는 실제 고객 리뷰 원문이 그대로 들어가므로
`.gitignore`에 이미 등록되어 있다 — 이 폴더를 절대 커밋하지 않는다.
