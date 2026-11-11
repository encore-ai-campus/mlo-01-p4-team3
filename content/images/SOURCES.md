Codex 내장 image_gen · ChatGPT(gpt-image) · Gemini(외부 기증 지도) · 비상업 교육 프로젝트용 생성물

## B-10-08 판 시작 전 호랑이 로고

11/9 확정 시안 인수 예외를 따른다. 원본은 시안 공유 폴더 `branding/tiger-emblem-v3.png`이며 SHA-256은 `cfb48d7618733b9ad8601e44d2268cd62cdfb85bbe4c0539364db0070c9e9bf4`다. 원본 그림은 재생성하지 않았다. 최종 검수에서 원본 PNG의 C2PA `caBX` 안 `c2pa.created`·`softwareAgent`를 확인해 도구·모델·생성 시각을 기록했다(서명 검증은 하지 않음). 시안 기록 `tiger-v7-assets.json`의 11/7 날짜와 달리 아래 시각은 UTC다. 판 시작 전 로그인·가입·홈의 로고로만 쓴다.

| 결과 파일 | 도구 | 모델·버전 | 프롬프트 전문 | 부정 프롬프트 | 시드 | 생성 시각 UTC | 가공 |
|---|---|---|---|---|---|---|---|
| `content/images/branding/tiger_emblem.png` | ChatGPT 이미지 생성(C2PA 기록) | gpt-image(C2PA 기록) | 확인 불가(11/9 시안 인수 예외) | 확인 불가(같은 예외) | 도구 미제공 | 2026-11-06T16:22:19.474861855Z | `python -m tools.assets.branding <원본 PNG>`: 알파를 곱한 면적 평균 단일 배율 축소, 정사각 투명 여백 중앙 정렬, 필터 선택·zlib9 무손실 인코딩 |

정규화 결과는 512×512 RGBA·373100 bytes이며 SHA-256은 `da8e0aa3e721e602c3b46c5b686e0af61c5e36c1bef7cf1c6d76c55d1bc2235f`다. 등록 태스크는 B-10-08이며 인물·배경의 에셋 태스크 완료로 계산하지 않는다.

## A-08-04 시험 기록

시험 파일은 `out/`에 보관하며 manifest의 실제 에셋 교체는 A-09-05 이후에 한다. 아래 인물·배경은 11/6 사용자가 시안으로 확인한 자료이며, E의 최종 에셋 검수를 대신하지 않는다. 마을 인물은 별도 시험 인물이고 콘텐츠 캐릭터로 확정하지 않았다. `jumo`·`ferryman`은 처리 시 키 등급을 지정하기 위한 ID다.

| 시험 ID | 결과 파일 | 도구 | 모델·버전 | 시드 | 생성 시각 UTC | 입력·금지 지시 | 기준 그림 |
|---|---|---|---|---|---|---|---|
| trial_female | `out/assets/A-08-04/processed/trial_female_v3.png` | Codex 내장 image_gen | 도구 미제공 | 도구 미제공 | 2026-11-06T05:58:02.603Z | 아래 `trial_female` 프롬프트 전문, 별도 negative prompt 필드는 도구 미지원(본문 금지 지시 사용) | 참고 입력 없음; female 첫 결과가 기준 그림 자신 |
| trial_male | `out/assets/A-08-04/processed/trial_male_v3.png` | Codex 내장 image_gen | 도구 미제공 | 도구 미제공 | 2026-11-06T05:59:15.300Z | 아래 `trial_male` 프롬프트 전문, 별도 negative prompt 필드는 도구 미지원(본문 금지 지시 사용) | `out/assets/A-08-04/independent/raw/trial_female.png` |
| meeting | `out/assets/A-08-04/processed/meeting.png` | Codex 내장 image_gen | 도구 미제공 | 도구 미제공 | 2026-11-06T05:59:35.544Z | 아래 `meeting` 프롬프트 전문, 별도 negative prompt 필드는 도구 미지원(본문 금지 지시 사용) | `out/assets/A-08-04/independent/raw/trial_female.png` |
| repeat | `out/assets/A-08-04/independent/raw/trial_female_repeat.png` | Codex 내장 image_gen | 도구 미제공 | 도구 미제공 | 2026-11-06T06:00:02.134Z | 아래 `repeat` 프롬프트 전문, 별도 negative prompt 필드는 도구 미지원(본문 금지 지시 사용) | 참고 입력 없음; female 첫 결과가 기준 그림 자신 |
| mudang_f | `out/assets/A-08-04/shaman-gat/mudang_f.png` | Codex 내장 image_gen | 도구 미제공 | 도구 미제공 | 2026-11-06T06:05:35.589Z | 아래 `mudang_f` 프롬프트 전문, 별도 negative prompt 필드는 도구 미지원(본문 금지 지시 사용) | `out/assets/A-08-04/shaman-gat/mudang_f-layout.png`, `out/assets/A-08-04/shaman-gat/female-style-draft.png` |
| mudang_m | `out/assets/A-08-04/shaman-gat/mudang_m.png` | Codex 내장 image_gen | 도구 미제공 | 도구 미제공 | 2026-11-06T06:05:04.564Z | 아래 `mudang_m` 프롬프트 전문, 별도 negative prompt 필드는 도구 미지원(본문 금지 지시 사용) | `out/assets/A-08-04/shaman-gat/mudang_m-layout.png`, `out/assets/A-08-04/shaman-gat/female-style-draft.png` |

