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
| 용량 상한(바이트) sprite | 8388608 |
| 용량 상한(바이트) portrait | 3145728 |
| 용량 상한(바이트) background | 10485760 |
| 용량 상한(바이트) mascot | 3145728 |
| 저장 루트 | content/images |
| manifest | content/images/manifest.json |

- 용량 상한은 kind별 행이 있으면 그 값, 없으면 공통 값(500KB = 512,000바이트, P-15)이다. 반실사 실제 그림을 위해 sprite 8MB, 초상 3MB, 장면 배경 10MB, 마스코트 3MB로 크게 잡았다(11/5 PM 결정, 저장소·번들이 조금 무거워지는 것은 감수). 임시 유지 유형(장소·UI 아이콘, 지도, 광고)은 공통 값이다. 넘으면 크기·형식은 그대로 두고 6절의 허용 방법(무손실 재압축)으로 줄인다.
- 이미지 안에 글자(한글·한자·영문·숫자), 실존 인물, 상표·로고를 넣지 않는다. 글자가 필요하면 화면(HTML)이 쓴다. 예외: 임시 sprite·초상·장면 배경의 "임시" 이름표(5절).

## 2. 유형별 규격

| kind | 묶음 | 크기(px) | ID 출처 | 파일 경로 | url |
|---|---|---|---|---|---|
| portrait | 인물 초상 | 512×512 | `fixtures/content_min/bundle.json`의 `characters[].npc_id` + 무당 `mudang_f`·`mudang_m` | `content/images/portrait/<id>.png` | `/static/assets/portrait/<id>.png` |
| sprite | 인물 전신 그림 | 512×1280 | portrait와 같은 인물 13종 | `content/images/sprite/<id>.png` | `/static/assets/sprite/<id>.png` |
| background | 장면 배경 | 1280×720 | `meeting`·`night` 2장 | `content/images/background/<id>.png` | `/static/assets/background/<id>.png` |
| mascot | 마스코트 | 512×512 | `kkachi`(까치)·`jokjebi`(족제비) 2장 | `content/images/mascot/<id>.png` | `/static/assets/mascot/<id>.png` |
| place | 장소 아이콘 | 128×128 | `engine.domain.model.Place` 값 5개 | `content/images/place/<id>.png` | `/static/assets/place/<id>.png` |
| ui | UI 아이콘 | 128×128 | 아래 3절 목록 10개 | `content/images/ui/<id>.png` | `/static/assets/ui/<id>.png` |
| map | 마을 지도 | 1280×720 | `village` 1장 | `content/images/map/<id>.png` | `/static/assets/map/<id>.png` |
| ad | 광고 소재 | SLOT_SIZES | `fixtures/ads/campaigns.json`의 모든 소재(캠페인 상태 무관) × 그 캠페인 지면의 규격 | `content/images/ad/<slot_image_path의 파일명>` | `slot_image_path(image_path, slot)` |

