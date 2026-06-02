# 기능 도입 계획 (채널 설정 / 영상 상세 / 단일 분석 / 주간 리뷰 / 태그 클라우드 / 숏츠)

본 문서는 `my-assistant`의 YouTube 기능을 `ytdb`(다중 그룹)로 이식·발전시키기 위한 구현 계획이다.
구현 전 확정용 문서이며, 확정 후 단계별로 진행한다.

## 현황 요약

| 기능 | 현재 ytdb 상태 | 핵심 격차 |
|------|----------------|-----------|
| ① 채널별 활성/알림/주기 | DB·API 완비. UI는 활성 토글·추가만. `notify_enabled` 런타임 미사용 | 알림/주기 편집 UI + 발송 시 채널 플래그 반영 |
| ② 영상 상세 보기 | 상세 모달 존재. 태그·핵심포인트·인사이트·엔티티 미표시 | 표시 항목 확장 |
| ③ 단일 URL 분석 등록 | 없음(재분석만) | API + UI 신규 |
| ④ 주간 리뷰(다이제스트) | `Digest` 모델만 존재 | 서비스·스케줄·설정·UI·알림 신규 |
| ⑤ 태그 클라우드 | 자동 기록은 동작. 조회 API·UI 없음 | 태그 API + 클라우드 UI + 태그 필터 |
| ⑥ 숏츠 포함 설정 | 없음(원본에도 없음). `duration_seconds`는 수집됨 | 신규 설계: 설정값 + 수집 필터 |

> 참고: `Digest`, `Tag`, `VideoTag` 모델은 이미 `PgBase.metadata`에 등록되어 있어
> `ensure_schema`가 누락 테이블을 자동 생성한다. 새 DB 테이블 마이그레이션은 별도 작업 불필요.

---

## 확정이 필요한 설계 결정

### D1. 숏츠 판별 기준 (⑥) — 구현하지 않음
YouTube Data API에는 "숏츠 여부" 플래그가 없고 영상 길이 기반 휴리스틱은 정확도가 낮아
**도입하지 않기로 결정**(2026-06-02). `duration_seconds` 수집·표시만 유지한다.

### D2. 단일 URL 분석의 채널 처리 (③)
원본은 가상 채널 `__instant__`를 사용한다. ytdb는 `videos.channel_pk`가 NOT NULL FK이므로 채널 행이 필요하다.

- 등록된 채널의 영상이면 해당 `channel_pk`에 연결.
- 미등록 채널 영상이면 그룹 스키마에 가상 채널 1개(`channel_id='__instant__'`, `is_active=false`, `notify_enabled=false`)를 ensure 후 연결.

**제안: 가상 채널 방식 채택**(원본과 동일, 변경 최소).

### D3. 단일 분석 알림 발송 여부 (③)
- 즉시 분석으로 추가된 영상도 그룹 알림 설정에 따라 발송할지.
- **제안: 발송하지 않음(데이터/상세만).** 수동 확인 목적이 크고, 가상 채널 `notify_enabled=false`와 일관.

### D4. 다이제스트 스케줄 모델 (④)
원본은 그룹(단일 인스턴스)별 cron(`요일+시각`)으로 동작. ytdb는 다중 그룹이다.

- `digest` 설정 카테고리(그룹별):
  - `enabled`(bool), `period_weeks`(int, 기본 1)
  - `schedule_day`(mon~sun, 기본 sun), `schedule_time`(HH:MM, 기본 20:00), `timezone`(기본 Asia/Seoul)
  - `telegram_enabled`(bool), `category`(선택 필터)
- 스케줄러: **전역 단일 잡**(예: 매 5분 틱)으로 활성 그룹을 순회하며 "지금이 그룹의 예약 시각인지" 판정해 실행.
  - 그룹별 동적 cron 잡 등록 방식은 그룹 증가·설정 변경 시 재등록 복잡 → 전역 틱 순회가 단순·견고.
  - 중복 실행 방지: 같은 (그룹, period_start) digest가 이미 있으면 skip.

**제안: 전역 틱(5분) 순회 + 멱등 가드.**

### D5. 다이제스트 LLM 경로 (④)
- `ai_gateway.digest_model`이 있으면 사용, 없으면 `primary_model`.
- LLM 호출은 텍스트 chat(`LiteLLMClient.chat`) 사용(영상 없이 집계 텍스트 입력).

---

## 단계별 구현 계획

각 단계는 독립 검증 가능하도록 분리한다. 의존성 순서가 아니라, 작은 것부터 진행한다.

### P1. 채널 설정 UI + 알림 반영 (①)  — 소규모
- 백엔드:
  - `notify_service.notify_video` 호출부(`monitor_service._notify_after_analysis`)에서
    채널 `notify_enabled=false`이면 발송 skip(데이터만). 채널 조회 추가.
  - 검증: 비활성 채널 영상 분석 시 알림 미발송, 활성 채널은 발송.
