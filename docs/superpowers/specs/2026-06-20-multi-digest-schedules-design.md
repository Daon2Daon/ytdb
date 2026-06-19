# Digest 복수 설정 + 기간 프리셋(1/7/30일) 설계

작성일: 2026-06-20

## 배경 / 문제

현재 digest(주간 리뷰)는 **그룹당 설정 1개**이며, 집계 기간은 `period_weeks`(주)만
지원한다. 스케줄도 **요일+시각** 주간 모델 하나뿐이다.

사용자 요구:

1. 리뷰 기간을 **1일 / 7일 / 30일** 프리셋으로 선택
2. **복수 digest 설정** — 예: 7일 분석 알림과 1일 분석 알림을 동시에 운영
3. 각 설정은 **이름, 기간·일정, 카테고리, 프롬프트, 텔레그램 on/off**를 독립 보유
4. 기존 digest **기록 보존** + 현재 flat 설정을 **7일 설정 1개**로 자동 이전

## 목표

- 그룹당 N개(상한 10) digest 스케줄 설정을 JSON 배열로 저장 (접근법 A)
- 기간 프리셋별 자연스러운 트리거:
  - **1일** → 매일 지정 시각
  - **7일** → 지정 요일 + 시각
  - **30일** → 매월 지정일(1–28) + 시각
- 스케줄러 틱이 **활성 설정마다** 독립적으로 생성·발송·dedup
- 설정 UI를 flat 필드 목록에서 **복수 카드 편집기**로 교체

## 비목표

- 임의 일수 자유 입력 (예: 14일, 45일)
- digest별 텔레그램 chat_id/bot_token 분리 (그룹 notification 설정 공유 유지)
- digest별 share_link_enabled 분리 (그룹 공통 1토글 유지 — 기존 `share_link_enabled`는 그룹 레벨)
- 기존 digest 레코드의 `period_weeks` 소급 재계산

## 결정 사항

| 항목 | 결정 |
|---|---|
| 저장 | `digest` 카테고리 단일 JSON 키 `configs` (value_type=json) |
| 레거시 flat 키 | 읽기 시 lazy 마이그레이션 → `configs` 1건으로 변환; 저장 시 `configs`만 기록 |
| 설정 식별 | 각 항목에 `id` (UUID v4 문자열) |
| 기간 | `period_days`: 1 \| 7 \| 30 |
| dedup 키 | `(digest_config_id, period_days, period_start, period_end, category)` |
| 직전 리포트 | 동일 `digest_config_id` + `period_days` + `category` |
| 프롬프트 | 설정별 `digest_prompt`; 빈 값이면 그룹 `prompts.digest_prompt` → 코드 기본값 |
| share_link | 그룹 공통 `share_link_enabled` (기존 flat 키 유지, configs 밖) |
| timezone | 설정별 `timezone` (독립 일정 판정) |

---

## 1. 설정 데이터 모델

### 1.1 `DigestScheduleConfig` (dataclass)

```python
@dataclass
class DigestScheduleConfig:
    id: str                          # UUID
    name: str                        # UI 표시명, 예: "일간 브리핑"
    enabled: bool = False
    period_days: int = 7             # 1 | 7 | 30
    schedule_time: str = "20:00"     # HH:MM, 모든 프리셋 공통
    schedule_day: str = "sun"        # 7일 전용: mon..sun
    schedule_dom: int = 1            # 30일 전용: 1..28
    timezone: str = "Asia/Seoul"
    category: str = ""
    digest_prompt: str = ""          # 빈 값 → 그룹 기본
    telegram_enabled: bool = False
```

검증 규칙:

- `period_days` ∈ {1, 7, 30}
- `schedule_time` HH:MM (00:00–23:59)
- `schedule_day` ∈ {mon..sun} (period_days=7일 때만 UI 노출·검증)
- `schedule_dom` 1–28 (period_days=30일 때만; 29–31일은 월말 불일치 방지)
- `name` 비어 있으면 저장 시 `"Digest {n}"` 자동 부여
- 그룹당 최대 **10개**; 초과 시 400

### 1.2 Settings 저장 형태

**신규 canonical:**

```json
{
  "key": "configs",
  "value_type": "json",
  "value": [
    {
      "id": "a1b2c3d4-...",
      "name": "주간 종합",
      "enabled": true,
      "period_days": 7,
      "schedule_day": "sun",
      "schedule_time": "20:00",
      "schedule_dom": 1,
      "timezone": "Asia/Seoul",
      "category": "",
      "digest_prompt": "",
      "telegram_enabled": true
    },
    {
      "id": "e5f6...",
      "name": "일간 요약",
      "enabled": true,
      "period_days": 1,
      "schedule_time": "08:00",
      "schedule_day": "sun",
      "schedule_dom": 1,
      "timezone": "Asia/Seoul",
      "category": "macro",
      "digest_prompt": "...",
      "telegram_enabled": true
    }
  ]
}
```

