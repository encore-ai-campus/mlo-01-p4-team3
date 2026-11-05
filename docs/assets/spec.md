# 에셋 파일 규격 (E-05-05)

이미지 파일의 형식·크기·용량·이름·저장 위치와 UI 아이콘 목록의 **원본**이다. `tools/assets/make_placeholders.py`가 이 문서의 1~3절 표를 읽어 임시 세트를 만들고, 검사 도구(`tools/assets/check_assets.py`, E-08-07)도 같은 표를 기준으로 삼는다. 표의 제목·열 순서를 바꾸면 스크립트가 실패한다.

- 결정 근거: E_pm.md E-05-05 완료 조건 1~3(11/4 PM 결정), 용량 상한 P-15(잠정), 광고 지면 규격 P-19(잠정, 계약 `ads-0.3`).
- 파일명·규격은 이 태스크에서 정한 뒤 바꾸지 않는다. 임시 → 실제 교체는 **같은 경로에 파일만 덮어쓰고** manifest의 `placeholder`만 `false`로 바꾼다.
- 그림 내용(화풍, 색, 구도, 금지 요소)은 [`style_guide.md`](style_guide.md)를 따른다.

## 1. 공통 규칙

| 항목 | 값 |
|---|---|
| 형식 | PNG |
| 용량 상한(바이트) | 512000 |
| 저장 루트 | content/images |
| manifest | content/images/manifest.json |

- 용량 상한은 모든 파일에 적용한다(500KB = 512,000바이트, P-15). 넘으면 크기는 그대로 두고 색 수를 줄이거나 다시 압축한다.
- 이미지 안에 글자(한글·한자·영문·숫자), 실존 인물, 상표·로고를 넣지 않는다. 글자가 필요하면 화면(HTML)이 쓴다.

## 2. 유형별 규격

| kind | 묶음 | 크기(px) | ID 출처 | 파일 경로 | url |
|---|---|---|---|---|---|
| portrait | 인물 초상 | 512×512 | `fixtures/content_min/bundle.json`의 `characters[].npc_id` + 무당 `mudang_f`·`mudang_m` | `content/images/portrait/<id>.png` | `/static/assets/portrait/<id>.png` |
| place | 장소 아이콘 | 128×128 | `engine.domain.model.Place` 값 5개 | `content/images/place/<id>.png` | `/static/assets/place/<id>.png` |
| ui | UI 아이콘 | 128×128 | 아래 3절 목록 10개 | `content/images/ui/<id>.png` | `/static/assets/ui/<id>.png` |
| map | 마을 지도 | 1280×720 | `village` 1장 | `content/images/map/<id>.png` | `/static/assets/map/<id>.png` |
| ad | 광고 소재 | SLOT_SIZES | `fixtures/ads/campaigns.json`의 모든 소재(캠페인 상태 무관) × 그 캠페인 지면의 규격 | `content/images/ad/<slot_image_path의 파일명>` | `slot_image_path(image_path, slot)` |

- **무당**: 주인공 초상은 여 `mudang_f`, 남 `mudang_m` 2종이다(11/4 PM 결정). 화면은 판 메타 `mudang_variant`(`f`|`m`)로 고른다(B-08-02). 엔진의 참가자 ID `mudang`(PLAYER_ID)에는 파일을 두지 않는다.
- **광고**: 크기는 계약 `ads/domain/model.py`의 `SLOT_SIZES`(728×90: `lobby_banner`·`result_banner`, 300×250: `llm_wait`·`review_side`)를 그대로 쓴다. 파일명은 `slot_image_path(image_path, slot)`의 마지막 조각이다(예: `/static/ads/gat.png`, `review_side` → `content/images/ad/gat__300x250.png`, url `/static/ads/gat__300x250.png`). 한 소재의 두 지면이 같은 규격이면 파일은 1장이다. 합성 캠페인(`syn:`)은 같은 파일을 가리키고 따로 만들지 않는다.
- **지도**: ID는 `village`다. 11/5 PM 결정으로 지도는 **시연까지 임시 에셋**이다. 장소 5곳의 영역 좌표는 E-08-07이 임시 지도용 안내 고정값(정보가 없는 균등 배치)으로 manifest에 넣는다(B-10-02 아이콘 배치용). 현재 임시 지도에는 좌표가 없다.
- **정적 파일 연결**(B-05-03 5번): `/static/assets/<kind>/…` → `content/images/<kind>/…`, `/static/ads/…` → `content/images/ad/…`.

