# 마스터 대시보드 기간 지정 + 심각도 정렬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 마스터 대시보드에서 기간을 직접 지정하고, 그 기간의 긍/부정 비율·자동 특이사항을 보여주며, Claude가 판정한 심각 부정 리뷰가 있는 병원을 맨 위로 정렬한다.

**Architecture:** 시트에 `severity` 컬럼을 추가하고 기존 재분류(`reclassify.py`, 매일 13:50 자동)에 "severity" 작업을 붙여 부정 리뷰마다 `심각`/`일반`을 기록한다. 정적 페이지(`index.html`)는 CSV의 severity·key_points를 기간으로 모아 정렬·칩·특이사항을 클라이언트에서 계산한다. UI 디자인은 기존 컴포넌트(기간 pill, date-range-picker, risk-chip, st-note 칸)를 그대로 재사용한다.

**Tech Stack:** Python 3.13(테스트는 각 `test_*.py`를 직접 실행하는 assert+print 방식), gspread, 바닐라 JS 단일 파일 `index.html`, GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-10-19-master-dashboard-period-severity-design.md`

## Global Constraints

- 작업 폴더: `G:\공유 드라이브\07.컨설팅사업부-CS_모듈\AI 자동화\my_ai_company\01_컨설팅\05_CS\팀 프로젝트\리뷰 통합 시트` (git 저장소, `main` 브랜치 직접 커밋·push가 이 프로젝트 관례).
- Python: `C:\Users\SEMACONSULTING\AppData\Local\Programs\Python\Python313\python.exe` (CLAUDE.md의 `C:\Users\SEMA\...`는 이 PC에 없음). 아래에서 `$py`로 표기.
- 유료 API 금지. 심각도 판정은 Claude Code(`claude -p` 또는 세션)만.
- **현재 UI 디자인 유지**: 새 색상·새 컴포넌트 디자인을 만들지 않는다. 기존 클래스 재사용, 추가 CSS는 최소(3줄 이내 수준).
- severity 값은 정확히 `심각` | `일반`, 부정 리뷰에만. 미판정 부정 리뷰는 화면에서 `일반`으로 집계(심각으로 추정 금지).
- 테스트·검증 데이터에 실제 병원명·실제 리뷰 금지(가짜 이름 `테스트A의원` 등).
- `reclassify_work/`, `scheduled_logs/`는 커밋 금지(.gitignore 등록됨).
- 모든 문서·주석·커밋 메시지는 한국어. 커밋 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. 시트에 severity 컬럼이 아직 없을 때(구 CSV) 화면이 깨지지 않고 부정은 전부 `일반`으로 집계 — Task 7 브라우저 확인 (a).
2. Claude 결과의 severity가 `심각`/`일반`이 아닌 값(`높음`, 빈 문자열)이거나 최종 감성이 부정이 아닌데 severity가 온 경우 — 시트에 쓰지 않음, Task 2 테스트.
3. 감성 재판정에서 부정이 됐지만 severity를 빠뜨린 lexicon 행 — 다음 실행에 `["severity"]` 후보로 다시 나옴, Task 2 테스트.
4. 기간 0건 병원/수집 0건 병원이 정렬 맨 아래로 가고 "기간 내 리뷰 없음(마지막 리뷰 M월 D일)"/"수집된 리뷰 없음" 표시 — Task 7 (c).
5. 주간 요약 문장이 선택 기간 밖일 때 표시되지 않음(예: 최근 1주 선택 시 10-06~10-12 요약 미표시) — Task 7 (e).

---

### Task 1: 시트 `severity` 컬럼

**Files:**
- Modify: `sheets_writer.py:10-14`(HEADER), `sheets_writer.py:65`(review_to_row)
- Test: `test_sheets_writer.py`

**Interfaces:**
- Produces: `sheets_writer.HEADER[-1] == "severity"`; `review_to_row(...)`는 길이 `len(HEADER)`, 마지막 값 `""`.

- [ ] **Step 1: 실패 테스트 작성** — `test_sheets_writer.py`의 `test_review_to_row_key_points_blank` 아래에 추가하고 `__main__` 목록 끝에 호출 추가:

```python
def test_header_ends_with_severity_and_row_blank():
    assert sheets_writer.HEADER[-2:] == ["key_points", "severity"], f"실제: {sheets_writer.HEADER}"
    classification = {"sentiment": "부정", "confirmed": True, "score": None, "matched_words": [], "method": "rating"}
    row = sheets_writer.review_to_row("h_a", {"channel": "구글", "author": "", "rating": 1, "date": "", "content": "별로"}, classification, "t")
    assert len(row) == len(sheets_writer.HEADER)
    assert row[sheets_writer.HEADER.index("severity")] == ""
    print("PASS: test_header_ends_with_severity_and_row_blank")


def test_ensure_header_adds_severity_to_key_points_era_sheet():
    # key_points까지만 있는 현재 운영 시트 → severity만 뒤에 붙는다
    old = sheets_writer.HEADER[:sheets_writer.HEADER.index("key_points") + 1]
    ws = FakeWorksheet(initial_values=[old, ["h_a"] + [""] * (len(old) - 1)])
    sheets_writer.ensure_header(ws)
    assert ws.values[0] == sheets_writer.HEADER, f"실제: {ws.values[0]}"
    print("PASS: test_ensure_header_adds_severity_to_key_points_era_sheet")
