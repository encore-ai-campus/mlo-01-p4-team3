# project — 저장소 운영(브랜치·라벨·이슈 동기화)

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

저장소 운영 규칙과 그 도구. 태스크 원본 `docs/tasks/*.md`를 GitHub 이슈·라벨·마일스톤으로 맞추는 `tools/tasks_to_issues.py`, 라벨 정의 `.github/labels.yml`, 이슈·PR 양식(`.github/ISSUE_TEMPLATE/`, `pull_request_template.md`)이 여기에 든다. 브랜치 모델과 병합 규칙은 [README 3절](../tasks/README.md)이 원본이다.
계층: 계획 계산(`desired_issues`·`plan_creates`·`plan_updates`, 도메인 규칙) / `GhClient`(인프라, gh CLI) / `apply_plan`(응용) / `main`(표현). 태스크 문서 해석은 `tools/tasks_md.py`.

## 왜

- 태스크 정의의 원본은 저장소 마크다운이고, 이슈는 진행 관리(담당·상태·토론)에만 쓴다(README 11절). 이슈를 손으로 만들면 원본과 어긋나므로 도구로 만들고 다시 돌려도 같은 결과가 나오게 했다(본문 첫 줄 `<!-- task-id: X -->` 표식으로 기존 이슈를 찾는다).
- 브랜치 모델: `dev`가 통합·기본 브랜치, `main`은 배포 브랜치(최종 배포 전 승격, 11/9 사용자 테스트는 리허설 태그로 띄움). 병합은 merge commit만(11/5 PM 결정, README 3절).
- 보호 규칙 대신 운영 규칙: 저장소가 비공개·조직 무료 플랜이라 브랜치 보호 규칙을 설정할 수 없다(HTTP 403, P-11). 병합은 PM만 하고, PR 화면에서 작성자 외 승인 1명과 `precheck: 통과` 줄을 확인한다.

## 사용법

```
python -m tools.tasks_to_issues --dry-run                                  # 만들고 고칠 것 출력만(기본)
python -m tools.tasks_to_issues --hours                                    # 흐름별 일별 시간표
python -m tools.tasks_to_issues --apply --repo <OWNER/NAME>                # 라벨·마일스톤·이슈 동기화
python -m tools.tasks_to_issues --apply --repo <OWNER/NAME> --assign A=<login>,B=<login>,...   # 담당자를 흐름 기준으로 덮어쓰기
gh issue list --search "<태스크 ID> in:title"                               # PR 본문에 쓸 이슈 번호 찾기
```

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `.github/labels.yml` | 라벨 33개 | 이름·색·설명. `flow:`·`day:`·`type:` 접두와 `review-required`·`asset`·`scope:extension`은 도구가 관리 | 관리 라벨을 손으로 붙이면 다음 동기화 때 원본 기준으로 바뀐다 |
| `MILESTONES` | 통합 1(11/7)·통합 2(11/9)·통합 3(11/11)·발표(11/12) | 예정일이 마감일까지인 태스크가 그 마일스톤에 든다 | 일정 변경 시 함께 고친다 |
| `YEAR` | 2026 | 태스크 날짜(MM-DD)의 연도 | 마일스톤 마감일 계산 |
| `--assign` | 없음 | 흐름 → GitHub 로그인. 지정하면 이슈 담당을 그 흐름 담당 1명으로 덮어쓴다 | 담당 변경 뒤 다시 돌려야 이슈에 반영된다 |

## 한계

- 이슈의 열림·닫힘 상태와 댓글은 건드리지 않는다. 원본에서 사라진 ID의 열린 이슈는 보고만 하고 닫지 않는다(사람이 새 ID를 적고 닫는다).
- 보호 규칙이 없어서 직접 푸시·잘못된 병합을 기계가 막지 못한다. `.githooks/pre-push`는 각자 설치해야 동작한다(설치는 [ci](ci.md)).
- `gh` 로그인과 저장소 쓰기 권한이 있어야 `--apply`가 된다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-05-01 | 11/5 | 처음 작성: 교육기관 저장소 배정(비공개·무료 플랜)에 맞춘 브랜치 모델(`dev` 통합·`main` 배포, merge commit만), 보호 규칙을 운영 규칙으로 대체(P-11), `tasks_to_issues --assign`이 흐름 담당으로 덮어쓰게 수정, 열린 이슈 130개 동기화 |