## 처리 명령·설정

원본을 덮어쓰지 않는다. `cutout` 출력 경로가 이미 존재하면 새 파일명을 사용한다. 배경과 전경이 같은 키 색인 경우와 넓은 반투명 물체·복잡한 배경은 지원하지 않는다. 가장자리 RGB는 알파를 미리 곱하지 않은 straight RGB다. 축척 계산에서 몸 일부를 늘리지 않는다.

- `trial_female`: 단색 #00FF00 요청; 바탕 편차 허용32, 가까운 전경색32px 탐색·straight alpha·despill·단일 배율

```powershell
python -m tools.assets.cutout out/assets/A-08-04/independent/raw/trial_female.png out/assets/A-08-04/processed/trial_female_v3.png --id jumo --head-top 195 --background 00ff00
```

- `trial_male`: 위와 같음

```powershell
python -m tools.assets.cutout out/assets/A-08-04/independent/raw/trial_male.png out/assets/A-08-04/processed/trial_male_v3.png --id ferryman --head-top 114 --background 00ff00
```

- `meeting`: 불투명 배경, 비율 유지 배율의 정수 픽셀 반올림으로1280×720

```powershell
python -B out/assets/A-08-04/verify_trial.py
```

- `repeat`: 가공하지 않은 원본

```powershell
동일 female 요청 추가1회, 원본 비교만
```

- `mudang_f`: 도구가 생성한 투명 RGBA, export.json의 단일 배율·발끝 정렬, matte 제거 시험에는 사용하지 않음

```powershell
./out/assets/A-08-04/shaman-gat/export.ps1
```

- `mudang_m`: 위와 같음

```powershell
./out/assets/A-08-04/shaman-gat/export.ps1
```

## 생성 프롬프트 전문

모든 요청은 저장한 JSON과 아래 전문으로 대조할 수 있다. 도구에 별도로 모델·시드·정확한 픽셀 크기 매개변수를 전달하지 않았다. 크기는 프롬프트 요청이며 실제 출력 크기는 증빙에 따로 적는다.

### trial_female

- `transparent_background`: `false`

```text
A-08-04 independent technical style trial ONLY. Create ONE brand-new ordinary village WOMAN; this is NOT a shaman or a production A-09-05 asset. No reference images. Anime-adjacent semi-realistic 2D Joseon village game illustration, natural adult anatomy with a slightly generous readable head (about seven heads tall, NOT eight or nine), thin charcoal outer contour 2-4px and inner detail1-2px at512px width, soft clean cel-painted shading, restrained ivory/ochre/earth/indigo palette. One consistent upper-left light, lower-right shading, no photographic skin texture, no watercolor or3D. An ordinary Korean woman aged about45, natural unidealized face with very subtle age lines, one small highlight per open eye, closed mouth and calm neutral expression. Practical plain faded ochre short jeogori jacket over simple cream ankle-length chima skirt, clean ivory collar, a small plain rust tie, no patterned borders, no embroidery. Dark hair in a low compact bun with a tiny undecorated wooden hairpin. No ceremonial hat, no ritual robe, no gold trim, no jewelry, no makeup or blush. Empty relaxed hands close to hips, front-facing natural balanced stance, full body and both simple dark cloth shoes visible. Natural warm personality without exaggerated features.
TECHNICAL: a single standalone person on requested512x1280 portrait2:5 canvas. FEMALE grade: hair top y185, chin y341, eyes y263, shoe sole baseline y1248; hair-top-to-foot1063px and6.8-head body proportion. Do not stretch legs. Head is156px tall at final512width; give the body believable compact adult proportions. Width withinx40..472, centeredx256. Leave large empty upper margin14.5% of canvas, bottom32px. All body parts entirely visible with no clipping, including feet. If generated at another resolution retain those ratios. ONE FLAT UNIFORM OPAQUE pure chroma GREEN #00FF00 RGB(0,255,0) background for later software matte extraction, including between arms and body. No green clothes. No ground shadow, gradient, scenery, transparency, checkerboard, text, numbers, grid, labels, borders, logos or watermark. No held objects or props.
```