```

- [ ] **Step 2: 실패 확인** — `& $py test_sheets_writer.py` → `AssertionError` (HEADER 끝이 key_points).

- [ ] **Step 3: 구현** — `sheets_writer.py`:

```python
HEADER = [
    "hospital_id", "channel", "author", "rating", "date", "content", "has_reply",
    "sentiment", "confirmed", "score", "matched_words", "method", "collected_at",
    "key_points",  # 부정 리뷰의 주요 불만 포인트(Claude 재분류 단계에서 기록, 2026-09-29 추가)
    "severity",  # 부정 리뷰 심각도 "심각"|"일반"(Claude 재분류 단계에서 기록, 2026-10-20 추가)
]
```

`review_to_row` 반환 리스트 끝:

```python
        "",  # key_points — 재분류 단계에서 채운다
        "",  # severity — 재분류 단계에서 채운다(부정 리뷰만)
    ]
```

기존 두 테스트의 기대 행 끝에 `""` 하나 추가: `test_review_to_row`(`"2026-09-16T00:00:00", "", ""`), `test_review_to_row_with_rating_and_no_matched_words`(`"2026-09-16T00:00:00", "", ""`).

- [ ] **Step 4: 통과 확인** — `& $py test_sheets_writer.py` → 모든 `PASS:` 출력, 예외 없음. 이어서 `& $py test_review_ingest.py; & $py test_naver_extension_bridge.py; & $py test_main.py` → 모두 PASS(행 길이 의존 회귀 확인).

- [ ] **Step 5: 커밋**

```powershell
git add sheets_writer.py test_sheets_writer.py
git commit -m "시트에 severity 컬럼 추가(부정 리뷰 심각도, 재분류 단계에서 기록)`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 재분류에 심각도 작업 추가

**Files:**
- Modify: `reclassify.py:32-48`(`_tasks_for`), `reclassify.py:211-232`(`apply_results` 필드·로그), 모듈 docstring 1~10행에 한 줄
- Test: `test_reclassify.py`

**Interfaces:**
- Consumes: `sheets_writer.HEADER`의 `"severity"` (Task 1)
- Produces: `reclassify.SEVERITY_VALUES = ("심각", "일반")`; `_tasks_for(review)` 반환 규칙:
  - `method == "lexicon"` → `["sentiment", "key_points", "severity"]`
  - 본문 있음 + `sentiment == "부정"` → `(["key_points"] if 포인트 없음) + (["severity"] if severity 없음)`
  - 본문 있음 + `sentiment == "긍정"` + 포인트 없음 → `["key_points"]`
  - 그 외 `[]`
- 결과 JSON 후보 항목에 `"severity": "심각"|"일반"`.

- [ ] **Step 1: 기존 테스트 기대값 갱신 + 새 실패 테스트 작성**

`_row` 헬퍼에 `severity=""` 인자 추가하고 dict에 `"severity": severity` 추가.
`_sheet_row` 헬퍼에 `severity=""` 인자 추가하고 `row[sheets_writer.HEADER.index("severity")] = severity`.

기대값 수정:
- `test_find_candidates_filters_lexicon_only`: `candidates[0]["tasks"] == ["sentiment", "key_points", "severity"]`, `candidates[1]["tasks"] == ["key_points", "severity"]`.
- `test_find_candidates_adds_key_point_task_for_negative_rows`: 기대 `[["key_points", "severity"], ["key_points", "severity"], ["sentiment", "key_points", "severity"]]`.
- `test_find_candidates_skips_rows_with_key_points`: 앞 두 행에 `severity="일반"` 추가(나머지 그대로) → 여전히 `[]`.

새 테스트(파일 끝, `__main__` 목록에도 추가):