- **무당**: 주인공 초상은 여 `mudang_f`, 남 `mudang_m` 2종이다(11/4 PM 결정). 화면은 판 메타 `mudang_variant`(`f`|`m`)로 고른다(B-08-02). 엔진의 참가자 ID `mudang`(PLAYER_ID)에는 파일을 두지 않는다.
- **sprite**(11/5 크로스체크 17·18): 회의 화면(B-10-07)에 세우는 인물 전신 그림이다(사람마다 1장). 바탕은 투명(RGBA, 8비트 반투명 허용)이고 발끝 기준선과 인물별 키 등급(머리 꼭대기·등신)은 6절이다. 인물 ID는 portrait와 같다.
- **표정 변형**(11/5 PM 결정 P-20, 크로스체크 19): 8·9절의 대상 인물은 기본(평상시) sprite `sprite/<id>.png` 외에 표정마다 `sprite/<id>__<표정 id>.png`를 두고, 초상도 표정마다 `portrait/<id>__<표정 id>.png`로 파생한다. 표정 sprite는 **기본 sprite를 확정한 뒤 그 파일을 편집 도구의 입력으로 넣어 얼굴 영역만 바꿔** 만든다. 처음부터 새로 생성하지 않는다. manifest 표정 sprite 항목에 `"edited_from": "sprite:<id>"`가 붙고, 검사는 그 인물의 얼굴 영역(6절, 키 등급에서 계산) 밖이 기본과 같은지 본다. 기본 sprite를 다시 만들면 그 인물의 표정 sprite도 새 기본에서 다시 만든다.
- **마스코트**(11/5 PM 결정 P-21): 까치 `kkachi`(아침 희생자 발표 장면)와 족제비 `jokjebi`(무당의 짝, 안내·도움말)다. 512×512 RGBA 바탕 투명, 표정 없음. 연출·안내만 하고 새 게임 정보를 주지 않는다(스타일 가이드 6절).
- **초상은 sprite에서 파생**한다: sprite에서 인물별 크롭 위 y(6절)부터 512×512를 잘라 먹남(`#2E344A`) 위에 알파 합성한다(6절). 한 인물은 sprite 1장만 만들고, 초상은 `make_placeholders`가 만든다. manifest의 초상 항목에 `"derived_from": "sprite:<id>"`가 붙고, 쌍의 `placeholder`는 같아야 한다(4절).
- **장면 배경**: 회의 `meeting`(회의 장소 고정 1장, 장소는 B-09-06이 정함), 밤 `night`(밤 마을 공통 1장). 장소별 배경은 만들지 않는다(7절).
- **로고**(11/9 사용자 결정): 판 시작 전 로그인·홈 화면에만 쓰는 호랑이 로고다. 원본은 B의 시안 공유 폴더 `branding/`의 호랑이 엠블럼(시안 로그인 화면 머리에 쓴 파일, B-10-08이 정해 `SOURCES.md`에 적음)이고, 게임 장면(회의·밤)에는 쓰지 않는다(기획서 1.4 세계관). 이미지 안 글자 금지는 다른 유형과 같다. ID는 `branding:tiger_emblem`, 크기 512×512 RGBA, 파일 `content/images/branding/tiger_emblem.png`, url `/static/assets/branding/tiger_emblem.png`이다. 위 표의 `branding` 행은 표를 읽는 도구가 그 유형을 알게 되는 B-10-08에서 `tools/assets/spec.py` `KINDS`·검사와 함께 더한다(그 전에 행만 넣으면 에셋 도구가 알 수 없는 유형으로 멈춤).
- **광고**: 크기는 계약 `ads/domain/model.py`의 `SLOT_SIZES`(728×90: `lobby_banner`·`result_banner`, 300×250: `review_side`)를 그대로 쓴다. 파일명은 `slot_image_path(image_path, slot)`의 마지막 조각이다(예: `/static/ads/gat.png`, `review_side` → `content/images/ad/gat__300x250.png`, url `/static/ads/gat__300x250.png`). 한 소재의 두 지면이 같은 규격이면 파일은 1장이다. 합성 캠페인(`syn:`)은 같은 파일을 가리키고 따로 만들지 않는다.
- **지도**: ID는 `village`다. 11/5 PM 결정으로 지도는 **시연까지 임시 에셋**이다. 장소 5곳의 영역 좌표는 E-08-07이 임시 지도용 안내 고정값(정보가 없는 균등 배치)으로 manifest 지도 항목의 `regions`에 넣었다(B-10-02 아이콘 배치용). 계약 `Place` 순서대로 지도 가로를 장소 수로 나눈 칸의 가운데, 세로 가운데에 장소 아이콘 크기(128×128) 상자다(`spec.map_regions`). 위치는 장소 사이 거리·방향을 뜻하지 않는다.
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

항목 배열이다. 키 (`kind`, `id`, `size`)는 유일하다. 항목은 kind 순서(portrait, sprite, background, mascot, place, ui, map, ad) 다음 id, size 순서로 정렬한다.