## 3. UI 아이콘 목록

| 아이콘 ID | 뜻 | 대응 이름 |
|---|---|---|
| divination_clear | 점괘 맑음 | `DivinationResult.CLEAR` |
| divination_murky | 점괘 탁함 | `DivinationResult.MURKY` |
| geumjul | 금줄(감금) | 감금 상태 |
| vote | 투표 | `CommandKind.VOTE` |
| suspect | 의심 | `CommandKind.SUSPECT` |
| defend | 감싸기 | `CommandKind.DEFEND` |
| interrogate | 캐묻기 | `CommandKind.INTERROGATE` |
| confess | 고백 | `ReactionIntent.CONFESS` |
| missing | 실종(짚신) | 아침 실종 발표 |
| notebook | 수첩 | 수첩 화면 |

## 4. manifest.json 형식

항목 배열이다. 키 (`kind`, `id`, `size`)는 유일하다. 항목은 kind 순서(portrait, place, ui, map, ad) 다음 id, size 순서로 정렬한다.

```json
[
  {"kind": "portrait", "id": "hunjang", "path": "content/images/portrait/hunjang.png",
   "url": "/static/assets/portrait/hunjang.png", "placeholder": true},
  {"kind": "ad", "id": "cr_gat", "size": [300, 250], "path": "content/images/ad/gat__300x250.png",
   "url": "/static/ads/gat__300x250.png", "placeholder": true}
]
```

| 필드 | 뜻 |
|---|---|
| `kind` | `portrait`·`place`·`ui`·`map`·`ad` |
| `id` | 콘텐츠 ID. 광고는 `creative_id` |
| `size` | 광고만. `[가로, 세로]` |
| `path` | 저장소 기준 파일 경로 |
| `url` | 웹 주소 |
| `placeholder` | 임시 파일이면 `true`. 실제 파일로 덮어쓰면 `false` |

- B는 `asset_url(content_id)`처럼 manifest를 조회해서만 이미지를 넣는다(B-05-03 4번). D는 캠페인의 `image_path`와 `slot_image_path`로만 소재를 가리킨다(D-05-03 7번). 경로 문자열을 코드에 쓰지 않는다.

## 5. 임시 세트

- `python -m tools.assets.make_placeholders`가 1~3절과 ID 출처로 모든 항목을 만든다. 초상은 Claude가 코드로 그린 도트 그림(`tools/assets/portrait_art.py`: 스타일 가이드 격자 128×128·팔레트 17색·외곽선, ×4 확대)이고, 나머지는 단색 배경 + 도형이다. 모두 글자가 없고, 같은 입력이면 같은 바이트가 나온다. 인물 모양은 코드의 인물 데이터로 정하고 규칙·해시로 고르지 않는다(주민은 전원 같은 처리).
- 이미 실제 파일(manifest `placeholder: false`)이 있는 항목은 덮어쓰지 않는다. 표시가 `true`여도 파일 내용이 임시 그림과 다르면 덮어쓰지 않고 보고한다(`--force`로만 덮어씀).
- **실제 파일을 넣으면 바로 그 항목을 `"placeholder": false`로 바꾸고 커밋한다.**
- 현재 픽스처 기준 기대 수: portrait 13(주민 11 + 무당 2), place 5, ui 10, map 1, ad 8 = **37**.
- 임시 파일은 에셋2(생성 기록, `SOURCES.md`)를 면제한다.