### trial_male

- `transparent_background`: `false`

```text
A-08-04 INDEPENDENT technical style trial ONLY. Create ONE new ordinary adult Korean village MAN, not a shaman or finalized named character. The only reference is a NEW female trial image generated for A-08-04: match its clean charcoal contour, painted shading, understated palette and frontal standing pose, but create a different person and different clothing. Anime-adjacent semi-realistic 2D Joseon village game illustration, natural adult anatomy with a slightly generous readable head (about seven heads tall, NOT eight or nine), thin charcoal outer contour 2-4px and inner detail1-2px at512px width, soft clean cel-painted shading, restrained ivory/ochre/earth/indigo palette. One consistent upper-left light, lower-right shading, no photographic skin texture, no watercolor or3D. Ordinary Korean man about48 with a natural slightly weathered unidealized face and faint age lines. Both eyes open, one small highlight per eye, closed-mouth neutral expression, no blush or dramatic emotion. Plain faded indigo cotton jeogori and comfortable ash-brown trousers tied at ankles, ivory crossover collar, plain fabric sash, dark cloth shoes. Compact neatly tied hair topknot and a very narrow dark headband, NO tall hat. No gold trim, embroidery, jewelry, no ritual clothing. Empty relaxed hands near hips; same neutral frontal standing pose, fullbody all parts visible. NOT the shaman from any previous artwork.
Requested512x1280 portrait2:5 canvas. MALE grade7.0HEADS tall from hair crown to soles, hair crown y94, chin y259, eyes y176, soles y1248, headheight165px. Natural SEVEN-head ordinary adult anatomy, not tall slender8-head fashion model. Male height1154px versus reference woman's1063px: adult man stands a little taller at the SAME world scale; no giant head. Centerx256, empty hairtop margin94px, bottom32px, keepentiresilhouettewithinframe. Topknot can extend above crown but minimum16px. Use same scale bothaxes, no stretched legs. One flat uniform OPAQUE chroma green #00FF00 background outside silhouette, also betweenarmsbody. No green clothes, shadows, ground, gradient, texture, scenery, transparency, text, grid, labels, watermark or border. No held objects.
```

### meeting

- `transparent_background`: `false`

```text
A-08-04 independent STYLE CONSISTENCY test environment. Create one NEW generic Joseon village daytime meeting courtyard illustration. The ONLY reference is a new ordinary female trial character, for style/linework/palette/light ONLY: DO NOT draw her or ANY human/animal/mascot. Anime-adjacent semi-realistic 2D Joseon village game illustration, natural adult anatomy with a slightly generous readable head (about seven heads tall, NOT eight or nine), thin charcoal outer contour 2-4px and inner detail1-2px at512px width, soft clean cel-painted shading, restrained ivory/ochre/earth/indigo palette. One consistent upper-left light, lower-right shading, no photographic skin texture, no watercolor or3D. An ordinary broad packed-earth village yard with simple low wooden fences and modest thatched or tiled rooftops along distant sides, a few background trees, pale blue sky. Restrained warm ochre earth, desaturated brown timber, muted grey-blue roof tones. Clean readable 2D illustration, same linework and soft shading as the reference. Gentle light from upper-left, shadows toward lower-right, even daylight with no singled-out house or symbolic clue. Viewer faces the courtyard at character eye level. Center(x384..896 of1280width) should be simple and quiet with no foreground objects, to place a speaking sprite. Bottom180px only quiet plain earth for a dialogue overlay. No spotlighted building, no dramatic perspective, no readable signs, ritual symbols or suggestive clues. Requested1280x72016:9 landscape, fully opaque full bleed. NO chroma green, no transparency, no text/labels/interface/logos/watermark/frame.
```

### repeat

- `transparent_background`: `false`

