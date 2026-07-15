# Phase E-1 설계: 초대 기반 B2B 최소 유료화 — pro 플랜·만료일·자동 강등

- 상태: 확정 (2026-07-15, 브레인스토밍 완료 — 사용자 승인)
- 상위 스펙: `2026-07-03-multi-tenant-design.md` §7 row E ("시장/방식 확정 후 별도 설계")
- 선행: Phase D-2 — main 머지·push·실 DB E2E 완료(2026-07-14)

## 0. 범위·확정 결정

상위 스펙 E행(결제 연동, 약관/개인정보처리방침, 플랜 업그레이드)에서 **시장/방식이
확정**됨에 따라 범위를 최소 유료화(E-1)로 축소 분해한다(사용자 확정).

**브레인스토밍 확정 결정:**

| 결정 | 내용 |
|------|------|
| E1. 시장/방식 | **초대 기반 B2B 소규모 유료 운영**. 공개 가입 없음, 고객사당 1계정. 수동 결제(계좌이체·견적) + 관리자 플랜 전환. 글로벌/국내 공개 SaaS — 기각(시기상조) |
| E2. 결제 연동 | **E-1 범위 제외** — 가입자 추이에 따라 후속(E-2)으로 PG(토스페이먼츠 등) 도입 검토. plans에 가격 필드도 추가하지 않음(B2B 견적 기반, YAGNI) |
| E3. 조건 상향 | 추가 결제 시 **관리자가 그룹·채널 한도 상향** — 기존 Phase B의 `user_limits` 오버라이드·초대 발급으로 대응(신규 구현 없음). 조직/그룹 공유 기능 — 기각(고객사당 1계정) |
| E4. 기간 관리 | **`plan_expires_at` + 스케줄러 자동 강등**(A안). 읽기 시점 해석(B안)은 해석 지점 산재로 기각, 수동 강등(C안)은 입금 누락 추적을 사람 기억에 의존해 기각 |
| E5. 약관/개인정보처리방침 | **지금은 생략** — 결제 기능 도입 시점(E-2)에 일괄 도입 |

**배경 사실(탐색으로 확인):**

- `plans`: free(1그룹/5채널/일10건/60분/$5/폴링하한 60분, is_default)·unlimited(관리자용)
  2종 시드(`auth_service.PLAN_SEEDS`, slug 멱등). 가격 필드 없음. 관리자는 PATCH만
  가능(생성 불가) — E-1도 생성 API는 불필요(pro는 시드로 추가).
- `users`에 만료 개념 없음 — 플랜 전환은 영구.
- 강등의 하류는 전부 자동: `quota_service.effective_limits`가 plan JOIN이라 plan_id가
  바뀌면 한도·관리자 화면·마이페이지가 즉시 반영된다.
- 알림 인프라: D-1의 `telegram_destinations`(공용 봇, 사용자 소유) 재사용 가능.

## 1. 데이터 모델

### 1.1 `app.users` 컬럼 2개 추가 (순수 추가, 부팅 마이그레이션)

| 컬럼 | 타입 | 의미 |
|------|------|------|
| plan_expires_at | TIMESTAMPTZ NULL | 유료 플랜 만료 시각. **NULL=무기한**(free·unlimited·기존 사용자 전원) |
| plan_expiry_notified_at | TIMESTAMPTZ NULL | 만료 임박(D-7) 알림 발송 시각 — 중복 알림 방지 가드 |

- `ensure_control_schema`의 기존 additive 패턴(`ALTER TABLE … ADD COLUMN IF NOT EXISTS`)으로 멱등 추가.
- 강등 후에도 `plan_expires_at`은 이력으로 보존한다 — 강등 틱은 "비기본 플랜"만
  대상으로 하므로 재처리되지 않는다(자연 멱등).
- 관리자가 플랜 또는 만료일을 변경하면 `plan_expiry_notified_at`을 NULL로 리셋한다
  (연장 시 다음 만료 주기에 임박 알림이 다시 나가야 함).

### 1.2 pro 플랜 시드 (PLAN_SEEDS 추가, slug 멱등)

| slug | max_groups | max_channels_total | max_analyses_per_day | max_video_minutes | monthly_cost_budget_usd | min_poll_interval_min | is_default |
|------|-----------|--------------------|----------------------|-------------------|--------------------------|------------------------|------------|
| pro | 3 | 30 | 100 | 120 | 30.0 | 10 | false |

시드값일 뿐 — 관리자가 기존 `PATCH /api/admin/plans/{id}`로 언제든 조정한다.
개별 고객 상향은 플랜 수정이 아니라 `user_limits` 오버라이드로 한다(E3).

## 2. 만료 틱 (신규 스케줄러 잡)