**그룹 공통 (configs 밖, 기존 flat 유지):**

- `share_link_enabled` (bool) — 모든 digest 텔레그램 발송에 공통 적용

### 1.3 SettingsManager API 변경

| 기존 | 신규 |
|---|---|
| `get_digest(group_id) -> DigestSettings` | `get_digest_configs(group_id) -> list[DigestScheduleConfig]` |
| — | `get_digest_share_settings(group_id) -> DigestShareSettings` (share_link_enabled만) |

`get_digest_configs` 내부:

1. `configs` JSON이 있으면 파싱·검증·정규화 후 반환
2. 없고 레거시 flat 키(`enabled`, `period_weeks` 등)가 있으면 **1건으로 변환**:
   - `id`: `"legacy"` (고정; 이후 저장 시 새 UUID로 치환 가능)
   - `name`: `"주간 리뷰"`
   - `period_days`: `period_weeks * 7` (최소 7)
   - 나머지 flat 값 매핑
3. 둘 다 없으면 `[]`

`set_values`로 `configs` 저장 시 레거시 flat digest 키는 **삭제하지 않음**
(읽기 우선순위: configs > legacy). UI 저장은 `configs` + `share_link_enabled`만 전송.

### 1.4 신규 그룹 시드

`DEFAULT_GROUP_SETTINGS["digest"]`:

```python
{"key": "configs", "value": "[]", "value_type": "json"},
{"key": "share_link_enabled", "value": "true", "value_type": "bool"},
```

기존 flat digest 키 시드 제거.

---

## 2. 생성 결과(Digest) 데이터 모델

### 2.1 additive 컬럼 (`db_engine.additive_columns`)

```python
("digests", "period_days", "integer"),
("digests", "digest_config_id", "text"),
("digests", "config_name", "text"),   # 생성 시점 스냅샷(목록 표시용)
```

기존 컬럼 유지:

- `period_type`, `period_weeks` — 레거시 호환; 신규 생성 시:
  - `period_days` = canonical
  - `period_type` = `"daily"` \| `"weekly"` \| `"monthly"` (period_days에서 파생)
  - `period_weeks` = `max(1, period_days // 7)` (기존 코드 경로 안전)

### 2.2 dedup / 조회 키 변경

**생성 전 중복 검사** (`_digest_exists_for_period`):

```sql
WHERE digest_config_id = :id
  AND period_days = :days
  AND period_start = :start
  AND period_end = :end
  AND category IS NOT DISTINCT FROM :category
```

**직전 리포트** (`_fetch_previous_digest`):

```sql
WHERE digest_config_id = :id
  AND period_days = :days
  AND category IS NOT DISTINCT FROM :category
  AND period_end <= :before
  AND status IN ('done', 'fallback')
ORDER BY period_end DESC LIMIT 1
```

레거시 digest(`digest_config_id IS NULL`)는 기존 `(period_type, period_weeks, category)` 로직 유지.

### 2.3 API 스키마 (`DigestOut`)

추가 필드:

- `period_days: int`
- `digest_config_id: Optional[str]`
- `config_name: Optional[str]`

`DigestGenerateRequest`:

- `digest_config_id: Optional[str]` — 지정 시 해당 설정으로 생성
- `period_days`, `category` — 수동 오버라이드(테스트용, config_id 없을 때만)

---

## 3. 기간·스케줄 로직

### 3.1 집계 기간 `_period(as_of, period_days)`

```python
end = as_of.replace(second=0, microsecond=0)
start = end - timedelta(days=period_days)
return start, end
```

`period_weeks` 기반 계산 제거(레거시 읽기 전용).

### 3.2 발생 시각 계산 `_most_recent_occurrence`

공통 입력: `now_local`, `cfg: DigestScheduleConfig`

| period_days | 함수 | 설명 |
|---|---|---|
| 1 | `_most_recent_daily(now, time)` | 오늘 time이 지났으면 오늘, 아니면 어제 |
| 7 | 기존 `_most_recent_occurrence(now, day, time)` | 변경 없음 |
| 30 | `_most_recent_monthly(now, dom, time)` | 이번 달 dom+time; 미래면 지난달; dom이 해당 월에 없으면 해당 월 말일로 clamp |

### 3.3 따라잡기(catch-up) 윈도우

| period_days | 최대 지연 |
|---|---|
| 1 | 1일 |
| 7 | 7일 (기존과 동일) |
| 30 | 31일 |

`now_local - occ_local > window` 이면 skip (폭주 방지).

### 3.4 `run_digest_tick_once` 흐름