```python
def test_find_candidates_severity_task_for_negative_rows():
    existing = [
        _row(content="주차가 불편", sentiment="부정", method="rating", key_points="주차"),            # 포인트 있음, 심각도 없음
        _row(content="시술 후 염증", sentiment="부정", method="claude_review", key_points="부작용", severity="심각"),  # 다 있음
        _row(content="", sentiment="부정", method="rating"),                                          # 본문 없음
        _row(content="친절해요", sentiment="긍정", method="claude_review", key_points="직원 친절"),     # 긍정은 심각도 없음
    ]
    candidates = reclassify.find_candidates(existing)
    assert [(c["content"], c["tasks"]) for c in candidates] == [("주차가 불편", ["severity"])], f"실제: {candidates}"
    print("PASS: test_find_candidates_severity_task_for_negative_rows")


def test_apply_results_writes_severity_for_negative():
    existing = [_row(channel="구글", content="시술 후 염증이 심해졌어요", sentiment="부정", method="rating", matched_words="")]
    ws, updated = _run_apply([_sheet_row("시술 후 염증이 심해졌어요", "부정", "rating")], existing,
                             [{"id": 0, "key_points": ["시술 부작용"], "severity": "심각", "note": "시술 후 악화 주장"}])
    h = sheets_writer.HEADER
    assert updated == 1 and ws.batch_update_calls == 1
    assert [ws.values[1][h.index(f)] for f in ("key_points", "severity")] == ["시술 부작용", "심각"], f"실제: {ws.values[1]}"
    print("PASS: test_apply_results_writes_severity_for_negative")


def test_apply_results_lexicon_to_negative_with_severity():
    existing = [_row(channel="구글", content="원장이 반말하고 소리를 질렀어요", sentiment="중립", method="lexicon", matched_words="")]
    ws, _ = _run_apply([_sheet_row("원장이 반말하고 소리를 질렀어요", "중립", "lexicon")], existing,
                       [{"id": 0, "sentiment": "부정", "confirmed": True, "key_points": ["의료진 태도"], "severity": "심각", "note": ""}])
    h = sheets_writer.HEADER
    assert [ws.values[1][h.index(f)] for f in ("sentiment", "severity")] == ["부정", "심각"], f"실제: {ws.values[1]}"
    print("PASS: test_apply_results_lexicon_to_negative_with_severity")


def test_apply_results_ignores_invalid_or_non_negative_severity():
    existing = [
        _row(channel="구글", content="대기가 길어요", sentiment="부정", method="rating", matched_words="", key_points="대기 시간"),
        _row(channel="구글", content="친절하고 좋아요", sentiment="중립", method="lexicon", matched_words=""),
    ]
    ws, _ = _run_apply([_sheet_row("대기가 길어요", "부정", "rating", key_points="대기 시간"),
                        _sheet_row("친절하고 좋아요", "중립", "lexicon")], existing,
                       [{"id": 0, "severity": "높음", "note": "잘못된 값"},
                        {"id": 1, "sentiment": "긍정", "confirmed": True, "key_points": ["직원 친절"], "severity": "일반", "note": ""}])
    sv = sheets_writer.HEADER.index("severity")
    assert ws.values[1][sv] == "" and ws.values[2][sv] == "", f"실제: {ws.values}"
    assert ws.values[2][sheets_writer.HEADER.index("sentiment")] == "긍정"  # 다른 필드는 정상 반영
    print("PASS: test_apply_results_ignores_invalid_or_non_negative_severity")


def test_negative_without_severity_comes_back_as_severity_candidate():
    # lexicon → 부정으로 바뀌었는데 severity를 빠뜨린 행은 다음 export에 severity 작업으로 다시 나온다
    existing = [_row(channel="구글", content="상담이 성의 없어요", sentiment="중립", method="lexicon", matched_words="")]
    ws, _ = _run_apply([_sheet_row("상담이 성의 없어요", "중립", "lexicon")], existing,
                       [{"id": 0, "sentiment": "부정", "confirmed": True, "key_points": ["상담 태도"], "note": ""}])
    after = sheets_writer.read_existing_reviews(ws)
    assert [c["tasks"] for c in reclassify.find_candidates(after)] == [["severity"]], f"실제: {after}"
    print("PASS: test_negative_without_severity_comes_back_as_severity_candidate")
```

- [ ] **Step 2: 실패 확인** — `& $py test_reclassify.py` → 첫 AssertionError(`filters_lexicon_only` tasks 불일치).

- [ ] **Step 3: 구현** — `reclassify.py`:

```python
_KEY_POINT_SENTIMENTS = ("부정", "긍정")
SEVERITY_VALUES = ("심각", "일반")


def _split_key_points(value):
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def _tasks_for(review):
    """이 행에 Claude가 할 일. 감성 재판정은 규칙기반(lexicon) 행만, 주요 포인트는
    본문이 있고 아직 포인트가 없는 긍정·부정 행(별점 판정 포함), 심각도는 본문이 있고 아직
    심각도가 없는 부정 행 — 감성 작업 행은 판단 결과가 긍정·부정일 수 있어 포인트·심각도 작업도
    같이 받는다(심각도는 결과가 부정일 때만 쓴다). 중립은 포인트·심각도를 쓰지 않는다."""
    if review.get("method") == "lexicon":
        return ["sentiment", "key_points", "severity"]
    if not str(review.get("content") or "").strip():
        return []
    sentiment = review.get("sentiment")
    tasks = []
    if sentiment in _KEY_POINT_SENTIMENTS and not _split_key_points(review.get("key_points")):
        tasks.append("key_points")
    if sentiment == "부정" and str(review.get("severity") or "").strip() not in SEVERITY_VALUES:
        tasks.append("severity")
    return tasks
```

`apply_results`의 `if "key_points" in tasks and points:` 다음에 추가:

```python
        severity = result.get("severity")
        final_sentiment = fields.get("sentiment", candidate["current_sentiment"])
        if "severity" in tasks and severity is not None:
            if severity in SEVERITY_VALUES and final_sentiment == "부정":
                fields["severity"] = severity
            else:
                print(f"경고: id={result['id']} severity={severity!r}(최종 감성 {final_sentiment!r})는 반영하지 않습니다 "
                      f"— 부정 리뷰의 '심각'/'일반'만 씁니다.")
```

로그 dict에 `"severity": result.get("severity", ""),` 추가(`"key_points"` 다음 줄).
모듈 docstring 8행 다음에 한 줄: `부정 리뷰는 심각도(severity: 심각|일반)도 적는다(2026-10-20).`

- [ ] **Step 4: 통과 확인** — `& $py test_reclassify.py` → 모든 PASS. `& $py test_weekly_notes.py` → PASS(회귀).

- [ ] **Step 5: 커밋**