```json
[
  {"kind": "portrait", "id": "hunjang", "path": "content/images/portrait/hunjang.png",
   "url": "/static/assets/portrait/hunjang.png", "placeholder": true, "derived_from": "sprite:hunjang"},
  {"kind": "sprite", "id": "hunjang", "path": "content/images/sprite/hunjang.png",
   "url": "/static/assets/sprite/hunjang.png", "placeholder": true},
  {"kind": "sprite", "id": "hunjang__joy", "path": "content/images/sprite/hunjang__joy.png",
   "url": "/static/assets/sprite/hunjang__joy.png", "placeholder": true, "edited_from": "sprite:hunjang"},
  {"kind": "ad", "id": "cr_gat", "size": [300, 250], "path": "content/images/ad/gat__300x250.png",
   "url": "/static/ads/gat__300x250.png", "placeholder": true}
]
```

| 필드 | 뜻 |
|---|---|
| `kind` | `portrait`·`sprite`·`background`·`mascot`·`place`·`ui`·`map`·`ad` |
| `id` | 콘텐츠 ID. 광고는 `creative_id`. 표정 변형은 `<인물 id>__<표정 id>`(8절) |
| `size` | 광고만. `[가로, 세로]` |
| `path` | 저장소 기준 파일 경로 |
| `url` | 웹 주소 |
| `placeholder` | 임시 파일이면 `true`. 실제 파일로 덮어쓰면 `false` |
| `derived_from` | 초상만. 원본 sprite 키(`sprite:<id>`). 쌍의 `placeholder`가 다르면 대조가 `pair_mismatch`로 실패한다 |
| `edited_from` | 표정 변형 sprite만. 편집 입력으로 쓴 기본 sprite 키(`sprite:<인물 id>`). 둘 다 실제면(또는 표정만 실제면) 얼굴 영역 밖이 같아야 한다(6절). 기본이 실제이고 표정이 임시인 것은 표정 작업 전 정상 상태다 |
| `regions` | 지도만. `{장소 id: [x, y, 가로, 세로]}`(지도 픽셀). 장소 id 전부를 한 번씩, 지도 안의 겹치지 않는 상자로 둔다(`check_assets.py`가 검사, E-08-07 6번) |

- B는 `asset_url(content_id)`처럼 manifest를 조회해서만 이미지를 넣는다(B-05-03 4번). D는 캠페인의 `image_path`와 `slot_image_path`로만 소재를 가리킨다(D-05-03 7번). 경로 문자열을 코드에 쓰지 않는다.

## 5. 임시 세트

- `python -m tools.assets.make_placeholders`가 1~3절과 ID 출처로 모든 항목을 만든다. 같은 입력이면 같은 바이트가 나온다.
- 임시 에셋 동일성 = 해상도·채널·디코드 픽셀(`tools/assets/png.py` `same_image`). 압축 바이트 차이는 허용한다(Windows 공식 Python의 zlib-ng 등). 생성기는 기존 임시 PNG가 같은 그림이고 용량 상한 이내면 파일을 그대로 둔다. 용량 상한·실제 에셋 SHA-256(`edited_from_sha256`)은 파일 바이트 기준이다.
- **임시 에셋은 회색 실루엣과 이름 표기다. 크기·기준선·배치 확인용이며 최종 그림이 아니다**(11/5 PM 결정).
  - sprite(`tools/assets/portrait_art.py`): 회색 사람 실루엣(바탕 투명), 인물의 키 등급(6절)대로 같은 축척으로 그린다. 눈 y(등급별)·평소 표시 경계 y=640 점선, 발끝 y=1248, 어깨 아래에 "임시"와 표시 이름 2줄. 키 등급과 머리 장식(갓·쪽머리 등)만 공개 인물 ID로 다르고 나머지 실루엣 비율은 모두 같다. 역할·규칙·해시로 모양을 고르지 않는다.
  - 초상: 그 sprite의 인물별 512×512 합성 크롭(6절, 이름표가 보인다).
  - 표정 변형: 기본과 같은 실루엣에 얼굴 영역 안에만 표정 이름(기쁨·당황·슬픔)을 찍는다(얼굴 밖은 기본과 같은 바이트).
  - 마스코트(`tools/assets/mascot_art.py`): 단순한 회색 새·족제비 실루엣(바탕 투명)과 "임시"/이름.
  - 장면 배경(`tools/assets/background_art.py`): 2색 그라데이션 + 왼쪽 위 "회의 배경(임시)"·"밤 배경(임시)".
  - 나머지(장소·UI 아이콘, 지도, 광고)는 단색 배경 + 도형, 글자 없음.
  - 이름은 콘텐츠 묶음의 공개 표시 이름(`name_ko`, 무당은 "무당(여)"·"무당(남)")이다. 글자는 저장소에 커밋한 비트맵(`tools/assets/glyph_data.py`)만 써서 노트북 글꼴과 무관하게 같은 바이트가 나온다. 비트맵에 없는 글자가 있는 이름은 인물 ID로 찍힌다. 인물을 추가하면 `python -m tools.assets.build_glyphs`(Windows, 관리자만)로 비트맵을 다시 만든다.
