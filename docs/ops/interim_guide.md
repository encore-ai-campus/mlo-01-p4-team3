# 팀원 안내서: 저장소 배정 전 작업·제출 방법

교육기관 GitHub 저장소를 받기 전까지 쓰는 방법이다. 규칙 원문은 [태스크 README 10-1절](../tasks/README.md)이다. 이 문서는 그중 팀원이 할 일만 순서대로 적었다.

- 코드는 **각자 노트북의 git**에서만 다룬다. NAS는 파일을 놓아두는 곳일 뿐이고, NAS 안에서 git 명령을 쓰지 않는다.
- 다 만든 작업은 **번들 파일 하나**로 NAS에 올린다. 합치기(병합)와 통합 테스트는 **PM만** 한다.
- NAS 주소·계정은 PM이 따로 알려 준다. **저장소·문서·채팅에 적지 않는다.**
- 명령은 모두 PowerShell 기준이고, 저장소 루트(`paldo`)에서 실행한다.

---

## 1. 처음 한 번

1. 개발 환경: [로컬 개발 환경](../../ops/compose/README.md) 2절을 따라 Docker·Python 3.14·가상환경을 준비한다.
2. 한글 출력 설정(한 번만, 새 PowerShell 창부터 적용):
   ```powershell
   [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
   ```
3. NAS의 `main/` 폴더에서 **가장 최근** main 번들(`main_<월일>_<시분>_<SHA12>.bundle`)을 노트북으로 복사한다.
4. 그 번들로 저장소를 만든다:
   ```powershell
   git clone -b main C:\받은폴더\main_1105_1800_0123456789ab.bundle paldo
   cd paldo
   ```
5. 커밋 작성자를 자기 이름으로 설정한다(이 저장소에만):
   ```powershell
   git config user.name "자기 이름"
   git config user.email "자기 이메일"
   ```

## 2. 새 main 받기 (PM이 새 번들을 올렸을 때)

```powershell
git switch main
git fetch C:\받은폴더\main_1106_1200_<SHA12>.bundle main
git merge --ff-only FETCH_HEAD
```

- `--ff-only`가 실패하면 main에 직접 커밋한 것이 있다는 뜻이다. main에는 커밋하지 않는다. 고치지 말고 PM에게 알린다.
- **번들 이름 끝의 SHA12가 다음 제출의 `--base` 값**이다. 어디 적어 둔다.

## 3. 태스크 하나 작업하기

```powershell
git switch main
git switch -c task/A-05-01          # 태스크 ID로 브랜치
# ... 작업 ...
git add <바꾼 파일들>               # 파일을 지정해서 올린다(git add . 금지)
git commit -m "A-05-01: 무엇을 왜 바꿨는지"
```

- 커밋 메시지에 `Closes`, `Fixes` 같은 닫기 키워드를 쓰지 않는다(로컬 CI가 실패시킨다).
- 비밀번호·접속 주소·`.env` 내용은 커밋하지 않는다. `check_secrets`가 잡는다.
- 증빙(`docs/evidence/<ID>.md`)과 트러블(`docs/troubles/<흐름>.md`)은 태스크 문서의 완료 조건대로 같은 브랜치에 커밋한다.

## 4. 제출하기

1. **모두 커밋했는지 확인**한다. `git status`가 깨끗해야 한다(커밋 안 한 파일이 있으면 제출 도구가 거부한다).
2. 서버 코드(`server/`, `ads/`, `tests/infra/`, `tests/ads/`)를 바꿨으면 Compose를 먼저 띄우고 서비스 테스트를 켠다:
   ```powershell
   docker compose -p paldo-dev -f ops/compose/dev.yml up -d
   $env:PALDO_SERVICE_TESTS = "1"
   ```
3. 제출 명령:
   ```powershell
   python -m tools.interim submit --task A-05-01 --base <받은 main SHA12> --hours 2 --seen \\NAS\...\inbox\A
   ```
   | 옵션 | 언제 |
   |---|---|
   | `--hours` | 실제 걸린 시간(숫자만, 예: `2.5`) |
   | `--complete` | 이 제출로 태스크를 끝낼 때(증빙 완료 검사까지 한다) |
   | `--kind fix` | 버그·검수 탈락을 고치는 제출일 때 |
   | `--seen` | NAS의 **자기 흐름 inbox 폴더**. 이미 올린 번호 다음 번호를 쓰게 한다. 항상 붙인다 |
