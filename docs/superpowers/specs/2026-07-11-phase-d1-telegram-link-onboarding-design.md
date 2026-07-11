# Phase D-1 설계: 공용 봇 텔레그램 연결 + 온보딩 체크리스트

- 상태: 확정 (2026-07-11, 브레인스토밍 완료 — 사용자 승인)
- 상위 스펙: `2026-07-03-multi-tenant-design.md` §2.7(telegram_destinations)·§7 row D
- 선행: Phase C(AI 원장·전역 게이트웨이) — main 머지·push 완료(2026-07-11)

## 0. 범위·확정 결정

상위 스펙 Phase D("온보딩·운영") 4항목은 독립 서브시스템이라 분해한다(사용자 확정):

- **D-1(본 스펙)**: 공용 봇 딥링크 연결(§2.7) + 온보딩 체크리스트. Phase D 검증 기준
  "신규 사용자가 UI만으로 가입→그룹 생성→채널 추가→분석 결과 텔레그램 수신"을 직접 달성.
- **D-2(별도 스펙, 이연)**: YouTube 쿼터 카운터(상위 §5 yt_quota_usage), 전 스키마 순회
  마이그레이션 도구.

**브레인스토밍 확정 결정:**

| 결정 | 내용 |
|------|------|
| D1. 수신 방식 | **getUpdates long-polling 워커**(A안). 공개 HTTPS 전제 0 — 현 배포(단일 컨테이너)에 그대로 동작. webhook(B안)은 공개 URL·TLS 의존이라 기각. 수신부를 함수로 분리해 향후 webhook 교체 가능하게 |
| D2. 연결 범위 | **개인 DM만**(start 딥링크). 그룹채팅방(startgroup·my_chat_member)은 후속 — 검증 기준은 DM으로 충족. `chat_kind` 컬럼만 선반영 |
| D3. 마법사 형태 | **체크리스트 카드**(그룹 만들기→채널 추가→텔레그램 연결, 완료 자동 감지). 전담 위자드 페이지 기각(라우팅·상태관리 과잉) |
| D4. 봇 토큰 저장 | `global_settings.telegram_bot_token`(secret) + dead config였던 `DEFAULT_TELEGRAM_BOT_TOKEN` env에서 멱등 시드(기존 YouTube/AI 키 시드 패턴) |
| D5. 발송 호환 | 기존 그룹의 bot_token/chat_ids 직접 설정이 **1순위**로 무변경 유지(프로덕션 4그룹 무중단). destination은 추가 경로 |
| D6. 연결 토큰 | DB 테이블(TTL 10분, 1회용) — 재시작에도 유효 |

**배경 사실(탐색으로 확인):** Phase C의 §3.3 권한 분리로 일반 user는 notification의
`bot_token`/`chat_ids`가 차단됨 → 현재 user가 알림을 받을 방법이 전무. D-1이 이 갭을
공용 봇 경로로 메운다. `DEFAULT_TELEGRAM_BOT_TOKEN`은 config에만 있고 사용처 0(dead).
그룹 0개 상태 프런트는 App.tsx:28의 안내 문구뿐(생성 유도 없음).

## 1. 제어평면 테이블 2개 (신규)

```sql
CREATE TABLE app.telegram_destinations (
    dest_id    BIGSERIAL   PRIMARY KEY,
    user_id    BIGINT      NOT NULL REFERENCES app.users(user_id) ON DELETE CASCADE,
    chat_kind  TEXT        NOT NULL DEFAULT 'private',  -- 'private' | 'group'(후속 확장 대비)
    chat_id    BIGINT      NOT NULL,
    title      TEXT,                                     -- DM: 텔레그램 표시 이름
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    linked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, chat_id)
);

CREATE TABLE app.telegram_link_tokens (
    token      TEXT        PRIMARY KEY,                  -- secrets.token_urlsafe(24)
    user_id    BIGINT      NOT NULL REFERENCES app.users(user_id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,                     -- 발급 + 10분
    used_at    TIMESTAMPTZ                               -- NULL=미사용(1회용)
);
```

- ORM: `app/models/control/telegram_destination.py`, `telegram_link_token.py` 신규.
  기본값은 전부 `server_default`(raw insert 호환 — 기존 관례). `ensure_control_schema`
  create_all로 생성(신규 테이블이라 ALTER 불필요).
- 재연결(같은 user·chat_id로 다시 /start): upsert — `is_active=true`·title 갱신.
- 토큰 발급 시 같은 user의 만료 토큰 lazy 삭제(전용 청소 잡 불필요).

## 2. 공용 봇 토큰 (global_settings 확장)

- 신규 키 `telegram_bot_token` — `SECRET_KEYS`에 추가(Fernet 암호화). admin
  `_GLOBAL_KEYS`에 노출(마스킹 라운드트립 가드는 SECRET_KEYS 기준이라 자동 적용).
