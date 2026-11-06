Codex 내장 image_gen · 비상업 교육 프로젝트용 생성물

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
