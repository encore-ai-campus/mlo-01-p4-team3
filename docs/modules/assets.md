# assets — 이미지 에셋 규격·임시 세트

## 무엇을

게임 화면(B)과 광고 서버(D)가 쓰는 이미지 파일의 규격, 파일 목록(`manifest.json`), 임시 세트를 관리한다.
- 입력: 규격표 `docs/assets/spec.md`, 콘텐츠 ID(`fixtures/content_min/bundle.json` 인물 + 무당 2종, 계약 `Place`, 규격표의 UI 아이콘 10개, `fixtures/ads/campaigns.json` 소재 × 지면 규격)
- 출력: `content/images/<kind>/…` PNG와 `content/images/manifest.json`(kind, id, size(광고만), path, url, placeholder, derived_from(초상만), edited_from·edited_from_sha256(표정 변형 sprite만))
- 유형 9개: portrait·sprite·background·mascot·branding·place·ui·map·ad. 비주얼 노벨 회의 화면용 sprite(전신 512×1280, 바탕 투명)를 원본으로 두고, 초상은 sprite에서 인물별 크롭 위 y(`vn.portrait_top`)부터 512×512를 먹남 위에 알파 합성해 만든다(크로스체크 17·18, 11/6 키 등급). 표정 변형은 `<인물 id>__<표정 id>` sprite·초상이고 기본 sprite에서 얼굴만 편집한다(크로스체크 19). 마스코트는 까치·족제비 512×512 RGBA다.
- 계층(`tools/assets/`)
  - 도메인: `spec.py`(규격표 해석, 기대 항목, 대조, 초상-sprite 쌍 검사)·`art.py`(유형별 임시 그림 배분)·`portrait_art.py`(임시 sprite: 회색 실루엣·기준선·이름표, 초상 합성 크롭)·`background_art.py`(임시 장면 배경: 그라데이션·이름표)·`mascot_art.py`(임시 마스코트: 동물 실루엣·이름표)·`glyphs.py`(이름표 글자 찍기, 비트맵 `glyph_data.py`)·`vn.py`(sprite 기준선·배경·표정 얼굴 영역·마스코트 검사, 평소/전신 컷·마스코트 배치 수치, 부드러운 축소, 검수용 합성)
  - 인프라: `sources.py`(파일·로그·결과 JSON, 인물 표시 이름)·`png.py`(PNG RGB/RGBA 쓰기·읽기)·`build_glyphs.py`(Windows 글꼴 → `glyph_data.py`, GDI·ctypes, 개발 도구 CLI)
  - 응용+표현: `make_placeholders.py`(생성·대조 흐름, CLI)·`preview_vn.py`(검수용 합성 PNG CLI)·`normalize.py`(바탕이 투명한 실제 원본 → 키 등급 sprite·마스코트 배치, 인물별 머리카락 윗선 표 `HAIR_TOP`, A-09-05; 장면 배경 원본 → 16:9 자르기·1280×720 축소 `--background`, A-10-05)
  - 의존: `make_placeholders`·`preview_vn` → `spec`·`art`·`vn` → `portrait_art`·`background_art`·`mascot_art` → `glyphs`, `sources`·`png` → `spec`. 도메인은 인프라를 import하지 않는다.
- 고칠 곳: 규격·기대 수 → `docs/assets/spec.md` + `spec.py`, 임시 실루엣 → `portrait_art.py`, 임시 배경 → `background_art.py`, 임시 마스코트 → `mascot_art.py`, 표정 목록·대상 → `spec.md` 8·9절, 이름표 글자 추가 → `python -m tools.assets.build_glyphs`, sprite·배경 검사 기준·화면 배치 수치 → `vn.py`, PNG 형식 → `png.py`.
- 그림 기준은 `docs/assets/style_guide.md`(반실사 일러스트, 사람이 체크리스트로 판정). 임시 에셋은 회색 실루엣과 이름 표기, 크기·기준선·배치 확인용이며 최종 그림이 아니다.

## 왜

