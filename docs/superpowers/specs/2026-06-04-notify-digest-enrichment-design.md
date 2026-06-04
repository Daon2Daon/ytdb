# 텔레그램 메시지 풍부화 + Digest 영상블록 설계

작성일: 2026-06-04
대상: ytdb
레퍼런스: `my-assistant`(전작) — 검증된 구현을 ytdb 멀티그룹 구조로 포팅

## 배경 / 목적

ytdb의 알림·digest는 전작 `my-assistant`의 간소화(퇴화) 포팅이었다. 두 가지를 복원한다.

1. **텔레그램 영상 알림이 간략함**: 현재 `build_message`는 headline+one_line+short_summary+감성/신뢰도+url만. 전작은 채널명·full_analysis_md·bullets·태그·날짜·재생시간을 포함.
2. **digest가 영상 내용을 안 봄**: ytdb `synthesize_with_llm`은 `{videos_block}`을 치환하지 않고 태그·감성 카운트 JSON만 LLM에 전달. 전작은 영상별 요약(one_line/bullets/insights/entities)을 `_build_videos_block`으로 구성해 `.format()`으로 주입.

## 확정 결정
- 메시지 상세도: notification 설정 `message_detail`(`full`|`compact`) 토글, **기본 `full`**.
- 카테고리: **토큰 포함 매칭**으로 정규화(다중 리뷰 재구성은 하지 않음, 기존 단일-카테고리 모델 유지).

## 아키텍처 적응 (전작 단일DB → ytdb 멀티그룹)
- 전작은 단일 youtube 스키마·전역 설정·SQLite 앱로그. ytdb는 그룹별 youtube 스키마(schema_translate_map)·control DB 설정·async 데이터평면 세션.
- 포팅 시: 모든 쿼리는 그룹 세션(`make_session`/`AsyncSession`)으로, SQLite `create_log` 제거, ytdb `write_job_log` 사용, LLM은 ytdb `LiteLLMClient` + 그룹별 `settings_manager`.

---

## Part A — 텔레그램 영상 알림 풍부화

### A-1. `build_message` 교체 (`app/services/notify_service.py`)
전작 `build_notification_text` 포팅. 시그니처:
```python
def build_message(video, analysis, threshold=0.0, *, channel_name="", tags=None, detail="full") -> str
```
- `detail="compact"`: 현재 형식 유지(headline+one_line+short_summary+감성/신뢰도+url) — 하위호환.
- `detail="full"`: 아래 순서로 구성(전작 포맷):
  - 저신뢰도(`confidence_score < threshold`) 시 상단 `⚠️ <b>[저신뢰도 분석]</b>`
  - `🎬 [{channel_name}] 신규 영상`
  - `<b>{headline}</b>`
  - `full_analysis_md`(없으면 short_summary_md 폴백)
  - `bullet_points` → `• {항목}` 라인들
  - `🏷 {tags ', ' join}`
  - `📅 {published_at KST}  ·  ⏱ {duration mm:ss}`
  - `🔗 <a href="{url}">영상 보러가기</a>`
- HTML escape는 기존 `escape` 사용. href는 별도 escape.

### A-2. 스마트 길이 절단
`_TELEGRAM_MAX_LEN = 4096`. 초과 시 재귀 축소(전작 로직):
1. `full_analysis_md`를 overflow만큼 잘라 `…` 후 재구성
2. 그래도 초과면 `bullet_points` 마지막 항목부터 제거
3. 최후엔 HTML 안전 하드컷
현재의 단순 `text[:_MAX_LEN]`(태그 깨짐) 제거.

### A-3. 호출부 보강
`notify_video`, `notify_pending_batch`가 발송 전 채널명·태그를 조회해 `build_message`에 주입:
- 채널명: 이미 조회 중인 `Channel.channel_name`(batch는 조인에 포함, `notify_video`는 video.channel_pk로 조회 또는 `video.source_channel_name` 사용).
- 태그: `video_tags`×`tags`에서 해당 video의 태그명(weight 내림차순 상위 N).
- `detail`: `get_notification().message_detail` 전달.
- `_notify_after_analysis`(monitor_service)도 동일하게 채널명/태그/detail 주입.

### A-4. 데이터 모델
notification 카테고리에 `message_detail`(string, 기본 `full`) 추가. `NotificationSettings` dataclass + `get_notification` + 기본 시드 + 프론트 defs.

---

## Part B — Digest가 영상 내용을 읽도록

