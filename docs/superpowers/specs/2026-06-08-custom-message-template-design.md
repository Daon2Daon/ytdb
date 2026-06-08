# Custom Message Template 설계

**날짜:** 2026-06-08  
**상태:** 승인됨

## 개요

텔레그램 알림 메시지의 구성을 사용자가 자유롭게 설정할 수 있도록 한다. 기존 `full`/`compact` 고정 모드를 단일 커스텀 렌더링 엔진으로 통합하고, `full`/`compact`는 미리 정의된 프리셋으로 제공한다.

## 핵심 결정

| 항목 | 결정 |
|---|---|
| 아키텍처 | `full`/`compact`를 프리셋으로 흡수, 단일 엔진으로 통합 |
| 저장 구조 | `message_detail` (string) → `message_template` (JSON) |
| 필드 범위 | 분석 결과 10개 + 영상 메타 6개 |
| 필드 옵션 | 포함/제외 + 순서만 (per-field 세부 옵션 없음) |
| 마이그레이션 | DB 스키마 변경 없음, 파싱 폴백으로 하위 호환 처리 |

## 1. 데이터 모델

### 템플릿 JSON 구조

```json
{"fields": ["headline", "analysis_sections", "bullet_points", "tags", "channel_name", "published_at", "duration", "video_url", "share_link"]}
```

`fields` 배열의 순서가 메시지 출력 순서이며, 배열에 없는 필드는 출력하지 않는다.

### 선택 가능한 필드

| 필드 키 | 출처 | 설명 |
|---|---|---|
| `headline` | video_analysis | 제목/헤드라인 |
| `one_line` | video_analysis | 한 줄 요약 |
| `short_summary_md` | video_analysis | 짧은 요약 |
| `analysis_sections` | video_analysis | 상세 분석 본문 (구조화) |
| `bullet_points` | video_analysis | 핵심 주장 리스트 |
| `key_points` | video_analysis | 핵심 포인트 |
| `insights` | video_analysis | 인사이트 |
| `entities` | video_analysis | 언급 개체 |
| `sentiment` | video_analysis | 감성 |
| `confidence_score` | video_analysis | 신뢰도 점수 |
| `channel_name` | videos | 채널명 |
| `published_at` | videos | 게시일 (KST) |
| `duration` | videos | 영상 길이 |
| `tags` | videos | 태그 |
| `video_url` | videos | 영상 링크 |
| `share_link` | (생성) | 웹 공유 링크 |

### 프리셋 상수 (Python)

```python
PRESET_FULL = {"fields": [
    "headline", "analysis_sections", "bullet_points",
    "tags", "channel_name", "published_at", "duration",
    "video_url", "share_link"
]}

PRESET_COMPACT = {"fields": [
    "headline", "one_line", "short_summary_md",
    "sentiment", "confidence_score",
    "video_url", "share_link"
]}
```

### 하위 호환 폴백

`message_template` 키가 없을 경우, `settings_manager.py` 파싱 시 `message_detail` 값을 읽어 해당 프리셋으로 대체한다. DB 스키마 변경 불필요.

```python
# settings_manager.py 파싱 로직
raw_template = d.get("message_template")
if raw_template:
    message_template = raw_template
else:
    detail = d.get("message_detail", "full")
    message_template = PRESET_COMPACT if detail == "compact" else PRESET_FULL
```

## 2. 백엔드 렌더링 엔진

### 변경 파일: `app/services/notify_service.py`

`_build_full()` / `_build_compact()` / `build_message()` 분기를 제거하고 단일 `build_from_template()` 함수로 교체한다.