- 이미지가 늦어도 B·D 개발이 막히지 않게, 실제와 **같은 경로·이름·크기·형식**의 임시 파일을 먼저 준다. 교체는 같은 `path`에 덮어쓰고 `placeholder`만 `false`로 바꾸므로 B·D 코드는 고치지 않는다(기획서 결정 기록 11/4, `docs/tasks/README.md` 3절 교체표, E-11-10 5번이 검사).
- B는 manifest 조회(`asset_url`)로만, D는 `slot_image_path(image_path, slot)`로만 이미지를 가리킨다(B-05-03 4번, D-05-03 7번). 광고 파일명은 계약 함수를 그대로 불러 만든다(`docs/contracts/ads.md` 2절). 이름 규칙을 두 곳에 따로 쓰지 않기 위해서다.
- 규격표(`spec.md`)를 사람과 스크립트가 함께 읽는 원본으로 둬, 값이 문서와 코드에 따로 적혀 어긋나는 일을 막는다.
- 표준 라이브러리만 쓴다(zlib PNG). `precheck`가 설치 없이 돌고, 같은 입력이면 같은 바이트가 나와 diff가 생기지 않는다. 이름표 글자도 시스템 글꼴 대신 커밋한 비트맵을 써서 노트북마다 결과가 같다(`build_glyphs`만 Windows 글꼴을 쓰고 `precheck`는 돌리지 않는다).
- 임시 그림을 회색 실루엣 + 이름으로 둔 이유(11/5 PM 결정): 코드로 그린 인물 그림은 최종 반실사 그림과 너무 달라 팀원에게 잘못된 인상을 준다. 실루엣은 크기·기준선·배치만 보여 주고 그림 기대를 만들지 않는다.
- 초상을 sprite에서 파생하는 이유: 한 인물을 한 번만 만들면 초상과 서 있는 그림의 얼굴·옷이 어긋나지 않고, 실제 에셋 작업(D, A 흐름 `[에셋]` 태스크)도 인물당 1장이다. 모든 인물을 같은 축척으로 그리고 초상 크롭은 눈이 초상 안 등급별 위치(`HeightGrade.portrait_eye`)에 오게 인물별로 잡아, 키가 달라도 초상의 얼굴 위치가 맞는다(11/6 PM 결정).
- 외형은 공개 인물 ID로만 정한다. 숨은 역할·성격·일과에 따라 그림·크기·폴백이 달라지면 정체 단서가 되기 때문이다(스타일 가이드 6절 4번). 밤 화면에는 sprite를 두지 않는다.

## 사용법

```
python -m tools.assets.make_placeholders            # 임시 세트 생성 + 유형별 대조(어긋나면 종료 코드 1)
python -m tools.assets.make_placeholders --check    # 쓰지 않고 대조만(실제 파일로 교체한 뒤 확인용)
python -m tools.assets.make_placeholders --force    # 표시 없는 다른 내용 파일도 임시 그림으로 덮어씀(주의)
python -m tools.assets.make_placeholders --result   # 대조 수치를 docs/evidence/perf/E-05-05/placeholders.json에
python -m tools.assets.make_placeholders --rederive-portraits   # 임시 초상만 sprite 파생으로 다시 씀(흉상 생성기에서 1회 이전)
python -m tools.assets.preview_vn --bg meeting --id hunjang --out out/preview_vn/meeting_hunjang.png
python -m tools.assets.preview_vn --bg meeting --id hunjang --mode full --out out/preview_vn/full_hunjang.png
python -m tools.assets.preview_vn --bg meeting --id jumo --expr sad --mascot kkachi --out out/preview_vn/morning.png
python -m unittest tests.assets.test_make_placeholders tests.assets.test_portrait_art tests.assets.test_vn tests.assets.test_expression_mascot
```

- 바탕이 투명한 생성 원본(`characters/<id>.png`, `mascots/<파일>`)을 맞출 때: 인물의 머리카락 윗선(원본 좌표, 장식 제외)을 `normalize.HAIR_TOP`에 근거와 함께 적고 `python -m tools.assets.normalize <원본 폴더>` → `make_placeholders`. 단색 바탕 원본은 `cutout`.
- 실제 sprite를 넣을 때: `content/images/sprite/<id>.png`(512×1280 8비트 RGBA, 반투명 허용, 팔레트 PNG 안 됨)를 덮어쓰고 sprite 항목 `placeholder`를 `false`로 바꾼 뒤 `make_placeholders`를 실행한다. 초상이 그 sprite에서 다시 합성·크롭되고(무손실 필터 선택으로 저장) 초상 표시도 `false`가 된다. 초상 파일은 따로 넣지 않는다.
- 실제 표정 sprite를 넣을 때: 기본 sprite를 편집 입력으로 얼굴 영역만 바꾼 결과를 기본에 붙여 넣어 `sprite/<id>__<표정 id>.png`에 덮어쓰고 `placeholder: false`로 바꾼 뒤 `make_placeholders`를 실행한다. 처음 실제로 표시될 때 기본 sprite 파일의 SHA-256이 `edited_from_sha256`에 기록되고, 이후 기본이 바뀌면 위반으로 보고한다. 표정을 새 기본에서 다시 만들었으면 그 항목의 `edited_from_sha256`을 지우고 다시 실행한다.
- 검수용 합성(`preview_vn`)은 저장소의 파일을 그대로 겹친다. 출력은 저장소 밖이나 `out/` 아래만 허용하고, `--bg night`에 `--id`를 주면 오류다.

