# assets — 이미지 에셋 규격·임시 세트

## 무엇을

게임 화면(B)과 광고 서버(D)가 쓰는 이미지 파일의 규격, 파일 목록(`manifest.json`), 임시 세트를 관리한다.
- 입력: 규격표 `docs/assets/spec.md`, 콘텐츠 ID(`fixtures/content_min/bundle.json` 인물 + 무당 2종, 계약 `Place`, 규격표의 UI 아이콘 10개, `fixtures/ads/campaigns.json` 소재 × 지면 규격)
- 출력: `content/images/<kind>/…` PNG와 `content/images/manifest.json`(kind, id, size(광고만), path, url, placeholder, derived_from(초상만))
- 유형 7개: portrait·sprite·background·place·ui·map·ad. 비주얼 노벨 회의 화면용 sprite(전신 512×1280, 바탕 투명)를 원본으로 두고, 초상은 sprite 위 512×512를 먹남 위에 알파 합성해 만든다(크로스체크 17·18).
- 계층(`tools/assets/`)
  - 도메인: `spec.py`(규격표 해석, 기대 항목, 대조, 초상-sprite 쌍 검사)·`art.py`(유형별 임시 그림 배분)·`portrait_art.py`(임시 전신 sprite 그리기, 인물 데이터, 초상 합성 크롭)·`background_art.py`(임시 장면 배경)·`vn.py`(sprite 기준선·배경 검사, 평소/전신 컷 배치 수치, 부드러운 축소, 검수용 합성)
  - 인프라: `sources.py`(파일·로그·결과 JSON)·`png.py`(PNG RGB/RGBA 쓰기·읽기)
  - 응용+표현: `make_placeholders.py`(생성·대조 흐름, CLI)·`preview_vn.py`(검수용 합성 PNG CLI)
  - 의존: `make_placeholders`·`preview_vn` → `spec`·`art`·`vn` → `portrait_art`·`background_art`, `sources`·`png` → `spec`. 도메인은 인프라를 import하지 않는다.
- 고칠 곳: 규격·기대 수 → `docs/assets/spec.md` + `spec.py`, 임시 인물 그림 → `portrait_art.py`, 임시 배경 → `background_art.py`, sprite·배경 검사 기준·화면 배치 수치 → `vn.py`, PNG 형식 → `png.py`.
- 그림 기준은 `docs/assets/style_guide.md`(반실사 일러스트, 사람이 체크리스트로 판정). 임시 그림은 실루엣·배치 확인용이며 최종 화풍이 아니다.

## 왜