```text
A-08-04 independent technical style trial ONLY. Create ONE brand-new ordinary village WOMAN; this is NOT a shaman or a production A-09-05 asset. No reference images. Anime-adjacent semi-realistic 2D Joseon village game illustration, natural adult anatomy with a slightly generous readable head (about seven heads tall, NOT eight or nine), thin charcoal outer contour 2-4px and inner detail1-2px at512px width, soft clean cel-painted shading, restrained ivory/ochre/earth/indigo palette. One consistent upper-left light, lower-right shading, no photographic skin texture, no watercolor or3D. An ordinary Korean woman aged about45, natural unidealized face with very subtle age lines, one small highlight per open eye, closed mouth and calm neutral expression. Practical plain faded ochre short jeogori jacket over simple cream ankle-length chima skirt, clean ivory collar, a small plain rust tie, no patterned borders, no embroidery. Dark hair in a low compact bun with a tiny undecorated wooden hairpin. No ceremonial hat, no ritual robe, no gold trim, no jewelry, no makeup or blush. Empty relaxed hands close to hips, front-facing natural balanced stance, full body and both simple dark cloth shoes visible. Natural warm personality without exaggerated features.
TECHNICAL: a single standalone person on requested512x1280 portrait2:5 canvas. FEMALE grade: hair top y185, chin y341, eyes y263, shoe sole baseline y1248; hair-top-to-foot1063px and6.8-head body proportion. Do not stretch legs. Head is156px tall at final512width; give the body believable compact adult proportions. Width withinx40..472, centeredx256. Leave large empty upper margin14.5% of canvas, bottom32px. All body parts entirely visible with no clipping, including feet. If generated at another resolution retain those ratios. ONE FLAT UNIFORM OPAQUE pure chroma GREEN #00FF00 RGB(0,255,0) background for later software matte extraction, including between arms and body. No green clothes. No ground shadow, gradient, scenery, transparency, checkerboard, text, numbers, grid, labels, borders, logos or watermark. No held objects or props.
```

### mudang_f

- `transparent_background`: `true`

```text
Create ONE fully rendered FEMALE Korean shaman full-body game sprite, age32. REFERENCE1 IS THE AUTHORITATIVE PIXEL LAYOUT TEMPLATE: preserve its full canvas aspect2:5, exact head/crown location and head size, eye level, shoulders, hand and shoe positions, top and bottom empty margins. Do NOT shrink the head or lengthen the body relative to that template. REFERENCE2 is drawing style and matching ceremonial costume reference ONLY, not a pose/body-proportion reference. This is a NEW rendering, not an enlargement of ref2. Final artwork is a human illustration not a grey silhouette. Remove ALL text, labels, guide dashes, grey fill and schematic hard shapes from ref1. Render only ONE person with REAL TRANSPARENT RGBA background. Polished 2D anime-adjacent semi-realistic Joseon-fantasy game illustration. Adult believable anatomy, thin clean charcoal outlines (outer2-4px, inner1-2px at512px width), soft controlled cel-painted shading, upper-left light, lower-right shadows. Muted ceremonial burgundy, deep indigo, ivory, muted brass-gold with restrained five-color cuff accents. No watercolor texture, no photography, no3D, no chibi. Wear a dignified Korean shaman MU-BOK, predominantly BURGUNDY with deep indigo side bands, ivory crossover collar, thin muted-gold edge trim with no glyphs, burgundy broad sash at natural waist, modest red/yellow/blue/white color bands at cuffs. A recognizable traditional BLACK KOREAN GAT, oval broad brim and compact cylindrical crown at positions of reference1. Thin plain chin cords. No crown, no tall plume. No handheld objects. Neutral closed-mouth expression, open eyes, two tiny highlights in each eye, smooth skin, symmetrical subtly idealized face, tidy black hair. Both hands relaxed close to sides, both shoes visible. Natural adult female anatomy, modestly loose robe with no exaggerated bust or cleavage. Do not overlengthen the skirt or legs. EXACT final canvas512x1280. Female6.8-head body: anatomical crown y185, chin341, eyes263, soles1248; gat extends above crown with at least16px margin. Match reference1 relative head/body dimensions exactly even if native output resolution differs. Body proportion is more important than a tall-fashion silhouette. No independent body-part stretching. Outer silhouette centeredx256. No crop of gat brim, fingers, hems or shoes, no ground shadow. No scene, props, text, numbers, checkerboard, frame or watermark.
```

### mudang_m

- `transparent_background`: `true`