```powershell
git add reclassify.py test_reclassify.py
git commit -m "재분류에 부정 리뷰 심각도(severity) 판정 작업 추가 — 기존 부정 리뷰도 후보로 백필`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 판정 기준 문서 + 자동 실행 프롬프트

**Files:**
- Modify: `CLAUDE.md`(이 폴더): `[재분류요청].json 형식`의 tasks 설명, `[재분류결과].json 형식` 예시·필드 설명, 새 절 `## 심각도 판정 기준 (severity)`
- Modify: `scheduled/daily_job.py:38-44`(`RECLASSIFY_PROMPT`)
- Test: `test_daily_job.py`가 있으면 실행, 없으면 `& $py -c "import sys; sys.path.insert(0,'scheduled'); import daily_job"`로 import 확인

**Interfaces:** 문서가 Claude의 `[재분류결과].json` 작성 규칙이 된다(Task 2의 `"severity"` 키).

- [ ] **Step 1: CLAUDE.md 수정**
  - `[재분류결과].json 형식` 예시의 id 1 항목을 `{"id": 1, "key_points": ["주차"], "severity": "일반", "note": "건물 주차장 입구를 막은 데 대한 불만"}`로.
  - `tasks` 설명 단락을 다음으로 교체:
    `- \`tasks\`: 후보마다 할 일. \`["sentiment", "key_points", "severity"]\`(규칙기반 행 — 감성 판정, 결과가 긍정·부정이면 주요 포인트, 부정이면 심각도도), 그 밖에는 \`key_points\`·\`severity\` 중 필요한 것만(이미 확정된 행). 목록에 없는 항목은 쓰지 않는다.`
  - 필드 설명에 추가: `- \`severity\`: 최종 판단이 부정일 때만 \`"심각"\` 또는 \`"일반"\`. 아래 "심각도 판정 기준"을 따른다. 긍정·중립에는 쓰지 않는다.`
  - `## 주요 포인트 작성 규칙` 다음에 새 절:

```markdown
## 심각도 판정 기준 (`severity`, 2026-10-20~)

마스터 대시보드 정렬(심각 → 일반 부정 → 부정 없음)의 원천이다. 부정 리뷰 본문이 아래 중 하나를
**주장·언급**하면 `"심각"`, 아니면 `"일반"`. 애매하면 `"일반"`(추측으로 심각을 만들지 않는다).

- 시술·치료 후 부작용, 증상 악화, 재수술·재치료 필요
- 의료 과실·오진
- 환불·보상 요구, 법적 대응·신고(보건소·소비자원 등) 언급
- 폭언·모욕·차별적 응대
- 개인정보 유출·사진 무단 사용
- 강한 비난과 함께 방문하지 말라고 권함("절대 가지 마세요" 등)

대기 시간·가격·주차·예약 불편·다소 불친절 같은 불만은 `"일반"`. 본문이 없는 별점 리뷰는 후보로 나오지
않으며 화면에서는 `일반`으로 집계된다.
```

- [ ] **Step 2: 프롬프트 수정** — `RECLASSIFY_PROMPT`의 `` "`주요 포인트 작성 규칙`을 그대로 따라 " `` 부분을 `` "`주요 포인트 작성 규칙`, `심각도 판정 기준`을 그대로 따라 " ``로 바꾸고, 마지막 문장 앞에 `"tasks에 severity가 있고 최종 판단이 부정이면 severity를 반드시 쓴다. "`를 추가.

- [ ] **Step 3: 확인** — `Get-ChildItem scheduled\test_*.py, test_daily_job.py -ErrorAction SilentlyContinue`; 있으면 실행해 PASS, 없으면 위 import 확인 명령이 오류 없이 끝나는지.

- [ ] **Step 4: 커밋**

```powershell
git add CLAUDE.md scheduled/daily_job.py
git commit -m "심각도 판정 기준 문서화 + 매일 자동 재분류 프롬프트에 severity 지시`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 기간 상태 확장(최근 1주) + 마스터 화면 날짜 직접 지정

**Files:**
- Modify: `index.html` — 마크업 360-364(마스터 기간), 397-401(병원 기간), JS 676-692(state/bounds/label), 1234-1270(기간 이벤트), 1584-1600(syncRangeButtons/요약 기간 버튼)

**Interfaces:**
- Produces: `state.range`는 문자열 `"1w"|"1"|"3"|"6"`(기본 `"3"`); `rangeStartDate(range)` → Date; `applyCustomRange(s, e)` → boolean; `resetRangeToDefault()`; `syncRangeInputs()`. 기간이 바뀌면 현재 보이는 화면에 맞게 `rerenderCurrentView()`가 `renderSummary()` 또는 `renderAll()`을 부른다.

- [ ] **Step 1: 마크업** — 두 pill 그룹 맨 앞에 `<button data-range="1w" aria-pressed="false">최근 1주</button>` 추가. 마스터 `#summaryRangeGroup` `</div>` 바로 뒤(같은 `.controls-row` 안, `.master-controls` 앞)에 병원 화면과 같은 구조를 id만 바꿔 추가:

```html
        <div class="date-range-picker">
          <input type="date" id="summaryCustomStart" aria-label="시작일">
          <span>~</span>
          <input type="date" id="summaryCustomEnd" aria-label="종료일">
          <button id="summaryCustomApply" type="button">기간 적용</button>
          <button id="summaryCustomReset" type="button" class="drp-reset" hidden>초기화</button>
        </div>
```