4. 도구가 임시 폴더에서 테스트·검사를 돌린다. 통과하면 `out\interim\`에 두 파일이 생긴다:
   - `A-05-01_01_<SHA12>.bundle`
   - `A-05-01_01_<SHA12>.ci.txt`
   실패하면 아무 파일도 안 생긴다. 출력의 실패 단계를 보고 고친 뒤 다시 커밋하고 다시 제출한다.
5. **NAS에 올리기**: 두 파일을 `inbox\<자기 흐름>\`에 `.part`를 붙인 이름으로 복사하고, 복사가 끝나면 `.part`를 뗀다.
   ```powershell
   $inbox = "\\NAS\...\inbox\A"
   $no = "01"                          # submit 출력에 나온 이번 번호(재제출이면 02, 03 …)
   foreach ($f in Get-ChildItem "out\interim\A-05-01_${no}_*") {
     Copy-Item $f.FullName "$inbox\$($f.Name).part"
     Rename-Item "$inbox\$($f.Name).part" $f.Name
   }
   ```
   `.part`인 동안은 PM 도구가 읽지 않는다. 덜 복사된 파일을 PM이 가져가는 일을 막는다.

### 다시 제출할 때
- 고쳐서 커밋하고 같은 명령으로 다시 제출한다. 번호가 `_02`, `_03`으로 오른다. 올릴 때 위 `$no`를 그 번호로 바꾼다.
- 이전 번들은 **지우지 않는다.** 가장 큰 번호만 유효하다.
- **자기 흐름의 inbox에만 쓴다.** 남의 폴더나 `main/`에는 쓰지 않는다.

## 5. 다른 흐름 검토하기 (PR 승인 대신)

PM이 검토를 맡기면, 남의 제출을 보고 판정을 남긴다. 자기 흐름의 제출은 검토할 수 없다.

1. NAS `inbox\<작성 흐름>\`에서 번들을 복사해 와서 가져온다:
   ```powershell
   git fetch C:\받은폴더\B-05-02_01_<SHA12>.bundle task/B-05-02
   git log --oneline main..FETCH_HEAD     # 커밋 목록
   git diff main...FETCH_HEAD             # 바뀐 내용
   git rev-parse FETCH_HEAD               # 확인한 HEAD SHA(40자)
   ```
2. 태스크 문서의 완료 조건과 바뀐 내용을 대조한다. 필요하면 `git switch --detach FETCH_HEAD`로 꺼내 실행해 본다(본 뒤 `git switch main`).
3. `review\<자기 흐름>\B-05-02_01.review.txt`를 만든다(위 4-5처럼 `.part`로 복사 후 이름 바꾸기):
   ```
   검토 흐름: A
   확인한 HEAD SHA: <git rev-parse FETCH_HEAD 결과 40자>
   판정: 통과
   메모: 완료 조건 1~4 확인. 3번 테스트 실행해 봄.
   ```
   - 판정은 `통과` 또는 `반려` 중 하나만 쓴다. `반려`면 메모에 이유와 고칠 점을 적는다. 반려된 제출은 병합되지 않고 재제출을 기다린다.

## 6. 하루 흐름 요약

| 때 | 할 일 |
|---|---|
| 시작 | NAS `main/`의 최신 번들로 main 받기(2절), 진행표 `status_<월일>_<시분>.md`에서 자기 태스크 상태 확인 |
| 작업 | `task/<ID>` 브랜치에서 작업·커밋(3절) |
| 끝낼 때마다 | 제출(4절), NAS에 올리기 |
| 맡은 검토 | 검토 기록(5절) |
| 질문 | 설계·규칙 질문은 PM에게. 비밀값은 메신저에 붙여 넣지 않는다 |

진행표 상태: `제출됨`(NAS에 번들만 있음) → `병합됨`(PM이 main에 합침) → `완료`(증빙 완료 + 검토 통과). GitHub가 있어야 끝나는 조건(태스크 문서에 **(저장소 배정 후)** 표시)은 배정 전에는 판정하지 않는다. 그래서 그런 태스크는 `병합됨`으로 남아도 정상이다.

## 7. 막힐 때

| 증상 | 대응 |
|---|---|
| `커밋하지 않은 변경` 때문에 제출 거부 | `git status`로 보고 커밋하거나, 필요 없는 파일이면 지운다 |
| `service` 단계 실패: 서비스 테스트를 안 돌림 | Compose를 띄우고 `$env:PALDO_SERVICE_TESTS = "1"` 뒤 다시 제출 |
| `docs` 단계 실패 | 출력에 나온 문서 규칙(태스크 README 10절)대로 모듈 문서·트러블을 고친다 |
| `evidence` 단계 실패 | `--complete`면 증빙 완료 조건을 확인한다. 커밋 메시지에 닫기 키워드가 있으면 새 커밋으로 덮을 수 없으니 PM에게 알린다 |
| `git merge --ff-only` 실패 | main에 직접 커밋한 것이 있다. 손대지 말고 PM에게 알린다 |
| 콘솔 한글이 깨짐 | `PYTHONUTF8=1` 설정(1절) 뒤 새 창을 연다 |
| `git clone` 뒤 브랜치가 이상함 | `-b main`을 빠뜨렸다. 폴더를 지우고 1절 4번을 다시 한다 |