```text
Create ONE fully rendered MALE Korean shaman full-body game sprite, age32. REFERENCE1 IS THE AUTHORITATIVE PIXEL LAYOUT TEMPLATE: preserve its full canvas aspect2:5, exact head/crown location and head size, eye level, shoulders, hand and shoe positions, top and bottom empty margins. Do NOT shrink the head or lengthen the body relative to that template. REFERENCE2 is drawing style and matching ceremonial costume reference ONLY, not a pose/body-proportion reference. This is a NEW rendering, not an enlargement of ref2. Final artwork is a human illustration not a grey silhouette. Remove ALL text, labels, guide dashes, grey fill and schematic hard shapes from ref1. Render only ONE person with REAL TRANSPARENT RGBA background. Polished 2D anime-adjacent semi-realistic Joseon-fantasy game illustration. Adult believable anatomy, thin clean charcoal outlines (outer2-4px, inner1-2px at512px width), soft controlled cel-painted shading, upper-left light, lower-right shadows. Muted ceremonial burgundy, deep indigo, ivory, muted brass-gold with restrained five-color cuff accents. No watercolor texture, no photography, no3D, no chibi. Wear a dignified Korean shaman MU-BOK, predominantly DEEP INDIGO with burgundy inner side bands, ivory crossover collar, thin muted-gold edge trim with no glyphs, burgundy broad sash at natural waist, modest red/yellow/blue/white color bands at cuffs. A recognizable traditional BLACK KOREAN GAT, oval broad brim and compact cylindrical crown at positions of reference1. Thin plain chin cords. No crown, no tall plume. No handheld objects. Neutral closed-mouth expression, open eyes, two tiny highlights in each eye, smooth skin, symmetrical subtly idealized face, tidy black hair. Both hands relaxed close to sides, both shoes visible. CRITICAL MALE ANATOMY: unmistakably adult male, square male jaw, straight broad masculine shoulders, flat pectoral ribcage under a loose straight outer robe; NO rounded breast shapes, NO bust, NO cleavage, NO paired curved chest seams or breast-defining folds. Robe hangs straight from upper chest toward sash, collar crosses high and neatly. Do not copy the female torso from reference2. EXACT final canvas512x1280. Male7-head body: anatomical crown y94, chin259, eyes176, soles1248; gat extends above crown to y32. Match reference1 relative head/body dimensions exactly even if native output resolution differs. Body proportion is more important than a tall-fashion silhouette. No independent body-part stretching. Outer silhouette centeredx256. No crop of gat brim, fingers, hems or shoes, no ground shadow. No scene, props, text, numbers, checkerboard, frame or watermark.
```

## 반복 시험과 원본 보관

첫 여성 원본과 repeat는 프롬프트·참고 이미지 입력 없음·transparent_background=false가 같다. 요청은 `out/assets/A-08-04/independent/requests/*-request.json`, 시간은 `*-timing.json`이다. 두 원본·최종 파일 해시와 화풍 비교·생성 시간은 [A-08-04 증빙](../../docs/evidence/A-08-04.md)에 있다. 원본·참고 이미지·측정 스크립트와 미리보기는 저장소 밖 전달 폴더에도 복사한다.

## A-09-05 실제 에셋 (첫 플레이 sprite 8·마스코트 2)

B(김영주)가 생성해 팀 공유 폴더 `2.asset 개선안 및 게임 UI 구성(김영주)/`에 올린 원본(바탕 투명 RGBA 793×1983, 마스코트 1254×1254)을 E가 규격에 맞췄다(A-09-05, 11/9 담당 D → E). 원본은 저장소에 넣지 않는다.

- 도구·모델·생성 시각은 원본 PNG의 콘텐츠 자격 증명(C2PA `caBX` 청크)에 적힌 값이다(`ChatGPT`, `gpt-image`, `c2pa.created`). 서명 검증은 하지 않았다. 정규화한 파일은 다시 인코딩해서 이 청크가 없다.
- 시드는 ChatGPT가 제공하지 않는다. 프롬프트 전문·금지 지시는 받지 않고 "확인 불가"로 적는다(11/9 사용자 결정, 에셋2 예외: 다른 사람이 만든 원본이라 프롬프트를 확인할 수 없으면 파일 안 생성 기록으로 도구·모델·생성 시각을 확인해 적는다). 이 기록으로 에셋2를 충족한 것으로 본다.
- 무당 여는 기본 `mudang_f.png`를 썼다. 변형(`-v2`·`-v3`·`-legacy-v4`·`-legacy-v5`)과 `branding/`은 쓰지 않았다.
- 처리: 인물별 머리카락 윗선(`tools/assets/normalize.py`의 `HAIR_TOP`, 원본 좌표)과 보이는 발끝으로 단일 배율을 정해 키 등급 머리 꼭대기·발끝 y=1248·가로 가운데 256에 둔다(면적 평균 축소, 가로·세로 같은 배율). 마스코트는 단일 배율로 512×512 가운데, 발끝 기준선 y=480, 위 여백 32 이상. 초상은 `make_placeholders`로 파생. 인물별 배율·결과 위치는 [A-09-05 증빙](../../docs/evidence/A-09-05.md).