- 이미 실제 파일(manifest `placeholder: false`)이 있는 항목은 덮어쓰지 않는다. 표시가 `true`여도 파일이 임시 그림과 다른 그림(픽셀)이면 덮어쓰지 않고 보고한다(`--force`로만 덮어씀).
- **실제 파일을 넣으면 바로 그 항목을 `"placeholder": false`로 바꾸고 커밋한다.** 실제 sprite를 넣고 표시를 바꾼 뒤 `make_placeholders`를 실행하면 초상이 그 sprite에서 다시 잘리고 초상 표시도 `false`가 된다. 초상 파일을 따로 그려 넣지 않는다.
- 이전 절차(11/5, 1회): 흉상 생성기로 만든 임시 초상은 새 파생 결과와 바이트가 달라 보호에 걸린다. `python -m tools.assets.make_placeholders --rederive-portraits`는 `placeholder: true`인 초상만 덮어쓴다(실제 파일·다른 유형은 보호 그대로).
- 현재 픽스처 기준 기대 수: portrait 37(주민 11 + 무당 2 + 표정 변형 24), sprite 37, background 2, mascot 2, place 5, ui 10, map 1, ad 8 = **102**. 표정 변형 24는 9절 대상 8명 × 표정 3개다(E-05-03에서 첫 플레이 인물 2명을 넣어 90 → 102).
- 임시 파일은 에셋2(생성 기록, `SOURCES.md`)를 면제한다.

## 6. sprite 기준선과 파일 형식 (11/5 PM 결정, 크로스체크 18)

화풍은 애니풍에 가까운 반실사 일러스트다([`style_guide.md`](style_guide.md)). 파일 크기는 2절 그대로다. 색 수·반투명·선은 검사하지 않고 스타일 가이드 체크리스트로 사람이 본다.

| 기준 | 값(px) | 검사 |
|---|---|---|
| sprite | 512×1280 RGBA, 8비트 알파(반투명 허용) | 자동 |
| 가로 가운데 | 보이는 픽셀(알파 16 이상) 경계 상자의 가운데 256±16 | 자동 |
| 머리 위 여백 | 16 이상 | 자동 |
| 머리 꼭대기 | 인물의 키 등급 값(아래 표). 모든 인물이 같은 축척 | 사람 검수 |
| 발끝 | y=1248, 아래 여백 16~48 | 자동 |
| 장면 배경 | 1280×720, 완전 불투명(RGB, 또는 알파가 모두 255인 RGBA) | 자동 |
| 표정 변형 | 그 인물의 얼굴 영역(아래 표) 밖이 기본 sprite와 정확히 같음(완전 투명 픽셀의 RGB는 무시), 얼굴 영역은 기본과 달라야 함(표정 변화 없음 금지). 기본이 표정을 만든 뒤 바뀌면(`edited_from_sha256` 불일치) 위반 | 자동 |
| 마스코트 | 512×512 RGBA, 네 모서리 투명. sprite와 같은 판정(알파 16 이상)으로 가로 가운데 256±16, 아래 여백 16~48(발끝 기준선 y=480) | 자동 |