### B-1. `aggregate_period` 확장 (`app/services/digest_service.py`)
영상별 brief를 수집하도록 쿼리·반환 확장:
```python
@dataclass
class VideoBrief:
    channel_name: str
    headline: Optional[str]
    one_line: Optional[str]
    title: Optional[str]
    sentiment: Optional[str]
    bullet_points: Optional[list]
    insights: Optional[list]
    entities: Optional[list]
```
`DigestAggregate`에 `videos: list[VideoBrief]` 추가. 기존 sentiment_breakdown/top_tags/top_channels 유지.

### B-2. `_build_videos_block` 포팅 (순수 함수)
영상별 텍스트 블록:
```
- [채널] 헤드라인 (논조: 강세/약세/중립/혼조)
  한줄요약
  • bullet1
  • bullet2
  ▶ 인사이트: ...
  · 등장: 기업A, 기업B, NVDA
```
상한 상수: `_MAX_VIDEOS_IN_PROMPT=40`, `_MAX_BULLETS_PER_VIDEO=3`, `_MAX_INSIGHTS_PER_VIDEO=3`. 초과분은 `... 외 N건`. `_SENTIMENT_KO` 매핑, `_format_entities`(entities → "name, name") 포팅.

### B-3. `.format()` 치환 (`synthesize_with_llm`)
현재 `user_msg = f"{prompt}\n\n집계 데이터:\n{context_json}"`를, 사용자 프롬프트의 placeholder를 실제 치환하도록 변경:
```python
filled = template.format(
    category=category or "전체",
    period_label=period_label,
    video_count=agg.video_count,
    sentiment_summary=_sentiment_summary_text(agg.sentiment_breakdown),
    top_tags=", ".join(t["name"] for t in agg.top_tags[:8]),
    videos_block=_build_videos_block(agg),
)
```
- 사용자 프롬프트의 JSON 출력 예시는 `{{ }}`로 이스케이프돼 있어 `.format()`과 호환.
- placeholder 누락/오타로 `KeyError`/`IndexError` 발생 시 안전 폴백(치환 실패 시 기존 방식으로 append)하여 발송 자체는 막지 않음.
- `period_label`은 기간으로 생성(예: "2026-05-28 ~ 2026-06-04").

### B-4. 카테고리 토큰 정규화
- `split_category_tokens(raw)` 포팅: 콤마 분리 + 순서보존 dedup.
- `aggregate_period`의 카테고리 필터를 **정확 일치 → 토큰 포함**으로 변경: 설정 category 토큰이 채널 category 토큰에 포함되면 매칭. 빈 설정이면 전체. NULL/공백 채널은 "미분류".
- 구현: 채널을 메모리에서 토큰 매칭하거나, 단순화를 위해 `Channel.category ILIKE '%{token}%'` 조건(토큰 단위). 정확도를 위해 파이썬 토큰 비교 권장.

---

## 파일 변경 요약
- `app/services/notify_service.py`: build_message 교체 + 스마트 절단 + 호출부(notify_video/notify_pending_batch) 채널명·태그·detail 주입 + 태그 조회 헬퍼.
- `app/services/monitor_service.py`: `_notify_after_analysis`의 notify_video 호출에 채널명·태그·detail 주입.
- `app/services/digest_service.py`: VideoBrief/aggregate_period 확장 + _build_videos_block/_format_entities/_SENTIMENT_KO/split_category_tokens 포팅 + synthesize_with_llm .format() + 카테고리 토큰 매칭.
- `app/services/settings_types.py` + `settings_manager.py` + `default_settings.py`: `message_detail`.
- `frontend/src/settings/defs.ts`(+convert): notification에 `message_detail` select(full/compact).

## 테스트
- 순수 함수 TDD: `build_message`(full 구조/compact 하위호환/길이절단/저신뢰배지), `_build_videos_block`, `split_category_tokens`, `_format_entities`.
- DB 의존(aggregate_period 확장, synthesize .format): 구현 + 수동 검증(digest 1회 생성, 텔레그램 수신 확인).
- 프론트: `message_detail` convert 라운드트립.

## 비목표
- A-1 confidence 프롬프트 재정의, 엔티티 별칭 병합·종목 컨센서스 표, 이메일 digest, 월간 주기 — 별도 작업.
- digest 다중 카테고리 리뷰 재구성(전작의 generate_category_review 구조) — 미포팅, 단일 카테고리 유지.