`plan_expiry_service.py`(신규) + 스케줄러 잡 등록(30분 주기, 기존 패턴:
`max_instances=1, coalesce=True, replace_existing=True`).

`run_plan_expiry_once()` 처리 순서:

1. **강등**: `plan_id != 기본플랜 AND plan_id != unlimited AND plan_expires_at IS NOT
   NULL AND plan_expires_at < now()` 인 사용자 → `plan_id = 기본플랜(is_default)`으로
   UPDATE. 사용자별 처리(부분 실패 격리), stdout 로그
   (`[plan-expiry] {email} pro 만료 → free 강등`).
2. **강등 알림**: 강등된 사용자에게 텔레그램 1회 — "플랜이 만료되어 free로
   전환되었습니다. 연장은 관리자에게 문의".
3. **임박 알림(D-7)**: `plan_expires_at`이 7일 이내(아직 미만료)이고
   `plan_expiry_notified_at IS NULL`인 비기본·비unlimited 사용자 → 텔레그램 1회 +
   `plan_expiry_notified_at = now()`. 재발송 규칙은 이 NULL 가드 하나뿐이며,
   연장 시 관리자 PATCH가 notified_at을 리셋해 다음 주기 알림을 다시 연다(§3).

**알림 경로·실패 처리**: D-1의 사용자 첫 active `telegram_destination`(공용 봇)으로
발송. 연결된 destination이 없거나 발송 실패면 **skip(best-effort)** — 알림 실패가
강등을 절대 막지 않으며, 마이페이지 표시(§4)가 폴백이다. 강등 자체는 알림과 무관하게
항상 수행된다.

**unlimited 제외 이유**: 관리자 플랜에 만료를 걸 이유가 없고, 실수로 만료일이
설정돼도 관리자 계정이 강등되는 사고를 구조적으로 차단한다.

## 3. 관리자

- `PATCH /api/admin/users/{id}`(기존 엔드포인트 확장): `plan_expires_at` 설정·해제
  (null 허용). 과거 시각도 허용한다 — 다음 틱에서 즉시 강등되므로 "지금 바로 강등"
  운영 동선으로 쓸 수 있다. 플랜 또는 만료일 변경 시 `plan_expiry_notified_at` 리셋.
- 사용자 목록(GET /api/admin/users): `plan_expires_at` 포함. Admin UI 사용자 테이블에
  만료일 컬럼 — 7일 이내 경고색(amber), 지났으면 빨강.
- 운영 동선: 입금 확인 → 해당 사용자 플랜 pro + 만료일(예: +1개월/+12개월) 설정.
  기간 연장 버튼 같은 편의 기능은 두지 않는다(YAGNI — 날짜 입력으로 충분).

## 4. 마이페이지

`GET /api/me/usage`(기존) 응답에 `plan_expires_at` 추가. MyPage에 현재 플랜명 옆
만료일 표시 — 7일 이내면 경고색 + "연장은 관리자에게 문의하세요" 문구. 무기한(NULL)
이면 만료일 비표시.

## 5. 테스트 계획

- 만료 틱: 만료된 pro→강등 / 미만료 유지 / NULL(무기한) 유지 / unlimited 제외 /
  이중 실행 멱등(강등 후 재처리 없음) / 사용자별 실패 격리(한 명 실패해도 계속).
- 임박 알림: D-7 진입 시 1회, notified_at 가드로 재발송 없음, 만료일 연장 시 리셋 후
  다음 주기 재발송, destination 없으면 skip(강등·notified 마킹은 정상 진행).
- 강등 → `effective_limits` 즉시 축소(quota_service JOIN 재사용 검증).
- admin PATCH: 만료일 설정/해제/과거 시각 허용, notified_at 리셋.
- me/usage 응답에 plan_expires_at 노출.
- 실 DB E2E(구현 후 체크리스트): 컬럼 마이그레이션 멱등, pro 시드, 만료 사용자
  실강등 + effective_limits 축소 실측, 임박 알림 실발송(실 봇), 관리자 PATCH 관통.

## 6. 프로덕션 호환성 (무중단 원칙)

- users 컬럼 2개 순수 추가(NULL), pro 플랜은 신규 행(시드 멱등).
- 기존 사용자 전원 `plan_expires_at=NULL`=무기한 → 만료 틱이 아무도 건드리지 않음.
- 새 스케줄러 잡은 대상 0명이면 no-op. 기존 잡·발송 경로 무변경.

## 7. E-2 이연 항목 (가입자 추이에 따라)

- PG 연동(토스페이먼츠 빌링 등) + plans 가격 필드 + 셀프서비스 업그레이드.
- 이용약관·개인정보처리방침 정적 페이지 + 가입 동의.
- 공개 가입 전환 여부 재검토.