**키 등급(11/6 PM 결정)**: 모든 인물을 같은 축척으로 그리고, 키는 인물마다 다르다. 발끝은 모두 y=1248이다. 머리 높이 h = (1248 − 머리 꼭대기) / 등신, 눈 y = 머리 꼭대기 + h/2. 머리 꼭대기는 머리카락 윗선이며, 갓·상투 같은 장식은 그 위로 올라가도 되지만 머리 위 여백 16px은 지킨다(11/6 PM 결정). 등급은 역할과 무관한 고정 표다(`vn.HEIGHT_GRADES`, `vn.CHARACTER_GRADE`). 새 인물은 등급을 정해 이 표와 코드에 함께 넣는다.

| 등급 | 머리 꼭대기 y | 등신 | 머리 높이 | 눈 y | 얼굴 영역 x | 얼굴 영역 y | 초상 안 눈 y | 초상 크롭 위 y | 인물 |
|---|---|---|---|---|---|---|---|---|---|
| 큰 키 `tall` | 48 | 7.2 | 167 | 131 | 156~355 | 31~245 | 200 | 0 | `blacksmith`, `hunter` |
| 남성 보통 `male` | 94 | 7 | 165 | 176 | 157~354 | 78~289 | 200 | 0 | `hunjang`, `meosum`, `yakcho`, `ferryman`, `monk`, `mudang_m` |
| 여성 보통 `female` | 185 | 6.8 | 156 | 263 | 162~349 | 169~370 | 200 | 63 | `jumo`, `gwabu`, `gisaeng`, `mudang_f` |
| 아이 `child` | 339 | 6 | 151.5 | 415 | 165~346 | 324~518 | 230 | 185 | `boy` |

- 얼굴 영역 = x 256 ± 0.6h, y (눈 − 0.6h) ~ (눈 + 0.69h), 정수 반올림(표는 끝 포함 범위). 표정 변형은 이 영역만 바꾼다.
- 초상 크롭 위 y = 눈 y − 초상 안 눈 y(반올림)를 0~768로 자른 값이다. 초상 안 눈 y는 등급별 값이다(아이는 여성보다 살짝 아래, `vn.HEIGHT_GRADES`). 위가 0으로 잘리는 등급(큰 키·남성 보통)은 눈이 그보다 위에 온다.
- 평소 표시(7절)는 인물과 관계없이 0~640행이다. 키가 작은 인물은 화면에서 낮게 서고, 이것이 의도다.
- 원래 비율로 그린다. 가로·세로 배율을 다르게 늘리거나, 다리만 늘리거나, 512×1280 틀을 채우려 그림을 늘리지 않는다. 원래 비율로 줄여 발끝을 맞추고 위에 남는 부분은 투명으로 둔다(스타일 가이드 4절).

- 자동 검사는 `tools/assets/vn.py`이고 `make_placeholders`가 실행한다. 알파 16 미만은 배경 제거 뒤 남은 옅은 점으로 보고 경계 상자에 넣지 않는다.
- **PNG 형식**: 8비트 RGB(색 형식 2)·RGBA(색 형식 6), 인터레이스 없음만 쓴다. 팔레트 PNG·16비트·흑백은 쓰지 않는다(실제 sprite를 크롭하는 읽기 도구가 읽지 못해 실패로 보고한다).
- **용량을 줄이는 방법**: 크기·형식은 그대로 두고 무손실 재압축(행별 필터 선택, zlib 최고 단계)만 한다. 색을 줄이는 양자화·손실 압축은 반투명 가장자리를 깨므로 쓰지 않는다. 그래도 500KB를 넘으면 A-08-04 측정 결과로 PM이 정한다.
- **초상 파생**: sprite에서 인물별 크롭 위 y(위 키 등급 표)부터 512×512를 잘라 먹남(`#2E344A`) 위에 알파 합성한다. 색 = round((c·a + 먹남·(255 − a)) / 255). 파생 초상은 무손실 필터 선택으로 저장하고, 검사는 크롭 결과와 픽셀이 같은지 본다.
- **가장자리 색 번짐**: 반투명 가장자리 픽셀의 RGB는 인물 색이어야 한다(바탕색이 섞이지 않은 straight alpha). 밝은 바탕·어두운 바탕·회의 배경 위와 축소 표시에서 사람이 본다(스타일 가이드 체크리스트).