- 실제 파일로 바꿀 때: 같은 `path`에 PNG를 덮어쓰고 **바로** manifest의 그 항목 `placeholder`를 `false`로 바꾼 뒤 커밋한다. 그러면 다시 실행해도 그 파일은 덮어쓰지 않는다.
- 표시를 바꾸지 않았더라도, 디스크 파일이 지금 만들 임시 그림과 다르면 덮어쓰지 않고 `protected_files`로 보고한 뒤 종료 코드 1로 끝난다. 실제 파일이면 표시를 `false`로 바꾸고, 정말 임시 그림으로 되돌릴 때만 `--force`를 쓴다.
- 빠진 콘텐츠 ID의 실제 파일 항목(`placeholder: false`)은 manifest에서 지우지 않고 `extra_in_manifest`로 보고한다(ID가 다시 생겨도 보호된다).
- 콘텐츠 ID가 늘면(인물 추가, 소재 추가) 다시 실행하면 된다. 없어진 ID의 파일은 지우지 않고 `extra_files`로 보고한다.

`python -B -m tools.assets.branding <시안 원본 PNG>`는 RGBA 원본을 비율 유지해 512×512 투명 캔버스로 정규화하고 공식 로고 ID·manifest를 등록한다. 다른 내용의 기존 로고는 덮어쓰지 않는다. `check_assets`·`make_placeholders`는 같은 branding 규격·RGBA 검사를 사용하며 전체 기대 수량은 103이다. 로고 출처는 B-10-08의 `SOURCES.md` 기록을 따른다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `spec.md` 1절 `용량 상한(바이트)` | 512000 | 파일 용량 공통 상한(P-15 잠정) | 넘는 임시 파일이 생기면 생성 실패 |
| `spec.md` 1절 `용량 상한(바이트) <kind>` | sprite 8388608, portrait 3145728, background 10485760, mascot 3145728 | kind별 상한(11/5 PM 결정, 반실사 실제 그림용) | 행이 없는 kind는 공통 상한 |
| `spec.md` 2절 크기 | portrait 512×512, sprite 512×1280, background·map 1280×720, place·ui 128×128 | 유형별 픽셀 크기 | 바꾸면 B 화면 레이아웃과 실제 에셋 전부 영향. 바꾸지 않는다 |
| `vn.py` sprite 기준선 | 가운데 256±16px(알파 16 이상), 머리 위 여백 ≥ 16px, 발끝 y=1248(아래 여백 16~48px). 키 등급 4개(`HEIGHT_GRADES`: 머리 꼭대기 y/등신/초상 안 눈 y = 큰 키 48/7.2/200, 남성 보통 94/7/200, 여성 보통 185/6.8/200, 아이 339/6/230, 사람 검수)와 인물 → 등급 표(`CHARACTER_GRADE`) | sprite 검사 기준(`spec.md` 6절과 같은 값, 테스트가 대조) | 등급 값·인물 배정은 PM 결정(11/6). 새 인물은 등급을 넣어야 그려진다 |
| `vn.py` 표정·마스코트 | 얼굴 영역 `face_box(인물 id)`(등급에서 계산: x 256±0.6h, y 눈−0.6h~눈+0.69h. 밖은 기본과 정확히 같음, 완전 투명 RGB 무시, 얼굴은 달라야 함), 마스코트 512×512 RGBA·모서리 투명·투명 20% 이상·가로 가운데 256±16·아래 여백 16~48px(sprite와 같은 판정 함수) | 표정 변형·마스코트 검사(`spec.md` 6절) | 실제 편집 시험(A-10-06) 결과로 PM이 조정 |
| `vn.py` 배치 | 무대 1280×720, 평소 0~640행 0.8배 410×512 위 28, 전신 컷 0.5배 256×640 위 40(대사창 숨김), 마스코트 0.5배 256×256 x 64·y 260, 대사창 180px, 밤 sprite 0 | 검수용 합성과 B-10-07의 기준 수치(`spec.md` 7절) | B 화면 배치와 함께 바꾼다 |
| `spec.md` 3절 | 아이콘 10개 | UI 아이콘 ID 목록 | 추가하면 기대 수가 늘어남 |
| `spec.md` 8·9절 | 표정 3개(joy·flustered·sad), 대상 8명(무당 2 + 첫 플레이 인물 6, `yakcho`·`gwabu`는 E-05-03이 정함) | 표정 변형 목록과 대상 | 대상 1명마다 sprite·초상 3장씩 늘어남. 새 표정 이름은 `build_glyphs` 다시 실행 |
| 광고 크기 | 계약 `SLOT_SIZES` | 728×90, 300×250(P-19 잠정) | 계약 변경 → 픽스처 재생성 → 다시 실행 |
| `--repo` | 이 저장소 | 출력 루트 | 테스트에서 임시 폴더로 바꿔 쓴다 |
| 로그 | `out/logs/assets.jsonl` + 표준 출력 | 구조화 로그(README 5절) | — |
| `glyph_data.py` | 맑은 고딕(Windows GDI) 32px, 1비트, 높이 45px | 이름표 글자 비트맵(인물 이름·고정 문구·영문 소문자·숫자) | 새 이름을 넣으면 `build_glyphs`로 다시 만들고 커밋. 안 하면 그 인물은 ID로 찍힘 |