- 프론트(`app.js`):
  - `renderChannels`에 채널별 **알림 토글**, **주기(시간) 편집** 추가(PATCH `notify_enabled`/`poll_interval_min`).
  - 주기는 UI에서 시간 입력 → 분으로 변환(설정 UI와 일관).
- 검증: 토글/편집 후 새로고침 시 값 유지.

### P2. 영상 상세 항목 확장 (②) — 소규모
- `app.js openVideo`에 `bullet_points`, `key_points`, `insights`, `entities`, **태그** 렌더 추가.
- 백엔드 스키마(`schemas/video.py AnalysisOut`)에 누락 필드 있으면 보강, 상세 응답에 태그 join 추가.
- 검증: 분석 완료 영상 상세에 태그/불릿/인사이트 표시.

### P3. 숏츠 필터 (⑥) — 소규모
- `settings_types.PollingSettings`에 `include_shorts`, `shorts_max_seconds` 추가.
- `settings_manager.get_polling`에 로드 추가.
- `monitor_service.process_channel` 필터 적용(D1).
- `app.js` Monitoring 설정에 `include_shorts`(체크박스), `shorts_max_seconds`(숫자) 추가.
- 검증: `include_shorts=false`로 60초 이하 영상 수집 제외 확인.

### P4. 태그 조회 API + 클라우드 UI (⑤) — 중규모
- `routers/tags.py` 신규: `GET /api/groups/{slug}/tags?min_count=&limit=` (video_count desc).
- 영상 목록 태그 필터: `GET .../videos?tag=name` 지원(`videos.py list_videos`에 `video_tags` 서브쿼리).
- `main.py`에 라우터 등록.
- 프론트: 네비에 **태그** 탭 추가, `renderTags`(폰트 크기 가중), 태그 클릭 → 영상 목록 필터.
- 검증: 태그 목록·카운트 정상, 클릭 시 필터 동작.

### P5. 단일 URL 분석 등록 (③) — 중규모
- `youtube_api`: 단일 영상 URL→video_id 추출(`watch?v=`, `youtu.be/`, `/shorts/`), `get_video_details` 재사용.
- `monitor_service` 또는 신규 함수: 가상 채널 ensure + 영상 INSERT(pending) → 분석 트리거.
- `routers/videos.py`(또는 actions): `POST .../videos/instant` (body: video_url). 기존 영상이면 그 pk 반환.
- 프론트: 네비 **영상 분석** 탭 또는 영상 탭 상단에 URL 입력 폼. 등록 후 완료 폴링→상세.
- 검증: URL 입력→분석 완료→상세 확인.

### P6. 주간 리뷰(다이제스트) (④) — 대규모
- `settings_types.DigestSettings` 신규 + `settings_manager.get_digest`.
- `services/digest_service.py` 신규:
  - `aggregate_period(session, start, end, category)` → 영상/감성/태그/채널 집계.
  - `synthesize_with_llm(...)` → 브리핑 합성(digest_model/primary_model).
  - `generate_digest(group, ...)` → 집계→LLM→`digests` 저장(멱등).
  - 텔레그램 발송(`notify_service` 재사용 또는 digest 전용 메시지 빌더).
- `services/scheduler.py`: 전역 `youtube_digest_tick`(5분) 잡 추가 → 활성 그룹 예약 판정·실행.
- `routers/digests.py` 신규: 목록/상세/삭제/수동생성(`generate?save=`).
- 프론트: 네비 **주간 리뷰** 탭(목록·상세), 설정에 **Digest** 탭.
- 검증: 수동 생성으로 집계·요약·저장·발송 확인 후, 스케줄 판정 단위 검증.

---

## 영향 받는 파일 (요약)

- 설정: `app/services/settings_types.py`, `settings_manager.py`, `routers/settings.py`(digest 검증은 이미 허용)
- 모니터: `app/services/monitor_service.py`(숏츠 필터, 채널 알림 플래그, 단일분석 적재)
- 신규 서비스: `app/services/digest_service.py`
- 라우터: `app/routers/tags.py`(신규), `digests.py`(신규), `videos.py`(태그필터·instant), `main.py`(등록)
- 스케줄러: `app/services/scheduler.py`(digest tick)
- 스키마: `app/schemas/video.py`(상세 확장), `app/schemas/tag.py`·`digest.py`(신규)
- 프론트: `app/static/index.html`(탭 추가), `app/static/app.js`(채널/상세/태그/단일분석/다이제스트), `style.css`(태그 클라우드)

## 검증 전략

- 단계별로 ASGI E2E(임시 그룹) + 실제 DB 라운드트립으로 확인.
- 숏츠/다이제스트 등 로직성 기능은 순수 함수 단위 검증을 우선.
- 회귀 방지: 기존 폴링/분석/알림 경로는 변경 최소화(채널 플래그 체크만 추가).