## 7. 비주얼 노벨 화면 배치와 유형별 실제/임시 정책 (11/5 PM 결정, 크로스체크 16·17)

회의·밤 화면의 비주얼 노벨 연출(B-10-07)에 쓰는 sprite·장면 배경의 배치 기준이다. 규격은 2·6절, 그림 기준은 [`style_guide.md`](style_guide.md)다. 수치는 `tools/assets/vn.py`와 같다.

| 항목 | 값 |
|---|---|
| 무대 | 1280×720(장면 배경 크기, 좁은 화면·200% 확대에서는 비율 유지 축소) |
| 평소 sprite | 회의만, 말하는 사람 1명. sprite 0~640행(머리~허리)을 부드럽게 0.8배 축소해 410×512, 가로 가운데(x 435~845), 위 y 28, 아랫변 y 540(대사창 윗변). 행 범위는 인물과 무관하게 고정이라 키가 작은 인물은 낮게 선다(6절 키 등급) |
| 전신 컷 | sprite 전체를 0.5배 축소해 256×640, 가로 가운데(x 512~768), 위 y 40. 컷 동안 대사창을 숨기고, 클릭하면 대사창이 돌아온다 |
| 전신 컷 조건 | 첫 등장(자기소개), 추방·처형 결과, 엔딩, 밤 습격 결과(아침 회의에서 피해자를 발표할 때) |
| 대사창 | 무대 아래 180px(1/4)를 덮는다. 표시 방식만이며 글자는 화면(HTML)이 쓴다 |
| 선택지 | 무대 안 패널(대사창 옆 또는 위, 11/9 사용자 원칙: 조작은 플레이 화면 안에서, B-10-07 7번). 진술표·수첩·투표 결과·내 점괘는 무대 위 창 |
| 밤 | sprite 0명(밤 실행자 노출 금지). 무당 독백은 대사창만 |
| 엔딩 | 평소와 같은 그림. 정체는 글로만 밝힌다 |
| 투명 sprite 실패 시 | 불투명 흉상(초상)을 액자처럼 무대 가운데에 둔다(A-08-04 시험 결과로 결정) |
| 표정 | 평소·전신 컷 모두 말하는 인물의 표정 sprite로 바꿔 끼운다. 표정이 없는 인물(추가 주민, 첫 플레이 8명 밖)은 기본 그림 |
| 마스코트 | 0.5배 256×256, 무대 왼쪽 x 64, 위 y 260(아랫변 516, 대사창 위). 까치는 **매일 아침 발표 때마다**(희생자가 없는 아침 포함, 이미 표시된 발표 문장과 함께), 족제비는 안내(튜토리얼·도움말)에만. 밤 화면에는 없다 |

**표정 매핑(정보 누설 금지, 크로스체크 19)**: 표정은 **지금 화면에 표시 중인 공개 사건**에만 반응한다.
- 역할(씐 자 여부), 거짓 진술 여부, 숨은 행적·의심도 같은 비공개 상태로 표정을 고르지 않는다. 같은 사건이면 누구든 같은 표정이다.
- 표정은 인물의 최신 서버 상태가 아니라 **표시 중인 대사·사건(사건 ID)**에 묶는다. 아직 읽지 않은 뒤 사건(고백·투표 결과 등)의 표정을 앞 대사에서 미리 보이지 않는다. 넘김·건너뛰기·새로고침·재접속에서도 같은 사건이면 같은 표정이다.
- 기색은 "판정을 했다"가 아니라 **"기색이 나타났다"고 화면에 표시된 결과**에만 반응한다(판정 여부 자체를 표정으로 드러내지 않는다).
- 표정은 그 사건의 대사 1개 동안만 유지하고 다음 대사에서 평상시로 돌아온다. 한 대사에 여러 사건이 겹치면 우선순위 슬픔 > 당황 > 기쁨 > 평상시.
- 화면에는 한 번에 1명(화자 또는 전신 컷 대상)만 서므로, 반응 인물이 지금 선 사람일 때만 표정을 바꾼다.