## 6. 도트 격자

화풍은 도트(픽셀 아트)다([`style_guide.md`](style_guide.md)). 원본 격자를 정수 배율로 최근접 확대(안티에일리어싱 없음)해 2절의 파일 크기에 맞춘다. 파일 크기는 바꾸지 않는다.

| kind | 원본 격자 | 배율 | 파일 크기 | 남는 가장자리 |
|---|---|---|---|---|
| portrait | 128×128 | ×4 | 512×512 | 없음 |
| place | 32×32 | ×4 | 128×128 | 없음 |
| ui | 32×32 | ×4 | 128×128 | 없음 |
| map | 320×180 | ×4 | 1280×720 | 없음 |
| ad 728×90 | 182×22 | ×4 | 728×90 | 그림 728×88, 위아래 1px씩 배경색 |
| ad 300×250 | 75×62 | ×4 | 300×250 | 그림 300×248, 위아래 1px씩 배경색 |

- 모든 유형을 ×4로 통일한다. 초상은 128×128 격자다(11/4 PM이 샘플을 보고 64에서 변경).
- 남는 가장자리는 그 그림의 배경색으로 채운다.
- 생성형 AI 출력은 격자에 정확히 맞지 않으므로 원본 격자로 줄이고, 제한 팔레트로 양자화한 뒤 정수 배율로 확대한다(style_guide.md 7절). 원본 격자 파일(확대 전)의 보관 위치는 생성 도구 태스크(E-08-06)에서 정한다.

## 7. 장면 배경과 유형별 실제/임시 정책 (11/5 PM 결정, E-08-07에서 코드 반영)

회의·밤 화면의 비주얼 노벨 연출(B-10-07)에 쓰는 장면 배경을 새 유형으로 둔다. **이 절은 아직 스크립트가 읽지 않는다.** E-08-07(11/8)이 아래 값을 2절 유형 표·4절 kind 목록·6절 격자 표로 옮기고 `spec.py`·`art.py`·`make_placeholders`를 함께 고친다. 그 전에 2절 표에 행을 더하면 임시 세트 생성이 실패한다.

| 항목 | 값 |
|---|---|
| kind | `background` |
| ID | `meeting`(회의 장소 고정 1장, 장소는 E-09-07이 정함), `night`(밤 마을 공통 1장) |
| 크기 | 1280×720(원본 격자 320×180, ×4, 남는 가장자리 없음) |
| 파일 경로 | `content/images/background/<id>.png` |
| url | `/static/assets/background/<id>.png` |
| 그림 | 인물 없음, 글자 없음, 특정 장소·인물을 강조하지 않음(스타일 가이드 5·6절) |
| 임시 그림 | 단색 바탕 + 도형(E-08-07, 인물 없음) |

- 기대 항목 수는 E-08-07이 코드 기준으로 다시 세어 5절에 적는다(현재 37 → 배경 2를 더해 39 예상).
- 장소별 배경은 만들지 않는다. 숨은 행적·실제 장소에 따라 배경을 고르면 거짓 진술을 화면이 판정해 주기 때문이다.

**유형별 실제/임시 정책**

| kind | 시연 때 | 검사 |
|---|---|---|
| portrait | 실제 필수 | 기본 모드(에셋2~4) |
| background | 실제 필수 | 기본 모드(에셋2~4) |
| place, ui, map, ad | 임시 허용(`placeholder: true`) | `check_assets.py --allow-placeholder place,ui,map,ad`(E-08-07 7번). 실제 필수 유형의 검사는 생략하지 않는다 |

- 임시 아이콘은 도형뿐이므로 화면이 기능·장소 이름 텍스트를 함께 쓴다(B-10-02).
- 광고 소재가 임시라 사람 클릭률은 소재 효과로 해석하지 않는다. 합성 CTR 검증(D)은 그림과 무관하다.