## 한계

- 임시 sprite·초상은 회색 실루엣과 이름표라, 화면에서는 머리 장식과 이름으로만 인물을 구분한다. 얼굴·옷·색은 최종 그림 판단에 쓰지 않는다.
- 장소·UI·지도·광고 임시 그림은 kind마다 같은 도형이다. id 해시로 보조색만 달라서, 화면에서 구분하는 용도로는 쓰지 못한다.
- 형식(PNG 구조·CRC·데이터 길이)·크기·용량·누락·남는 파일·manifest path/url 불일치, sprite·배경 규칙(`vn.py`), 초상-sprite 쌍만 본다. 생성 기록(에셋2)과 정식 검사는 `check_assets.py`(E-08-07)가 맡는다.
- 임시 sprite는 키 등급별로 같은 축척의 실루엣이고 머리 장식만 사람마다 다르다.
- 머리 꼭대기·등신(키 등급)은 그림에서 자동으로 찾을 수 없어 사람 검수 항목이다(자동은 가운데·머리 위 여백·발끝, 표정의 얼굴 영역만).
- PNG 읽기(`png.decode`)는 8비트 RGB/RGBA·인터레이스 없음만 지원한다. 다른 형식으로 저장한 실제 sprite는 오류로 알려 준다.
- 지도 장소 영역 좌표는 아직 없다(실제 지도 E-11-07에서 manifest에 추가).
- 결과 JSON은 `tools/evidence_result.write_result`(E-05-02)로 쓴다. 그 모듈이 없는 환경에서만 `sources.write_result`가 같은 형식으로 직접 쓴다.
- 화풍은 애니풍에 가까운 반실사 일러스트(11/5 PM 결정)다. 임시 실루엣은 이 화풍을 따르지 않는다(실제 파일로 덮어쓰면 사라진다). 실제 에셋 생성 도구(P-10)와 투명 바탕 처리 스크립트는 A-08-04에서 정한다.
- 반실사 실제 sprite는 임시 그림보다 용량이 크다. 상한을 sprite 8MB·초상 3MB·장면 배경 10MB로 크게 잡았고(`spec.md` 1절), 실제 크기는 A-08-04가 측정해 기록한다. 넘을 때 허용하는 줄이기 방법은 무손실 재압축뿐이다(`spec.md` 6절).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| B-10-08 | 11/9 | 확정 branding 유형·규격표·기대 항목·임시 생성·RGBA 검사, 승인 시안 호랑이 로고 정규화·등록·출처. 인물·배경 파일은 보존 |
| A-08-04 | 11/8 | (11/6 선행 작업) `cutout.py`·`test_cutout.py`: 실제 생성 원본의 단색 바탕 편차·가는 머리카락·색 번짐을 처리하고 straight alpha로 복원, 키 등급별 단일 배율 배치. 생성·처리 기록 `content/images/SOURCES.md`, 시험 증빙 `docs/evidence/A-08-04.md`. 시험 파일은 out/ 보관, 실제 manifest 교체 전 |
| A-09-05 | 11/9 | `normalize.py`·`test_normalize.py`: 바탕이 투명한 원본을 인물별 머리카락 윗선(`HAIR_TOP`)·발끝으로 단일 배율 배치(sprite), 512×512 가운데·아랫변 480(마스코트), 모두 통과해야 쓰고 manifest `placeholder: false`. 첫 플레이 sprite 8장·마스코트 2장 실제 파일, 초상 8장 파생, `SOURCES.md` A-09-05 절 |
| A-10-05 | 11/9 | `normalize.py`·`test_normalize.py`: 장면 배경 정규화(`background_crop` 가운데 정확한 16:9 자르기, `fit_background` 면적 평균 1280×720 불투명 RGB, `normalize_backgrounds` 모두 통과해야 쓰고 manifest `placeholder: false`, CLI `--background id=경로`). B 화면 설계 시안의 회의·밤 배경 2장 실제 파일, `SOURCES.md` A-10-05 절 |
| E-05-05 | 11/5 | 규격표(도트 격자 포함)·스타일 가이드(도트), 임시 세트 생성기(`make_placeholders`)와 37개 임시 세트·manifest, 임시 초상 13장은 코드로 그린 도트 그림(`portrait_art`). 도트 기준은 같은 날 반실사 개정으로 대체(아래 E-08-07 행) |
| E-08-07 | 11/8 | (11/5 사전 작업) 유형 sprite·background 추가(기대 52), 초상을 sprite 크롭으로 파생(`derived_from`, 쌍 상태 검사), RGBA PNG 쓰기·읽기, sprite·배경 검사(`vn.py`), 검수용 합성(`preview_vn`), 임시 sprite 13장·배경 2장. 같은 날 화풍을 반실사로 바꿔(크로스체크 18) sprite를 전신 512×1280으로, 검사를 px 기준선으로 바꾸고 도트 격자·팔레트·블록·알파 이진 검사를 뺐다. 초상은 알파 합성 크롭, 배치는 평소/전신 컷 두 모드. 이어서 임시 sprite·초상·배경을 회색 실루엣 + 이름표로 바꾸고(`glyphs`·`glyph_data`·`build_glyphs`) 코드 인물 그림·마을 그림을 지웠다 |
| E-08-07 | 11/8 | (11/5 사전 작업, 크로스체크 19) 표정 변형(`<id>__<표정 id>`, 규격표 8·9절, `edited_from`·`edited_from_sha256`, 얼굴 영역 밖 동일·얼굴 변화 검사)과 마스코트 유형(`mascot`, 까치·족제비, `mascot_art`) 추가, 기대 90. `preview_vn --expr --mascot` |
| E-05-05 | 11/6 | (11/6 PM 결정) sprite 키 등급: 눈높이·허리선 통일을 없애고 같은 축척에 인물별 머리 꼭대기·등신(`HEIGHT_GRADES`·`CHARACTER_GRADE`). 얼굴 영역(`face_box`)·초상 크롭 위 y(`portrait_top`, 등급별 초상 안 눈 위치)를 등급에서 계산, 임시 실루엣을 등급별로 다시 그림. 마스코트 가로 가운데·아래 여백 자동 검사(sprite와 같은 판정)와 임시 마스코트 위치 조정 |
| E-05-05 | 11/6 | 임시 그림 생성 가속(판정·생성 결과는 그대로, 바이트 동일): `portrait_art.render_sprite`·`mascot_art.render`는 그린 결과를 기억하고 복사본을 돌려줌(키에 키 등급·머리 장식·마스코트 위치 포함), `crop_portrait`는 알파 0·255를 바이트 마스크로 한 번에 합성, `make_placeholders`는 임시 PNG 바이트를 프로세스 안에서 기억 |
| E-05-05 | 11/6 | 임시 에셋 동일성을 해상도·채널·디코드 픽셀로(`png.same_image`): zlib 구현(표준 zlib·Windows 공식 Python zlib-ng)마다 압축 바이트가 달라도 생성기가 기존 임시 PNG를 보존(용량 상한 이내일 때). 용량 상한·실제 에셋 SHA-256은 파일 바이트 기준 그대로 |
| A-10-06 | 11/10 | 확정 기쁨·당황 16종 전신·파생 초상 등록. 기본 sprite 보존. 선택 결과 16장·생성 19단계 기록 복구. E 일괄 검수 별도 |
| A-11-04 | 11/10 | 확정 슬픔 8종 전신·파생 초상 등록. 기본 sprite 보존. 선택 결과 8장·생성 20단계 기록 복구. E 일괄 검수 별도 |