- 이미지가 늦어도 B·D 개발이 막히지 않게, 실제와 **같은 경로·이름·크기·형식**의 임시 파일을 먼저 준다. 교체는 같은 `path`에 덮어쓰고 `placeholder`만 `false`로 바꾸므로 B·D 코드는 고치지 않는다(기획서 결정 기록 11/4, `docs/tasks/README.md` 3절 교체표, E-11-10 5번이 검사).
- B는 manifest 조회(`asset_url`)로만, D는 `slot_image_path(image_path, slot)`로만 이미지를 가리킨다(B-05-03 4번, D-05-03 7번). 광고 파일명은 계약 함수를 그대로 불러 만든다(`docs/contracts/ads.md` 2절). 이름 규칙을 두 곳에 따로 쓰지 않기 위해서다.
- 규격표(`spec.md`)를 사람과 스크립트가 함께 읽는 원본으로 둬, 값이 문서와 코드에 따로 적혀 어긋나는 일을 막는다.
- 표준 라이브러리만 쓴다(zlib PNG). CI `core`가 설치 없이 돌고, 같은 입력이면 같은 바이트가 나와 diff가 생기지 않는다.
- 초상을 sprite에서 파생하는 이유: 한 인물을 한 번만 만들면 초상과 서 있는 그림의 얼굴·옷이 어긋나지 않고, 실제 에셋 작업(A)도 인물당 1장이다. 눈높이 기준선(y≈200)이 모든 인물에서 같아 초상을 나란히 놓아도 얼굴 크기가 튀지 않는다(크로스체크 18).
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
python -m unittest tests.assets.test_make_placeholders tests.assets.test_portrait_art tests.assets.test_vn
```

- 실제 sprite를 넣을 때: `content/images/sprite/<id>.png`(512×1280 8비트 RGBA, 반투명 허용, 팔레트 PNG 안 됨)를 덮어쓰고 sprite 항목 `placeholder`를 `false`로 바꾼 뒤 `make_placeholders`를 실행한다. 초상이 그 sprite에서 다시 합성·크롭되고(무손실 필터 선택으로 저장) 초상 표시도 `false`가 된다. 초상 파일은 따로 넣지 않는다.
- 검수용 합성(`preview_vn`)은 저장소의 파일을 그대로 겹친다. 출력은 저장소 밖이나 `out/` 아래만 허용하고, `--bg night`에 `--id`를 주면 오류다.

- 실제 파일로 바꿀 때: 같은 `path`에 PNG를 덮어쓰고 **바로** manifest의 그 항목 `placeholder`를 `false`로 바꾼 뒤 커밋한다. 그러면 다시 실행해도 그 파일은 덮어쓰지 않는다.
- 표시를 바꾸지 않았더라도, 디스크 파일이 지금 만들 임시 그림과 다르면 덮어쓰지 않고 `protected_files`로 보고한 뒤 종료 코드 1로 끝난다. 실제 파일이면 표시를 `false`로 바꾸고, 정말 임시 그림으로 되돌릴 때만 `--force`를 쓴다.
- 빠진 콘텐츠 ID의 실제 파일 항목(`placeholder: false`)은 manifest에서 지우지 않고 `extra_in_manifest`로 보고한다(ID가 다시 생겨도 보호된다).
- 콘텐츠 ID가 늘면(인물 추가, 소재 추가) 다시 실행하면 된다. 없어진 ID의 파일은 지우지 않고 `extra_files`로 보고한다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `spec.md` 1절 `용량 상한(바이트)` | 512000 | 파일 용량 상한(P-15 잠정) | 넘는 임시 파일이 생기면 생성 실패 |
| `spec.md` 2절 크기 | portrait 512×512, sprite 512×1280, background·map 1280×720, place·ui 128×128 | 유형별 픽셀 크기 | 바꾸면 B 화면 레이아웃과 실제 에셋 전부 영향. 바꾸지 않는다 |
| `vn.py` sprite 기준선 | 가운데 256±16px(알파 16 이상), 머리 위 여백 ≥ 16px, 발끝 y=1248(아래 여백 16~48px), 눈높이 y≈200·허리선 y=640(사람 검수) | sprite 검사 기준(`spec.md` 6절과 같은 값, 테스트가 대조) | 실제 sprite 생성(A-09-05) 결과를 보고 PM이 조정 |
| `vn.py` 배치 | 무대 1280×720, 평소 0~640행 0.8배 410×512 위 28, 전신 컷 0.5배 256×640 위 40(대사창 숨김), 대사창 180px, 밤 sprite 0 | 검수용 합성과 B-10-07의 기준 수치(`spec.md` 7절) | B 화면 배치와 함께 바꾼다 |
| `spec.md` 3절 | 아이콘 10개 | UI 아이콘 ID 목록 | 추가하면 기대 수가 늘어남 |
| 광고 크기 | 계약 `SLOT_SIZES` | 728×90, 300×250(P-19 잠정) | 계약 변경 → 픽스처 재생성 → 다시 실행 |
| `--repo` | 이 저장소 | 출력 루트 | 테스트에서 임시 폴더로 바꿔 쓴다 |
| 로그 | `out/logs/assets.jsonl` + 표준 출력 | 구조화 로그(README 5절) | — |

## 한계

- 임시 sprite·초상은 Claude가 코드로 그린 실루엣·배치 확인용 그림이라 인물마다 머리·모자·옷이 달라 화면에서 구분된다. 다만 품질은 실제 에셋 수준이 아니다(얼굴 틀이 모두 같고, 표정·세부 묘사가 없다). 새 인물 ID는 코드의 인물 데이터(`CHARACTERS`)에 넣기 전까지 평범한 주민 모양(`DEFAULT_RESIDENT`)으로 나온다.
- 장소·UI·지도·광고 임시 그림은 kind마다 같은 도형이다. id 해시로 보조색만 달라서, 화면에서 구분하는 용도로는 쓰지 못한다.
- 형식(PNG 구조·CRC·데이터 길이)·크기·용량·누락·남는 파일·manifest path/url 불일치, sprite·배경 규칙(`vn.py`), 초상-sprite 쌍만 본다. 생성 기록(에셋2)과 정식 검사는 `check_assets.py`(E-08-07)가 맡는다.
- 임시 sprite는 기존 흉상 머리를 0.8배로 옮기고 몸통·하의·신발을 이어 그린 것이다. 머리 위 여백은 모자·머리 모양에 따라 32~84px다(눈높이를 모두 같게 고정했기 때문). 손이 허리선(y=640) 아래라 평소 화면에서는 보이지 않는다.
- 눈높이·허리선은 그림에서 자동으로 찾을 수 없어 사람 검수 항목이다(자동은 가운데·머리 위 여백·발끝만).
- PNG 읽기(`png.decode`)는 8비트 RGB/RGBA·인터레이스 없음만 지원한다. 다른 형식으로 저장한 실제 sprite는 오류로 알려 준다.
- 지도 장소 영역 좌표는 아직 없다(실제 지도 E-11-07에서 manifest에 추가).
- 결과 JSON은 `tools/evidence_result.write_result`(E-05-02)가 없으면 같은 형식으로 직접 쓴다. E-05-02가 병합되면 그 함수를 쓴다.
- 화풍은 애니풍에 가까운 반실사 일러스트(11/5 PM 결정)다. 임시 그림은 이 화풍을 따르지 않는다(실제 파일로 덮어쓰면 사라진다). 실제 에셋 생성 도구(P-10)와 투명 바탕 처리 스크립트는 A-08-04에서 정한다.
- 반실사 실제 sprite는 임시 그림보다 용량이 크다. 500KB 안에 드는지는 A-08-04가 측정하고, 허용하는 줄이기 방법은 무손실 재압축뿐이다(`spec.md` 6절).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-05-05 | 11/5 | 규격표(도트 격자 포함)·스타일 가이드(도트), 임시 세트 생성기(`make_placeholders`)와 37개 임시 세트·manifest, 임시 초상 13장은 코드로 그린 도트 그림(`portrait_art`) |
| E-08-07 | 11/8 | (11/5 사전 작업) 유형 sprite·background 추가(기대 52), 초상을 sprite 크롭으로 파생(`derived_from`, 쌍 상태 검사), RGBA PNG 쓰기·읽기, sprite·배경 검사(`vn.py`), 검수용 합성(`preview_vn`), 임시 sprite 13장·배경 2장. 같은 날 화풍을 반실사로 바꿔(크로스체크 18) sprite를 전신 512×1280으로, 검사를 px 기준선으로 바꾸고 도트 격자·팔레트·블록·알파 이진 검사를 뺐다. 초상은 알파 합성 크롭, 배치는 평소/전신 컷 두 모드 |