- 부트스트랩(`bootstrap_global_settings`에 추가, 멱등): 전역 키 미시드이고
  `settings.DEFAULT_TELEGRAM_BOT_TOKEN`이 비어 있지 않으면 1회 시드.
  FERNET 부재 시 기존 패턴대로 경고 후 skip(부팅 안 막음).
- 봇 username: `getMe` 호출로 조회해 **인메모리 캐시**(딥링크 URL 생성용).
  토큰 변경 감지는 캐시 키를 토큰 자체로(토큰 바뀌면 재조회).

## 3. getUpdates long-polling 워커 (신규 `app/services/telegram_link_service.py`)

단일 소유 지점: 토큰 발급/검증/바인딩 + 워커 루프 + 봇 API 래퍼.

```
issue_link_token(user_id) -> (token, expires_at)      # 만료 토큰 lazy 삭제 포함
build_deep_link(token) -> str                          # https://t.me/<bot_username>?start=<token>
consume_link_token(token, chat_id, title) -> bool      # 검증(존재·미사용·미만료)→destination upsert→used_at 마킹
handle_update(update: dict) -> None                    # /start <token> private 메시지만 처리, 나머지 무시
run_telegram_updates_worker() -> None                  # 상시 루프(아래)
```

- **워커 수명**: `main.py` lifespan에서 `asyncio.create_task`로 기동, shutdown에서
  cancel. 스케줄러(APScheduler)와 별개의 상시 태스크 — long-poll(timeout=25s)이
  주기 잡보다 적합.