```
for group in active_groups:
  configs = get_digest_configs(group_id)
  share = get_digest_share_settings(group_id)
  for cfg in configs:
    if not cfg.enabled: continue
    occ_local = compute_occurrence(cfg, now)
    if occ_local is None or outside catch-up window: continue
    period_start, period_end = _period(occ_utc, cfg.period_days)
    if digest_exists(cfg.id, ...): continue
    digest = generate_digest_for_group(group, cfg, as_of=occ_utc)
    if cfg.telegram_enabled and digest.telegram_summary:
      send_telegram(..., share_link_enabled=share.share_link_enabled)
```

한 틱에서 **여러 설정이 동시에 due**일 수 있음 → 각각 독립 처리.

---

## 4. LLM 합성

`synthesize_with_llm(..., digest_prompt: str)`:

```python
prompt = (digest_prompt or prompts.digest_prompt or DEFAULT_DIGEST_PROMPT).strip()
```

설정별 프롬프트가 그룹 기본·코드 기본보다 우선.

텔레그램/웹 headline fallback `"주간 리뷰"` → period_days별:

- 1 → `"일간 리뷰"`
- 7 → `"주간 리뷰"`
- 30 → `"월간 리뷰"`

(하드코딩 3분기, 단순)

---

## 5. 프론트엔드 UI

### 5.1 Settings · Digest 탭

`SETTING_DEFS.digest` flat 필드 **제거**.

`Settings.tsx`에서 `category === 'digest'`일 때 전용 컴포넌트 렌더:

**`DigestConfigsEditor.tsx`**

- 상단: `share_link_enabled` 토글 (그룹 공통)
- 본문: 설정 카드 목록
  - 각 카드: name, enabled, period_days(select 1/7/30), schedule_time
  - period_days=7 → schedule_day select
  - period_days=30 → schedule_dom select (1–28)
  - timezone, category, digest_prompt(textarea), telegram_enabled
  - 삭제 버튼
- 하단: 「+ digest 추가」 (10개 상한)
- 저장: `configs` JSON + `share_link_enabled` PUT

카드 접기/펼치기는 v1 생략 (YAGNI).

### 5.2 Digests 목록 페이지

- 리스트 항목에 `config_name` · `period_days` 라벨 표시
  - 예: `[일간] 2026-06-19 ~ 2026-06-20`
- 「지금 생성」→ 설정 선택 드롭다운 후 `POST /generate { digest_config_id }`
  - 설정 0개면 안내 메시지

### 5.3 라벨

설정 탭: `"주간 리뷰"` → `"Digest"` 또는 `"리뷰 알림"` (카피 통일은 구현 시 결정)

---

## 6. 마이그레이션

### 6.1 설정 (lazy, 읽기 시)

| 레거시 flat | configs[0] |
|---|---|
| enabled | enabled |
| period_weeks | period_days = weeks * 7 |
| schedule_day/time/timezone | 동일 |
| telegram_enabled | telegram_enabled |
| category | category |
| — | digest_prompt = "" |
| — | name = "주간 리뷰" |
| — | schedule_dom = 1 |

첫 UI 저장 시 `configs` JSON으로 persist; legacy 키는 orphan으로 남아도 무방.

### 6.2 digest 기록

- 기존 행: `period_days` NULL, `digest_config_id` NULL → UI/API는 `period_weeks`로 표시
- 신규 행: `period_days` + `digest_config_id` 채움

DB backfill 마이그레이션 **하지 않음** (요구사항: 기록 보존만).

---

## 7. 테스트

| 영역 | 케이스 |
|---|---|
| settings | legacy flat → configs 변환; JSON 검증; 10개 상한 |
| schedule | daily/weekly/monthly `_most_recent_*` 경계(자정, 월말, 재시작) |
| dedup | 동일 config+기간 중복 생성 안 함; 다른 config_id는 각각 생성 |
| tick | enabled 2개 동시 due → 2건 생성 |
| prompt | config prompt > group prompt > default |
| generate API | digest_config_id 지정 생성 |
| migration | period_weeks=1 legacy → period_days=7 |

---

## 8. 구현 순서 (권장)

1. `DigestScheduleConfig` + SettingsManager (`get_digest_configs`, 검증, legacy 변환)
2. `digest_service` 기간·스케줄·dedup·LLM prompt 분기
3. `Digest` additive 컬럼 + 스키마/API 타입
4. `run_digest_tick_once` 복수 config 루프
5. 프론트 `DigestConfigsEditor` + Digests 페이지 보강
6. 테스트 + default seed 갱신

---

## 9. 리스크 / 주의

- **LLM 비용**: 1일 digest + 7일 digest 동시 활성 시 호출 2배 — UI help에 명시
- **월간 dom**: 29–31 미지원 — help: "28일 이하 권장 (모든 달에 존재)"
- **legacy id `"legacy"`**: 수동 생성 digest와 dedup 공유 — 첫 저장 시 UUID 재발급 권장
- **동시 tick**: 같은 그룹 다중 config LLM 호출은 순차(await) — v1에서 동시성 제한 불필요