| 항목 | 결과 파일 | 원본 파일(공유 폴더) | 도구 | 모델·버전 | 시드 | 생성 시각 UTC | 프롬프트 전문 | 부정 프롬프트·금지 지시 | 처리 |
|---|---|---|---|---|---|---|---|---|---|
| sprite:mudang_f | `content/images/sprite/mudang_f.png` | `characters/mudang_f.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:03:17Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| sprite:mudang_m | `content/images/sprite/mudang_m.png` | `characters/mudang_m.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:04:57Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| sprite:hunjang | `content/images/sprite/hunjang.png` | `characters/hunjang.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:05:01Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| sprite:meosum | `content/images/sprite/meosum.png` | `characters/meosum.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:05:40Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| sprite:yakcho | `content/images/sprite/yakcho.png` | `characters/yakcho.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:06:07Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| sprite:jumo | `content/images/sprite/jumo.png` | `characters/jumo.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:06:29Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| sprite:gwabu | `content/images/sprite/gwabu.png` | `characters/gwabu.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:06:39Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| sprite:blacksmith | `content/images/sprite/blacksmith.png` | `characters/blacksmith.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:07:39Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| mascot:kkachi | `content/images/mascot/kkachi.png` | `mascots/kkachi-v2.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:38:47Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |
| mascot:jokjebi | `content/images/mascot/jokjebi.png` | `mascots/jokjebi-v2.png` | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T14:40:05Z | 확인 불가 | 확인 불가 | `python -m tools.assets.normalize` |

```powershell
python -m tools.assets.normalize <원본 폴더 사본>
python -m tools.assets.make_placeholders
```

## 11/9 사용자 확정 v5 여성 무당 교체

B-ui-apply에서 사용자가 `mudang_f-legacy-v5.png`를 최종 v5로 지정했다. 위 A-09-05의 최초 입력 기록은 이력으로 보존하며 현재 여성 무당 전신·초상은 아래 입력을 따른다. 다른 17개 등록 그림과 manifest ID·크기는 유지한다. 재생성·화풍 리터칭은 하지 않았다.

- 입력(정식 PR에는 원화 미포함, 로컬 보존 브랜치 backup/B-ui-apply-full-20261109, 커밋 e22034e): `web/static/design-preview/assets/proposals/characters/mudang_f-legacy-v5.png`(793×1983 RGBA). SHA-256 `2f23c1c15e25d3a033b9e300fe2ede44456ca098fa544a5bb55b992fc867dee4`.
- 출처: 기존 B UI 원화, ChatGPT 이미지 생성. 모델 세부 버전·생성 시각·프롬프트 전문·금지 지시는 이 교체 작업에서 검증하지 않아 확인 불가로 기록한다. 이미지 생성은 이번 작업에서 실행하지 않았다.
- 처리: `tools.assets.cutout.fit_sprite(width, rows, 'mudang_f', 203)`. v5 눈 중심 y=338·턱 끝 y=448, 갓에 가린 머리 윗선은 기존 추정 방식 338−1.23×(448−338)≈203. 최초 원본용 HAIR_TOP=287은 사용하지 않는다. 단일 배율로 키 등급·발끝 1248·가운데 256을 맞춘다.
- 초상: `portrait_art.crop_portrait(512, sprite_rows, vn.portrait_top('mudang_f'))`의 RGB를 `png.encode_rgb(..., optimize=True)`로 인코딩한다. 전신은 RGBA. 초상을 따로 그리지 않는다.
- 출력: sprite 820482 bytes, SHA-256 `9181e7f15244e141bea0408c30596a64bb70764b0b408518b48c885bc162fe8d`; portrait 164120 bytes, SHA-256 `79f85c206a20a922aaae8d07c4be365f80eadc538d1c07dc5f237ce38008599b`.
- 여성 표정 3종은 아직 임시이므로 승인된 실제 표정으로 계산하지 않는다. 실제 표정 제작 시 이 v5 기본 sprite와 edited_from_sha256을 기준으로 얼굴 영역만 편집해야 한다.

## A-10-05 실제 에셋 (장면 배경 회의·밤)

B(김영주)가 11/7 화면 설계 시안(팀 공유 폴더 `2.asset 개선안 및 게임 UI 구성(김영주)/창귀_화면설계_디자인시안.html`)에 넣어 둔 배경 그림을 E가 규격에 맞췄다(A-10-05, 11/9 담당 D → E). 시안 HTML 안의 이미지 키가 `background:meeting`·`background:night`인 그림이고(1672×941 불투명 RGB), 시안에서 꺼낸 원본 PNG의 MD5 앞 8자리는 meeting `9a9cda47`, night `9d1c47ff`다. 원본과 시안은 저장소에 넣지 않는다. 같은 시안의 `background:dusk-proposal`은 규격 2장 범위 밖이라 쓰지 않았다.

- 도구·모델·생성 시각은 원본 PNG의 콘텐츠 자격 증명(C2PA `caBX` 청크)에 적힌 값이다(`ChatGPT`, `gpt-image`, `c2pa.created`). 서명 검증은 하지 않았다. 정규화한 파일은 다시 인코딩해서 이 청크가 없다.
- 시드는 ChatGPT가 제공하지 않는다. 프롬프트 전문·금지 지시는 확인할 수 없다. B가 화면 설계 시안용으로 생성한 그림이고 프롬프트 기록이 남아 있지 않으며, 11/9 사용자 결정으로 B에게 따로 받지 않는다. 도구·모델·생성 시각은 위 C2PA 기록으로 확인했다.
- 처리: 가운데 기준으로 정확한 16:9(1664×936, 왼쪽 4·위 2부터)로 자른 뒤 면적 평균으로 1280×720까지 줄였다(가로·세로 배율 0.76923). 수치는 [A-10-05 증빙](../../docs/evidence/A-10-05.md).

| 항목 | 결과 파일 | 원본 파일(공유 폴더) | 도구 | 모델·버전 | 시드 | 생성 시각 UTC | 프롬프트 전문 | 부정 프롬프트·금지 지시 | 처리 |
|---|---|---|---|---|---|---|---|---|---|
| background:meeting | `content/images/background/meeting.png` | `창귀_화면설계_디자인시안.html` 안 `background:meeting`(MD5 9a9cda47) | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T15:07:05Z | 확인 불가(B가 화면 설계 시안용으로 생성, 도구·모델·시각은 C2PA로 확인) | 확인 불가(B가 화면 설계 시안용으로 생성, 도구·모델·시각은 C2PA로 확인) | `python -m tools.assets.normalize --background` |
| background:night | `content/images/background/night.png` | `창귀_화면설계_디자인시안.html` 안 `background:night`(MD5 9d1c47ff) | ChatGPT 이미지 생성 | gpt-image(C2PA 기록) | 도구 미제공 | 2026-11-06T15:06:40Z | 확인 불가(B가 화면 설계 시안용으로 생성, 도구·모델·시각은 C2PA로 확인) | 확인 불가(B가 화면 설계 시안용으로 생성, 도구·모델·시각은 C2PA로 확인) | `python -m tools.assets.normalize --background` |

```powershell
python -m tools.assets.normalize --background meeting=<시안에서 꺼낸 meeting 원본> --background night=<시안에서 꺼낸 night 원본>
```

## A-10-06 확정 표정 생성 기록 복구 (2026-11-10)

선택된 결과 16장과 생성 단계 19개의 원문 프롬프트·입력 관계·도구·모델 표기·생성 시각을 과거 대화의 도구 호출, 반환 PNG의 C2PA, 보존된 조립 스크립트에서 복구했다. 최종 결과 SHA와 원본 SHA는 [선택된 생성·조립 기록](../../docs/evidence/A-10-06-sources.json)에 있다. 이 JSON의 selected_generation_stages와 assembly_recipes가 최종 사용 단계이며, retained_related_instructions는 참고 이력으로 구분한다. C2PA 서명 유효성은 검증하지 않았고 시드는 도구 미제공, 부정 프롬프트는 별도 필드 미지원이라 원문 안 금지 지시에 포함된다. 버려진 모든 시안의 전체 이력을 제출 완료 조건에 추가하지 않는다.

| 결과 파일 | 도구 | 모델·버전 | 프롬프트 전문 | 부정 프롬프트 | 시드 | 생성 시각 UTC | 편집 입력·가공 |
|---|---|---|---|---|---|---|---|
| `content/images/sprite/mudang_f__joy.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T03:44:52.565751845Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/mudang_f.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/mudang_m__joy.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T03:52:01.057616892Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/mudang_m.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/hunjang__joy.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T04:17:50.951990556Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/hunjang.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/jumo__joy.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T04:18:27.537415280Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/jumo.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/blacksmith__joy.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T04:19:19.794694011Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/blacksmith.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/meosum__joy.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T04:20:01.476433140Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/meosum.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/yakcho__joy.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T04:20:42.881930395Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/yakcho.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/gwabu__joy.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T04:21:23.295461850Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/gwabu.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/mudang_f__flustered.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T04:43:00.586439170Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/mudang_f.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/mudang_m__flustered.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T05:24:43.454748019Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/mudang_m.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/hunjang__flustered.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T05:51:02.294904625Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/hunjang.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/jumo__flustered.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T05:50:03.235061486Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/jumo.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/blacksmith__flustered.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T06:01:37.156631284Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/blacksmith.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/meosum__flustered.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T06:02:01.473334466Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/meosum.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/yakcho__flustered.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T05:52:03.649388919Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/yakcho.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/gwabu__flustered.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-10-06-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T05:50:34.341125419Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/gwabu.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |

## A-11-04 확정 표정 생성 기록 복구 (2026-11-10)

선택된 결과 8장과 생성 단계 20개의 원문 프롬프트·입력 관계·도구·모델 표기·생성 시각을 과거 대화의 도구 호출, 반환 PNG의 C2PA, 보존된 조립 스크립트에서 복구했다. 최종 결과 SHA와 원본 SHA는 [선택된 생성·조립 기록](../../docs/evidence/A-11-04-sources.json)에 있다. 이 JSON의 selected_generation_stages와 assembly_recipes가 최종 사용 단계이며, retained_related_instructions는 참고 이력으로 구분한다. C2PA 서명 유효성은 검증하지 않았고 시드는 도구 미제공, 부정 프롬프트는 별도 필드 미지원이라 원문 안 금지 지시에 포함된다. 버려진 모든 시안의 전체 이력을 제출 완료 조건에 추가하지 않는다.

| 결과 파일 | 도구 | 모델·버전 | 프롬프트 전문 | 부정 프롬프트 | 시드 | 생성 시각 UTC | 편집 입력·가공 |
|---|---|---|---|---|---|---|---|
| `content/images/sprite/mudang_f__sad.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-11-04-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T06:27:52.049679749Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/mudang_f.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/mudang_m__sad.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-11-04-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T06:39:47.143711118Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/mudang_m.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/hunjang__sad.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-11-04-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T07:04:41.279426198Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/hunjang.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/jumo__sad.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-11-04-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T07:30:47.289142568Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/jumo.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/blacksmith__sad.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-11-04-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T07:27:07.377623958Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/blacksmith.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/meosum__sad.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-11-04-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T07:27:45.054701700Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/meosum.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/yakcho__sad.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-11-04-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T07:28:24.701994551Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/yakcho.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |
| `content/images/sprite/gwabu__sad.png` | Codex 내장 image_gen / C2PA softwareAgent ChatGPT | gpt-image (C2PA 기록) | A-11-04-sources.json 해당 id·emotion selected_generation_stages[].prompt 전문 | 별도 필드 미지원; 원문 내 금지 지시 | 도구 미제공 | 2026-11-10T07:28:57.018531525Z (최종 사용 생성 단계; 합성 시각 아님) | `content/images/sprite/gwabu.png`; 입력 커밋 9d4b8dbe5f82b148bebe73ee0117ef0102ac2f63, 기본·결과 SHA 및 합성 스크립트 전문은 같은 JSON |

## 마을 지도 실제 그림 (11/11 사용자 결정)

11/5 PM 결정 "지도는 시연까지 임시"를 11/11 사용자 결정으로 바꿔 외부 기증 그림의 장소 이름 글자 없는 판을 쓴다. 생성자는 외부 기증(이름 미기재)이고 받은 날짜는 2026-11-11이다. 기증자가 도구만 알려 주었고 프롬프트·모델·생성 시각은 받지 못했다. 장소 이름은 화면이 덧씌운다(`docs/assets/spec.md` 2절 지도).

| 결과 파일 | 도구 | 모델·버전 | 프롬프트 전문 | 부정 프롬프트 | 시드 | 생성 시각 UTC | 가공 |
|---|---|---|---|---|---|---|---|
| `content/images/map/village.png` | Gemini(외부 기증) | 미제공 | 미제공 | 미제공 | 미제공 | 미제공(받은 날짜 2026-11-11) | 원본 1941×810을 1728×720으로 줄이고(LANCZOS) 256색 양자화(손실, 지도만 허용) |

- 원본: 1941×810 RGB PNG, 3,583,826 bytes, SHA-256 `f46b3fc4d1b621a46b4b997904f944a0aa3609c1e88fcc4c2517b470ba78cd99`. 원본은 저장소에 넣지 않는다.
- 결과: 1728×720 팔레트 PNG, 829,414 bytes, SHA-256 `cdb385bee07a0a666efc33828998cb60b81d4343b001f48365e2fbb9b28cddb4`.
- 재현 명령(Pillow 12.3.0, 저장소 의존성 밖 도구):

```powershell
python -c "from PIL import Image; Image.open('<원본 PNG>').convert('RGB').resize((1728, 720), Image.Resampling.LANCZOS).quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG).save('content/images/map/village.png', 'PNG', optimize=True)"
```