- [ ] **Step 2: 기간 계산** — 676-692를 다음으로:

```javascript
  var state = { mode: "preset", range: "3", customStart: null, customEnd: null, channel: "all", search: "", negOnly: false };

  // range: "1w"(최근 1주) 또는 개월 수 문자열("1"|"3"|"6")
  function rangeStartDate(range) {
    if (range === "1w") { var t = new Date(); t.setDate(t.getDate() - 7); return t; }
    return monthsAgo(parseInt(range, 10));
  }
  function currentWindowBounds() {
    if (state.mode === "custom" && state.customStart && state.customEnd) {
      return { start: new Date(state.customStart + "T00:00:00+09:00"), end: new Date(state.customEnd + "T23:59:59+09:00") };
    }
    return { start: rangeStartDate(state.range), end: new Date() };
  }
  function priorWindowBounds(cur) {
    var lengthMs = cur.end.getTime() - cur.start.getTime();
    var priorEnd = new Date(cur.start.getTime() - 1);
    return { start: new Date(priorEnd.getTime() - lengthMs), end: priorEnd };
  }
  function rangeLabel() {
    if (state.mode === "custom" && state.customStart && state.customEnd) return state.customStart + " ~ " + state.customEnd;
    return state.range === "1w" ? "최근 1주" : "최근 " + state.range + "개월";
  }
```

- [ ] **Step 3: 기간 이벤트 통합** — 1234-1270 블록을 다음으로 교체(병원 화면·마스터 화면이 같은 state·같은 입력값을 공유):

```javascript
  function syncRangeInputs() {
    var b = currentWindowBounds();
    var s = fmtDateInput(b.start), e = fmtDateInput(b.end);
    ["customStart", "summaryCustomStart"].forEach(function (id) { document.getElementById(id).value = state.mode === "custom" ? state.customStart : s; });
    ["customEnd", "summaryCustomEnd"].forEach(function (id) { document.getElementById(id).value = state.mode === "custom" ? state.customEnd : e; });
  }
  function rerenderCurrentView() {
    if (!document.getElementById("adminSummary").hidden) renderSummary(); else renderAll();
  }
  function applyCustomRange(s, e) {
    if (!s || !e || s > e) return false;
    state.mode = "custom"; state.customStart = s; state.customEnd = e;
    syncRangeButtons(); syncRangeInputs(); rerenderCurrentView();
    return true;
  }
  function resetRangeToDefault() {
    state.mode = "preset"; state.range = "3";
    syncRangeButtons(); syncRangeInputs(); rerenderCurrentView();
  }

  document.querySelectorAll("#rangeGroup button, #summaryRangeGroup button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      state.mode = "preset";
      state.range = btn.getAttribute("data-range");
      syncRangeButtons(); syncRangeInputs(); rerenderCurrentView();
    });
  });
  syncRangeInputs();

  document.getElementById("customApply").addEventListener("click", function () {
    applyCustomRange(document.getElementById("customStart").value, document.getElementById("customEnd").value);
  });
  document.getElementById("summaryCustomApply").addEventListener("click", function () {
    applyCustomRange(document.getElementById("summaryCustomStart").value, document.getElementById("summaryCustomEnd").value);
  });
  document.getElementById("customReset").addEventListener("click", resetRangeToDefault);
  document.getElementById("summaryCustomReset").addEventListener("click", resetRangeToDefault);
```

1584-1600(기존 `syncRangeButtons`와 `#summaryRangeGroup` 클릭 핸들러)을 다음으로 교체(클릭 핸들러는 위에서 통합했으므로 삭제):

```javascript
  // 요약 화면과 병원 화면의 기간 버튼이 같은 기간 상태(state)를 보여주게 맞춘다.
  function syncRangeButtons() {
    document.querySelectorAll("#rangeGroup button, #summaryRangeGroup button").forEach(function (btn) {
      var on = state.mode === "preset" && btn.getAttribute("data-range") === state.range;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    document.getElementById("customReset").hidden = state.mode !== "custom";
    document.getElementById("summaryCustomReset").hidden = state.mode !== "custom";
  }
```

`grep -n "state.range\|syncCustomInputsToPreset\|rangeButtons" index.html`로 남은 참조가 없는지 확인(있으면 위 함수로 바꾼다). `syncRangeButtons`는 함수 선언이라 호이스팅되어 앞쪽에서 호출해도 된다.

- [ ] **Step 4: 확인** — 로컬 서버(Task 7 Step 1)로 `?admin=1` 열어 콘솔 에러 없음, '최근 1주'/'최근 1개월' 클릭 시 캡션 `rangeLabel` 변경, 날짜 지정 → '기간 적용' 시 초기화 버튼 표시·병원 탭 전환 후에도 같은 기간 유지 확인.

- [ ] **Step 5: 커밋**

```powershell
git add index.html
git commit -m "마스터 대시보드 날짜 직접 지정 + 최근 1주 프리셋(병원 화면과 기간 공유)`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 심각도 파싱 + 정렬·칩·KPI·필터

**Files:**
- Modify: `index.html` — `toReview`(636-658), 마크업 367-372(필터 옵션), JS 1426-1516(`WEEKLY_NOTES` 주석, `HIGH_NEG_PCT`, `buildSummaryRows`, `renderSummaryKpis`, `summaryRowVisible`), 1546-1547(칩)