| 표정 | id | 공개 사건 | 반응 인물 |
|---|---|---|---|
| 평상시 | (기본) | 그 밖의 모든 대사 | — |
| 기쁨 | `joy` | 감싸기 발언 / 투표 결과 발표 직후(표를 받았지만 추방되지 않음) | 감싸기 발언의 화자 / 표를 받고 남은 인물의 그 직후 첫 대사 |
| 당황 | `flustered` | 의심하기 발언의 대상이 됨 / 자기 주장에 "기색이 나타났다"가 표시됨 | 의심 대상의 바로 다음 자기 대사 / 그 주장 화자의 기색 표시 대사 |
| 슬픔 | `sad` | 희생자 발표(아침) / 고백 / 추방 결과 | 발표 직후 각 인물의 그날 첫 대사 / 고백하는 화자 / 추방 결과 전신 컷의 당사자 |

- 검수용 합성: `python -m tools.assets.preview_vn --bg meeting --id hunjang [--expr joy] [--mode normal|full] [--mascot kkachi] --out <파일>`(저장소 밖 또는 `out/` 아래만). 배경 + sprite + 마스코트 + 대사창 사각형을 겹친 1280×720 PNG를 쓴다. `--bg night`에 `--id`나 `--mascot`을 주면 오류다.
- 장소별 배경은 만들지 않는다. 숨은 행적·실제 장소에 따라 배경을 고르면 거짓 진술을 화면이 판정해 주기 때문이다.
- 외형(sprite·초상·크기·폴백)은 공개 인물 ID로만 정한다. 같은 인물은 역할이 바뀌어도, 엔딩에서도 같은 그림이다(`tests/assets/test_vn.py`).

**유형별 실제/임시 정책**

| kind | 시연 때 | 검사 |
|---|---|---|
| portrait | 실제 필수(sprite에서 파생) | 기본 모드(에셋2~4) |
| sprite | 실제 필수(표정 변형 포함) | 기본 모드(에셋2~4) |
| background | 실제 필수 | 기본 모드(에셋2~4) |
| mascot | 실제 필수 | 기본 모드(에셋2~4) |
| place, ui, map, ad | 임시 허용(`placeholder: true`) | `check_assets.py --allow-placeholder place,ui,map,ad`(E-08-07 7번). 실제 필수 유형의 검사는 생략하지 않는다 |

- 임시 아이콘은 도형뿐이므로 화면이 기능·장소 이름 텍스트를 함께 쓴다(B-10-02).
- 실제 세트 검사 명령: 임시 세트 `python -m tools.assets.check_assets --placeholder`, 시연 기준 `python -m tools.assets.check_assets --allow-placeholder place,ui,map,ad`, 모든 유형 실제 `python -m tools.assets.check_assets`. 어느 모드든 실제 파일(`placeholder: false`)은 에셋2(`SOURCES.md` 기록 행)~4를 모두 본다.
- 광고 소재가 임시라 사람 클릭률은 소재 효과로 해석하지 않는다. 합성 CTR 검증(D)은 그림과 무관하다.

## 8. 표정 목록

표정 변형 파일의 표정 id와 이름(11/5 PM 결정 P-20). 평상시는 기본 sprite라 이 표에 넣지 않는다. 쓰는 때는 7절 표정 매핑이다. `make_placeholders`가 이 표를 읽는다.

| 표정 id | 이름 |
|---|---|
| joy | 기쁨 |
| flustered | 당황 |
| sad | 슬픔 |

## 9. 표정 대상

표정 변형을 만드는 인물이다. 첫 플레이 8명(무당 2종 + 첫 플레이 인물 6명, `content/bundle_v1.json`)이고, 추가 주민(7·8인 판용, 11/5 범위 축소)은 기본 1장만 둔다(11/5 PM 결정). 나머지 2명 `yakcho`·`gwabu`는 E-05-03에서 정했다.

| 인물 id |
|---|
| mudang_f |
| mudang_m |
| hunjang |
| jumo |
| blacksmith |
| meosum |
| yakcho |
| gwabu |