```python
FIELD_RENDERERS: dict[str, Callable] = {
    "headline":          _render_headline,
    "one_line":          _render_one_line,
    "short_summary_md":  _render_short_summary,
    "analysis_sections": _render_analysis_sections,
    "bullet_points":     _render_bullets,
    "key_points":        _render_key_points,
    "insights":          _render_insights,
    "entities":          _render_entities,
    "sentiment":         _render_sentiment,
    "confidence_score":  _render_confidence,
    "channel_name":      _render_channel_name,
    "published_at":      _render_published_at,
    "duration":          _render_duration,
    "tags":              _render_tags,
    "video_url":         _render_video_url,
    "share_link":        _render_share_link,
}

def build_from_template(video, analysis, template: dict, *, threshold: float = 0.0, include_share_link: bool = True) -> str:
    parts = []
    low_conf = _is_low_confidence(analysis, threshold)
    if low_conf:
        parts.append("⚠️ <b>[저신뢰도 분석]</b>")
    for field_key in template.get("fields", []):
        renderer = FIELD_RENDERERS.get(field_key)
        if renderer:
            rendered = renderer(video, analysis)
            if rendered:
                parts.append(rendered)
    return _join_and_truncate(parts)
```

**저신뢰도 배지(⚠️):** `confidence_score` 필드의 템플릿 포함 여부와 무관하게, 임계값 미만이면 항상 첫 줄에 표시한다.

**`share_link` 필드와 `include_share_link` 설정:** `share_link`가 템플릿 필드로 포함되면 공유 링크를 렌더링한다. 기존 `include_share_link` boolean 설정은 이 필드가 담당하므로 `NotificationSettings`에서 제거한다.

**기존 호출부 변경:** `videos.py`, `notify_service.py`, `monitor_service.py` 총 3곳에서 `detail=notif.message_detail` → `template=notif.message_template`으로 교체.

## 3. 설정 타입

### `app/services/settings_types.py`

```python
# 변경 전
message_detail: str = "full"  # full | compact

# 변경 후
message_template: dict = field(default_factory=lambda: PRESET_FULL)
```

## 4. 프론트엔드 UI

### `frontend/src/settings/defs.ts`

`FieldType`에 `template_builder` 추가:

```typescript
type FieldType = ... | 'template_builder'
```

`message_detail` 필드를 `message_template`으로 교체:

```typescript
{
  key: 'message_template',
  label: '메시지 템플릿',
  type: 'template_builder',
  help: '포함할 필드를 선택하고 순서를 조정하세요'
}
```

### `TemplateBuilder` 컴포넌트

두 영역으로 구성:

- **포함된 필드 (위):** 현재 템플릿의 필드들을 순서대로 표시. ▲▼ 버튼으로 순서 변경, × 버튼으로 제거.
- **추가 가능한 필드 (아래):** 아직 포함되지 않은 필드 목록. + 버튼으로 추가.

컴포넌트 상단에 프리셋 초기화 버튼 제공:

```
[Full 기본값으로]  [Compact 기본값으로]
```

저장은 기존 `SettingsForm` 플로우를 그대로 사용하며 `message_template` 값을 JSON 직렬화해 전송한다.

## 5. 영향 범위 요약

| 파일 | 변경 내용 |
|---|---|
| `app/services/settings_types.py` | `message_detail` → `message_template: dict` |
| `app/services/notify_service.py` | 렌더러 딕셔너리 + `build_from_template()` 도입, 기존 `_build_full`/`_build_compact` 제거 |
| `app/services/settings_manager.py` | `message_template` JSON 파싱 + `message_detail` 폴백 로직 |
| `app/services/default_settings.py` | `message_detail` → `message_template` 기본값 (PRESET_FULL JSON) |
| `app/routers/videos.py` | `detail=` → `template=` 호출 변경 |
| `app/services/monitor_service.py` | 동일 |
| `frontend/src/settings/defs.ts` | `FieldType` 확장, `message_detail` → `message_template` 필드 |
| `frontend/src/components/SettingsForm.tsx` | `template_builder` 타입 렌더링 분기 추가 |
| `frontend/src/components/TemplateBuilder.tsx` | 신규 컴포넌트 |