**Interfaces:**
- Consumes: Task 4의 `currentWindowBounds()`.
- Produces: 리뷰 객체에 `severe: boolean`(부정이고 severity가 `심각`). `buildSummaryRows()` 행: `{id, name, all, inRange, total, pos, neg, severe, severeList, posPct, negPct, negWords, posWords, topNeg, tier, links}`; `tier` 2=심각 있음, 1=부정 있음, 0=부정 없음. `compareSummaryRows(x, y)`.

- [ ] **Step 1: 파싱** — `toReview`의 return 객체에 `keyPoints` 다음 줄로:

```javascript
      // 부정 리뷰 심각도(Claude 재분류 단계에서 기록). 컬럼이 없거나 미판정이면 일반으로 본다.
      severe: sentiment === "negative" && (raw.severity || "").trim() === "심각",
```

- [ ] **Step 2: 행 계산·정렬** — `var HIGH_NEG_PCT = 10;` 줄 삭제. `buildSummaryRows`를 다음으로 교체:

```javascript
  // 정렬(2026-10-20 사용자 결정): ① 기간 내 심각 부정 리뷰 있는 지점(건수 많은 순)
  // ② 일반 부정 리뷰 있는 지점(부정 건수 → 부정 비율) ③ 부정 없음(긍정 비율 낮은 순 → 리뷰 많은 순).
  // 칭찬 속 개선 요청·부정 급증은 정렬에 쓰지 않는다(긍정뿐인 지점이 위로 오지 않게).
  function compareSummaryRows(x, y) {
    if (y.tier !== x.tier) return y.tier - x.tier;
    if (x.tier === 2 && y.severe !== x.severe) return y.severe - x.severe;
    if (x.tier >= 1) return (y.neg - x.neg) || ((y.negPct || 0) - (x.negPct || 0)) || x.name.localeCompare(y.name);
    var xp = x.posPct == null ? 101 : x.posPct, yp = y.posPct == null ? 101 : y.posPct;  // 기간 리뷰 0건은 맨 아래
    return (xp - yp) || (y.total - x.total) || x.name.localeCompare(y.name);
  }

  function buildSummaryRows() {
    var b = currentWindowBounds();
    return adminHospitalIds().map(function (id) {
      var all = ALL_REVIEWS.filter(function (r) { return r.hospital_id === id; });
      var inRange = all.filter(function (r) { return r.date && d(r.date) >= b.start && d(r.date) <= b.end; });
      var pos = inRange.filter(function (r) { return r.sentiment === "positive"; });
      var negList = inRange.filter(function (r) { return r.sentiment === "negative"; });
      var severeList = negList.filter(function (r) { return r.severe; });
      var negWords = [], posWords = [];
      negList.forEach(function (r) { negWords = negWords.concat(r.keyPoints); });
      pos.forEach(function (r) { posWords = posWords.concat(r.keyPoints); });
      var negPct = inRange.length ? Math.round((negList.length / inRange.length) * 100) : null;
      return {
        id: id, name: META_ALL[id].name || id, all: all, inRange: inRange, total: inRange.length,
        pos: pos.length, neg: negList.length, severe: severeList.length, severeList: severeList, negList: negList,
        posPct: inRange.length ? Math.round((pos.length / inRange.length) * 100) : null, negPct: negPct,
        negWords: negWords, posWords: posWords, topNeg: topCounts(negWords, 2),
        tier: severeList.length ? 2 : (negList.length ? 1 : 0),
        links: portalLinks(id)
      };
    }).sort(compareSummaryRows);
  }
```

- [ ] **Step 3: KPI·필터·칩**
  - `renderSummaryKpis`: `var risk = rows.filter(function (r) { return r.tier === 2; }).length; var negOnly = rows.filter(function (r) { return r.tier === 1; }).length;` 그리고 `kpiBranchesSub` 문구를 `"리스크(심각) " + risk + "곳 · 부정 " + negOnly + "곳"`.
  - `summaryRowVisible`의 switch: `case "flagged": return row.tier > 0;` / `case "quiet": return row.total === 0;` (negative는 그대로).
  - 필터 마크업: `<option value="flagged">리스크·부정 지점</option>`, `<option value="quiet">기간 내 리뷰 없음</option>`.
  - 칩(1546-1547 교체):

```javascript
      var chip = row.tier === 2 ? '<span class="risk-chip risk" title="심각 부정 리뷰 ' + row.severe + '건">리스크</span>'
        : (row.tier === 1 ? '<span class="risk-chip watch" title="부정 리뷰 ' + row.neg + '건">부정</span>' : "");
```

- [ ] **Step 4: 확인** — Task 7 픽스처로 정렬 순서 확인(그 전엔 콘솔 에러 없음만 확인). `grep -n "row.level\|row.note\|HIGH_NEG_PCT\|computeSpike(all)" index.html` → 마스터 요약 코드에 남은 참조 0건(병원 화면의 `computeSpike`는 유지).

- [ ] **Step 5: 커밋**

```powershell
git add index.html
git commit -m "마스터 대시보드 정렬: 심각 부정 → 일반 부정 → 부정 없음(칭찬 속 개선 요청은 정렬 제외)`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 기간 특이사항 칸 (자동 요약 + 조건부 주간 문장)