- **루프**: 전역 봇 토큰 조회 → 없으면 `sleep(60)` 후 재확인(idle, 무해) → 있으면
  `getUpdates(offset=last+1, timeout=25)` → 각 update를 `handle_update`로:
  - `message.chat.type == 'private'`이고 `text`가 `/start <token>` 형태일 때만:
    `consume_link_token` → 성공 시 확인 메시지 회신("✅ 연결 완료 — 이제 분석 알림을
    받습니다"), 실패(만료/无효) 시 안내 회신("링크가 만료됐습니다. 마이페이지에서 다시
    연결해 주세요").
  - 그 외 업데이트는 offset만 전진(무시).
- **견고성**: 루프 전체 try/except — 모든 예외를 삼키고 지수 백오프(최대 60s) 후 재개.
  워커 죽음이 앱을 못 깨뜨리고, 앱은 워커 없이도 정상(연결만 안 될 뿐).
- **전제**: getUpdates는 단일 소비자 — **단일 컨테이너 배포 전제**(현 배포와 일치).
  다중 인스턴스 전환 시 webhook 교체 필요(수신부 `handle_update`가 분리돼 있어 교체 국소적).

## 4. 연결 API + 마이페이지 UI

**백엔드** (`app/routers/auth.py`의 me_router 확장 — me 스코프 리소스):

| 엔드포인트 | 동작 |
|-----------|------|
| `POST /api/me/telegram/link-token` | 토큰 발급 → `{deep_link, expires_in_sec: 600}`. 전역 봇 미설정/getMe 실패 시 400 "관리자가 공용 봇을 설정해야 합니다." |
| `GET /api/me/telegram/destinations` | 본인 destination 목록 `[{dest_id, chat_kind, title, linked_at, is_active}]` (chat_id는 비노출 — 내부 식별자) |
| `DELETE /api/me/telegram/destinations/{dest_id}` | 본인 소유만 삭제(타인 것 404). 이 dest를 참조하는 그룹 notification은 발송 시 해석 단계에서 자연 폴백(§5) |

**프런트** (MyPage.tsx): "텔레그램 연결" 섹션 —
- 연결 목록(이름·연결일·해제 버튼).
- "연결하기" 버튼 → link-token 발급 → 딥링크 새 창(`window.open`) → 3초 간격 폴링(최대
  2분)으로 destinations 재조회, 새 연결 감지 시 목록 갱신+폴링 중단.
- 봇 미설정 400이면 안내 문구 표시.

## 5. 발송 경로 통합 — 3단계 우선순위 (기존 그룹 무중단이 제1원칙)

`NotificationSettings`에 `dest_id: Optional[int] = None` 추가. §3.3 권한: user 편집
**허용**(차단 목록에 안 넣음 — bot_token/chat_ids는 계속 차단). 프런트 defs의
notification에 dest 선택 필드 추가(user에게 보이는 유일한 발송 대상 설정).

**발송 대상 해석 — `notify_service.resolve_notify_target(group, notif)` 단일 소유:**

| 우선순위 | 조건 | 발송 수단 |
|---------|------|----------|
| 1 | 그룹 `bot_token`+`chat_ids` 명시 | 기존 경로 그대로 (프로덕션 4그룹 무변경) |
| 2 | `dest_id` 명시 + 해당 destination active | (전역 봇 토큰, destination.chat_id) |
| 3 | 둘 다 없음 + 그룹 owner의 active destination 존재 | (전역 봇 토큰, **첫 active destination**.chat_id — `dest_id` 오름차순, 즉 가장 먼저 연결한 것) — 상위 스펙 §2.7 기본값. 신규 사용자는 연결만 하면 설정 없이 알림 수신 |
| — | 셋 다 불가 | 발송 안 함(기존 "chat_ids 비면 데이터만" 동작 유지) |

- `is_sendable` 판정을 이 해석 결과 기반으로 확장(enabled && 대상 해석 성공).
- 영상 알림·다이제스트 발송 모두 이 해석 함수를 경유(단일 지점).
- dest_id 검증: notification PUT 시 그룹 owner 소유의 active destination인지 확인,
  아니면 400. owner가 NULL(레거시 admin 그룹)이면 dest_id 설정 불가(400) — 그 그룹은
  우선순위 1 경로 사용.
- destination 삭제 시 참조 그룹 설정은 그대로 두되 해석 단계에서 2→3→불가로 자연 폴백
  (설정 정리 강제 없음 — 관대한 동작).

## 6. 온보딩 체크리스트 카드 (프런트)

`OnboardingChecklist` 컴포넌트(신규):

- 스텝: ① 그룹 만들기(`groups.length > 0`) ② 채널 추가(현재 그룹 채널 > 0)
  ③ 텔레그램 연결(`destinations.length > 0`). 완료 단계 ✓ 표시, 미완 단계는 해당
  화면으로 링크(②는 채널 탭, ③은 마이페이지). 3개 모두 완료 시 렌더하지 않음.
- 노출 조건: **role=user만** (admin은 기존 운영자 — 불필요). 위치 2곳:
  - **그룹 0개 랜딩**: App.tsx:28의 안내 문구를 이 카드(+그룹 생성 폼 링크)로 대체.
  - **Dashboard 상단**: 온보딩 미완료 동안 표시.
- 데이터: 기존 API(groups 목록·channels 목록) + §4의 destinations 목록 재사용 —
  신규 백엔드 없음.

## 7. 테스트·검증

- **단위(DB 불필요)**: 토큰 발급 형식·TTL, `/start <token>` 파싱(비정형 입력 포함),
  `handle_update` 분기(private 아님/토큰 없음/만료/성공 — httpx·DB monkeypatch),
  **발송 대상 3단계 해석 우선순위**(각 조합), is_sendable 확장, deep_link 생성.
- **라우터**: link-token 발급 200/봇 미설정 400, destinations 목록/삭제 권한(본인만,
  타인 404), notification PUT dest_id 소유 검증 400.
- **리그레션**: 기존 notify/digest 테스트 전부 무변경 통과(우선순위 1이 기존 경로 —
  bot_token 설정된 기존 그룹의 동작 불변이 자동 증명됨).
- **프런트**: 체크리스트 스텝 판정 로직 단위 테스트(vitest). 빌드 클린.
- **실 DB E2E**(별도 체크포인트): 실 봇 토큰으로 — 전역 시드 확인 → 마이페이지
  연결하기 → 실제 텔레그램 /start → destination 생성 확인 → 분석 1건 알림 수신 →
  해제 → 발송 skip 확인.

## 8. 배포·운영 고려

- 부팅: 테이블 2개 create_all + env 시드(멱등). **봇 토큰 미설정이면 워커 idle** —
  기존 배포는 아무 변화 없음(관찰 가능한 차이 0). 롤백 안전(신규 테이블은 구버전이
  안 읽음).
- 운영 준비물(사용자 액션): BotFather로 공용 봇 생성 → 토큰을 env
  `DEFAULT_TELEGRAM_BOT_TOKEN` 또는 관리자 전역설정에 입력.
- 프로덕션 4그룹: bot_token 직접 설정이 우선순위 1이라 발송 무변경. admin 계정도
  원하면 마이페이지에서 연결 가능(강제 아님).

## 9. 영향 파일 요약

- 모델: `app/models/control/telegram_destination.py`·`telegram_link_token.py`(신규),
  `control_db.py`(임포트)
- 서비스: `telegram_link_service.py`(신규 — 토큰·워커·봇 API), `global_settings.py`
  (키·시드), `notify_service.py`(resolve_notify_target·is_sendable 확장),
  `settings_types.py`·`settings_manager.py`(dest_id)
- 라우터: `auth.py`(me_router 텔레그램 3종), `settings.py`(dest_id 검증), `admin.py`
  (_GLOBAL_KEYS)
- 앱: `main.py`(lifespan 워커 기동/종료)
- 프런트: `MyPage.tsx`(연결 섹션), `api/me.ts`, `OnboardingChecklist.tsx`(신규),
  `App.tsx`(그룹 0개 랜딩), `Dashboard.tsx`(카드), `settings/defs.ts`(notification
  dest 필드)