**Files:**
- Modify: `index.html` — 마크업 386(헤더 문구), CSS 298 다음(최소 2줄), JS `renderSummary`(1527-1573)

**Interfaces:**
- Consumes: Task 5 행 필드(`severeList`, `negList`, `posWords`, `all`, `total`, `neg`, `severe`, `pos`), `PLATFORM_LABEL`, `escapeHtml`, `topCounts`, `currentWindowBounds`, `d`.
- Produces: `periodNoteHtml(row)` → HTML 문자열; `weeklyNoteFor(id)` → 문장 또는 null.

- [ ] **Step 1: 마크업·CSS** — 헤더 `<th class="left">주간 특이사항 요약</th>` → `<th class="left">기간 특이사항</th>`. CSS 298행 다음:

```css
  .st-note details summary { cursor:pointer; color:var(--critical); font-weight:700; }
  .st-note .st-sub { font-size:11.5px; color:var(--text-muted); margin-top:4px; }
```

- [ ] **Step 2: 요약 함수** — `renderSummary` 위에 추가:

```javascript
  function fmtMD(iso) { var p = iso.split("-"); return parseInt(p[1], 10) + "월 " + parseInt(p[2], 10) + "일"; }

  // 프리셋 시작 시각은 "지금 시각" 기준이라, 주간 기간과는 그날 00:00으로 비교한다.
  function startOfDay(dt) { return d(fmtDateInput(dt)); }

  // 주간 문장(weekly_notes.json)은 그 주가 선택 기간 안에 완전히 들어갈 때만 보여준다.
  function weeklyNoteFor(id) {
    if (!WEEKLY_NOTES || !WEEKLY_NOTES.notes || !WEEKLY_NOTES.notes[id]) return null;
    var b = currentWindowBounds();
    if (d(WEEKLY_NOTES.period_start) < startOfDay(b.start)) return null;
    if (d(WEEKLY_NOTES.period_end) > b.end) return null;
    return WEEKLY_NOTES.notes[id].summary;
  }

  function periodNoteHtml(row) {
    var parts = [];
    if (!row.total) {
      var dated = row.all.filter(function (r) { return r.date; }).map(function (r) { return r.date; }).sort();
      parts.push('<span class="st-muted">' + (dated.length ? "기간 내 리뷰 없음(마지막 리뷰 " + fmtMD(dated[dated.length - 1]) + ")" : "수집된 리뷰 없음") + '</span>');
    } else {
      if (row.severe) {
        var sp = topCounts([].concat.apply([], row.severeList.map(function (r) { return r.keyPoints; })), 3).map(function (k) { return k.word; });
        var items = row.severeList.slice().sort(function (a, b) { return a.date < b.date ? 1 : -1; }).map(function (r) {
          return '<li>' + escapeHtml(r.date) + ' · ' + escapeHtml(PLATFORM_LABEL[r.channel] || r.channel) + ' — ' + escapeHtml(r.content || "(본문 없음)") + '</li>';
        }).join("");
        parts.push('<details><summary>심각 ' + row.severe + '건' + (sp.length ? '(' + escapeHtml(sp.join(", ")) + ')' : '') + '</summary><ul>' + items + '</ul></details>');
      }
      var general = row.negList.filter(function (r) { return !r.severe; });
      if (general.length) {
        var gp = topCounts([].concat.apply([], general.map(function (r) { return r.keyPoints; })), 3);
        parts.push('<div>부정 ' + general.length + '건' + (gp.length ? ': ' + gp.map(function (k) { return escapeHtml(k.word) + ' ' + k.count; }).join(", ") : '') + '</div>');
      }
      var praise = topCounts(row.posWords, 2).map(function (k) { return k.word; });
      if (praise.length) parts.push('<div>칭찬: ' + escapeHtml(praise.join(", ")) + '</div>');
      if (!row.neg && !praise.length) parts.push('<div>부정 리뷰 없음(긍정 ' + row.pos + '건)</div>');
    }
    var weekly = weeklyNoteFor(row.id);
    if (weekly) parts.push('<div class="st-sub">주간 요약(' + WEEKLY_NOTES.period_start.slice(5).replace("-", "/") + '~' + WEEKLY_NOTES.period_end.slice(5).replace("-", "/") + '): ' + escapeHtml(weekly) + '</div>');
    return parts.join("");
  }
```

- [ ] **Step 3: renderSummary 갱신**
  - `notesInfo` 변수와 캡션을: `document.getElementById("summaryCaption").textContent = rangeLabel() + " 기준 · 관리 지점 " + rows.length + "곳 · 특이사항은 이 기간 리뷰의 심각도·주요 포인트(Claude 재분류) 자동 집계";`
  - `var note = row.note ? ... : ...;` 줄을 `var note = periodNoteHtml(row);`로.
  - 각주 문구 교체:

```javascript
    document.getElementById("summaryFootnote").textContent =
      "지점명을 누르면 그 지점 상세 대시보드로, N·K·G 버튼은 네이버·카카오·구글 리뷰 원본으로 이동합니다(회색은 미등록). " +
      "정렬: 리스크(기간 내 심각 부정 리뷰, 건수 순) → 부정(일반 부정 리뷰, 건수·비율 순) → 부정 없음(긍정 비율 낮은 순). " +
      "심각도는 Claude가 부정 리뷰마다 판정하며(부작용·의료 과실·환불/법적 대응·폭언 등), 아직 판정 전인 부정 리뷰는 일반으로 집계합니다. " +
      "모든 수치·특이사항은 선택한 조회 기간 기준이고, 주간 요약 문장은 그 주가 조회 기간 안에 있을 때만 함께 표시합니다. " +
      "구글 리뷰는 구글 지도의 \"제한된 보기\"(비로그인 조회 시 대표 리뷰 약 5건만 표시) 때문에 일부만 수집될 수 있습니다.";
```

  - 1426-1431 주석의 "주간 특이사항" 설명을 "기간 특이사항: 기간 내 리뷰의 severity·key_points 자동 집계 + 기간 안의 주간 문장(weekly_notes.json)"으로 고친다.

- [ ] **Step 4: 확인** — Task 7에서 일괄.

- [ ] **Step 5: 커밋**

```powershell
git add index.html
git commit -m "마스터 대시보드 기간 특이사항: 심각/부정 포인트·칭찬 자동 요약 + 심각 원문 펼치기`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 픽스처 브라우저 검증 → 배포 → 실측 확인

**Files:**
- Create (스크래치, 커밋 안 함): `<scratchpad>\fixture.csv`
- Modify: 없음(문제 발견 시 해당 Task 파일)

- [ ] **Step 1: 로컬 서버** — `& $py -m http.server 8765` 를 run_in_background로 저장소 폴더에서 실행.

- [ ] **Step 2: 픽스처 CSV** — 헤더는 `sheets_writer.HEADER`와 같은 순서. `hospitals_meta.json`의 실제 자사 id 3~4개를 hospital_id로 쓰되(이름은 화면에만 나옴), **content는 전부 가짜 문장**. 오늘 기준 날짜(`YYYY-MM-DD`, channel `구글`):
  - id A: 부정 2건(1건 severity `심각` key_points `시술 부작용`, 1건 `일반` `대기 시간`) + 긍정 3건
  - id B: 부정 3건 전부 `일반`(`주차`,`주차`,`가격`) + 긍정 1건
  - id C: 긍정 5건(key_points `직원 친절`) — 부정 0
  - id D: 20일 전 긍정 1건만
  - 나머지 자사 id: 행 없음
  - 별도 파일 `fixture_old.csv`: 같은 데이터에서 `severity` 컬럼 제거

- [ ] **Step 3: Playwright로 CSV 가로채기** — `browser_run_code_unsafe`로 `page.route('**/export?format=csv**', r => r.fulfill({ status: 200, contentType: 'text/csv; charset=utf-8', body: <fixture 내용> }))` 후 `http://localhost:8765/index.html?admin=1` 이동.

- [ ] **Step 4: 확인 항목** (스크린샷 + `browser_evaluate`로 `#summaryBody tr` 첫 셀 텍스트 순서 추출)
  - (a) `fixture_old.csv`: 콘솔 에러 0, A·B 모두 '부정' 칩, 리스크 0곳.
  - (b) `fixture.csv` + 최근 1개월: 순서 A(리스크) → B(부정) → C → D(1개월 안이면 C보다 긍정비율 동일 100% → 리뷰 많은 C 먼저) → 나머지.
  - (c) 최근 1주: D는 "기간 내 리뷰 없음(마지막 리뷰 M월 D일)", 행 없는 병원은 "수집된 리뷰 없음", 둘 다 맨 아래.
  - (d) A의 '심각 1건(시술 부작용)' 클릭 → 원문 펼침. B는 "부정 3건: 주차 2, 가격 1".
  - (e) 최근 1주 선택 시 주간 요약 문장 미표시(10-06~10-12가 기간 밖), 최근 1개월 선택 시 표시.
  - (f) 날짜 직접 지정 → 기간 적용 → 병원 하나 클릭 → 병원 화면 기간 입력값이 같음 → 마스터 복귀 시 유지. 시작일>종료일이면 반영 안 됨.
  - (g) 폭 390px로 줄여 가로 스크롤이 기존과 같은 수준인지(표는 기존처럼 래퍼 안에서만 스크롤).

- [ ] **Step 5: 전체 테스트** — `Get-ChildItem test_*.py | ForEach-Object { & $py $_.Name }` → 모든 파일 PASS, `Traceback` 0건.

- [ ] **Step 6: 배포** — 서버 종료 후 `git push origin main`. Actions 다음 수집 때 `ensure_header`가 시트에 `severity` 헤더를 붙인다(수동으로 미리 붙이려면 `& $py reclassify.py --export --limit 1` — 헤더 확장 후 요청 파일 생성; 생성된 `reclassify_work/[재분류요청].json`은 그대로 두면 13:50 작업이 `_미반영_`으로 치움).

- [ ] **Step 7: 실측** — Pages(`https://sema-cs.github.io/review-integration-dashboard/?admin=1`) 강력 새로고침 후 콘솔 에러 0, 기간 변경·정렬 동작 확인. 심각도 백필 전이라 리스크 0곳이 정상임을 보고에 명시.

- [ ] **Step 8: 메모리 갱신** — `project_review_integration_sheet.md`에 "2026-10-20 마스터 대시보드 기간 지정·severity 정렬(커밋 해시)" 한 단락, MEMORY.md 해당 줄 끝에 짧게 추가.
