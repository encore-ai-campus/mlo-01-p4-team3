"""태스크 마크다운 → GitHub 이슈 동기화.

원본은 `docs/tasks/*.md`이고, 이슈는 진행 관리용이다(`docs/tasks/README.md` 11절).

    python -m tools.tasks_to_issues --dry-run                    # 기본: 출력만
    python -m tools.tasks_to_issues --hours                      # 흐름별 일별 시간표
    python -m tools.tasks_to_issues --apply --repo OWNER/NAME [--assign A=login,...]

구성(작은 도구라 한 파일 안에서 함수 단위로 나눈다):
- 계획(순수 함수): `desired_issues`, `plan_creates`, `plan_updates` — 무엇을 만들고 고칠지 계산만 한다.
- GitHub 연동: `GhClient`(gh CLI 호출). 테스트는 같은 메서드를 가진 가짜로 바꿔 끼운다.
- 실행: `apply_plan`, `main`.

멱등: 이슈 본문 첫 줄의 `<!-- task-id: X -->` 표식으로 기존 이슈를 찾는다. 같은 내용이면 아무것도 바꾸지 않는다.
이슈의 열림·닫힘 상태와 댓글은 건드리지 않는다. 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tools.tasks_md import TASK_LIMITS, UNASSIGNED_FLOWS, Decision, Task, hours_table, load_all

ROOT = Path(__file__).resolve().parents[1]
YEAR = 2026
MARKER_RE = re.compile(r"<!--\s*(?:task|decision)-id:\s*([A-Z]+-\d{2}(?:-\d{2})?)\s*-->")
MANAGED_PREFIXES = ("flow:", "day:", "type:")
MANAGED_EXACT = ("review-required", "asset")
MILESTONES = [
    # (제목, 마감일 MM-DD, 이 날짜까지의 태스크가 들어감)
    ("통합 1 (11/7)", "11-07"),
    ("통합 2 (11/9)", "11-09"),
    ("통합 3 (11/11)", "11-11"),
    ("발표 (11/12)", "11-12"),
]
FLOW_NAMES = {"A": "A 엔진", "B": "B 웹·API", "C": "C 인프라/파이프라인", "D": "D ML·광고", "E": "E PM", "S": "발표 트랙",
              "INT": "통합"}
# 흐름 → 라벨. 발표 트랙(S)은 담당이 P-24로 정해질 때까지 흐름 라벨 대신 트랙 라벨을 쓴다.
FLOW_LABELS = {"A": "flow:A", "B": "flow:B", "C": "flow:C", "D": "flow:D", "E": "flow:E", "S": "flow:presentation"}


# ---------------------------------------------------------------- 계획(순수 함수)

@dataclass
class DesiredIssue:
    """마크다운에서 계산한, 있어야 할 이슈 하나."""

    key: str  # 태스크 ID 또는 P-xx
    title: str
    labels: list[str]
    milestone: str | None
    flow: str | None
    deps: list[str] = field(default_factory=list)
    body_template: str = ""  # `{{ref:ID}}` 자리표시자를 이슈 번호로 바꿔 본문을 만든다


@dataclass
class ExistingIssue:
    number: int
    title: str
    body: str
    labels: list[str]
    milestone: str | None
    assignees: list[str]
    state: str = "OPEN"  # OPEN | CLOSED (gh issue list --json state)


@dataclass
class Action:
    kind: str  # create | update | assign
    key: str
    number: int | None = None
    title: str | None = None
    body: str | None = None
    add_labels: list[str] = field(default_factory=list)
    remove_labels: list[str] = field(default_factory=list)
    milestone: str | None = None
    clear_milestone: bool = False  # 원하는 마일스톤이 없는데 기존 이슈에 남아 있으면 제거
    assignee: str | None = None


def milestone_for(date_key: str | None) -> str | None:
    """MM-DD 날짜가 들어갈 마일스톤(그 날짜 이후 첫 통합). 날짜를 모르면 None."""
    if not date_key:
        return None
    for title, due in MILESTONES:
        if date_key <= due:
            return title
    return MILESTONES[-1][0]


def _deadline_key(deadline: str) -> str | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})", deadline)
    return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


def _source_link(source: str, repo: str | None) -> str:
    if repo:
        return f"[{source}](https://github.com/{repo}/blob/main/{source})"
    return f"`{source}`"


def desired_issues(tasks: list[Task], decisions: list[Decision], repo: str | None = None) -> list[DesiredIssue]:
    """태스크·결정 대기 목록을 이슈 목록으로 바꾼다."""
    out: list[DesiredIssue] = []
    for t in tasks:
        labels = [f"type:{t.kind}"]
        if t.flow in FLOW_LABELS:
            labels.append(FLOW_LABELS[t.flow])
        if t.date:
            labels.append(f"day:{t.date}")
        if "검수" in t.tags:
            labels.append("review-required")
        if "에셋" in t.tags:
            labels.append("asset")
        refs = " · ".join(f"{{{{ref:{d}}}}}" for d in t.deps) or "없음"
        decs = " · ".join(f"{{{{ref:{p}}}}}" for p in t.decisions) or "없음"
        date_txt = f"{int(t.date[:2])}/{int(t.date[3:])}" if t.date else "-"
        hours_txt = (f"{t.hours:g}h + 문서화 {t.doc_hours + t.batch_hours:g}h" if t.kind == "task"
                     else "주관 태스크 시간에 포함")
        conds = ", ".join(t.conditions) or "-"
        body = "\n".join([
            f"<!-- task-id: {t.task_id} -->",
            f"**흐름** {FLOW_NAMES.get(t.flow, t.flow)} · **날짜** {date_txt} · **시간** {hours_txt}",
            f"**원본** {_source_link(t.source, repo)} (이 본문은 `tools/tasks_to_issues.py`가 만든다. 직접 고치지 말고 댓글에 쓴다)",
            f"**선행** {refs}",
            f"**결정 대기** {decs}",
            "",
            "---",
            "",
            t.body,
            "",
            "---",
            "",
            f"**완료 판정**: 조건 {conds} + 공통 완료 정의 ①~⑧(README 3절). "
            f"`python -m tools.check_evidence {t.task_id} --complete`가 통과한 PR(`Closes #이 이슈`)의 병합으로만 닫힌다.",
            f"**증빙** `docs/evidence/{t.task_id}.md`",
        ])
        tag_txt = "".join(f" [{x}]" for x in t.tags)
        out.append(DesiredIssue(
            key=t.task_id, title=f"[{t.task_id}] {t.title}{tag_txt}", labels=sorted(labels),
            milestone=milestone_for(t.date), flow=t.flow, deps=list(t.deps) + list(t.decisions), body_template=body,
        ))
    for d in decisions:
        body = "\n".join([
            f"<!-- decision-id: {d.decision_id} -->",
            f"**기한** {d.deadline} · **막히는 태스크** {d.blocks}",
            f"**원본** {_source_link('docs/tasks/README.md', repo)} 7절",
            "",
            d.what,
            "",
            f"결정이 나면 README 7절 \"확정된 결정\"에 기록하는 PR을 내고, PR 본문에 `Task: {d.decision_id}`와 `Closes #이 이슈`를 쓴다.",
        ])
        out.append(DesiredIssue(
            key=d.decision_id, title=f"[{d.decision_id}] {_short(d.what)}", labels=["type:decision"],
            milestone=milestone_for(_deadline_key(d.deadline)), flow="E", body_template=body,
        ))
    return out


def _short(text: str, limit: int = 70) -> str:
    plain = re.sub(r"[*`]", "", text)
    plain = plain.split(". ")[0].split("(")[0].strip()
    return plain if len(plain) <= limit else plain[: limit - 1] + "…"


def render_body(template: str, numbers: dict[str, int]) -> str:
    """`{{ref:ID}}`를 `#번호 ID`로 바꾼다. 번호가 아직 없으면 ID만 남긴다."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        return f"#{numbers[key]} {key}" if key in numbers else key
    return re.sub(r"\{\{ref:([A-Z]+-\d{2}(?:-\d{2})?)\}\}", sub, template)


def index_existing(issues: list[ExistingIssue]) -> dict[str, ExistingIssue]:
    """본문 표식으로 기존 이슈를 키별로 찾는다. 같은 키가 둘 이상이면 번호가 작은 것을 쓴다."""
    found: dict[str, ExistingIssue] = {}
    for iss in sorted(issues, key=lambda i: i.number):
        m = MARKER_RE.search(iss.body or "")
        if m and m.group(1) not in found:
            found[m.group(1)] = iss
    return found


def plan_orphans(desired: list[DesiredIssue], existing: list[ExistingIssue]) -> list[tuple[int, str]]:
    """표식이 있는데 원본 마크다운에 더는 없는 **열린** 이슈(번호, 키). 태스크 ID를 바꾸거나 옮기면 생긴다.

    자동으로 닫지 않는다(밖으로 나가는 동작이고 이력·담당이 걸려 있다). 출력만 하고 PM이 처리한다(README 11절).
    """
    want = {d.key for d in desired}
    out = []
    for iss in sorted(existing, key=lambda i: i.number):
        m = MARKER_RE.search(iss.body or "")
        if m and m.group(1) not in want and iss.state.upper() == "OPEN":
            out.append((iss.number, m.group(1)))
    return out


def _managed(label: str) -> bool:
    return label.startswith(MANAGED_PREFIXES) or label in MANAGED_EXACT


def plan_creates(desired: list[DesiredIssue], existing: list[ExistingIssue]) -> list[Action]:
    """1단계: 없는 이슈를 만드는 동작. 본문의 선행 링크는 아직 번호 없이 만든다.

    2단계(`plan_updates`)가 모든 번호가 정해진 뒤 본문을 다시 그려, 실제로 다른 것만 갱신한다.
    그래서 두 번 실행하면 두 번째는 동작 0개다.
    """
    by_key = index_existing(existing)
    return [
        Action(kind="create", key=d.key, title=d.title, body=render_body(d.body_template, {}),
               add_labels=list(d.labels), milestone=d.milestone)
        for d in desired if d.key not in by_key
    ]


def plan_updates(desired: list[DesiredIssue], existing: list[ExistingIssue],
                 assign: dict[str, str] | None = None) -> list[Action]:
    """모든 이슈가 존재한다고 보고(1단계 뒤) 갱신·담당 지정 동작을 계산한다."""
    by_key = index_existing(existing)
    numbers = {k: v.number for k, v in by_key.items()}
    actions: list[Action] = []
    for d in desired:
        cur = by_key.get(d.key)
        if cur is None:
            continue
        body = render_body(d.body_template, numbers)
        want = set(d.labels)
        have = set(cur.labels)
        add = sorted(want - have)
        remove = sorted(l for l in have - want if _managed(l))
        changed_title = cur.title != d.title
        changed_body = (cur.body or "").strip() != body.strip()
        changed_ms = (cur.milestone or None) != (d.milestone or None)
        if changed_title or changed_body or add or remove or changed_ms:
            actions.append(Action(
                kind="update", key=d.key, number=cur.number,
                title=d.title if changed_title else None, body=body if changed_body else None,
                add_labels=add, remove_labels=remove, milestone=d.milestone if changed_ms else None,
                clear_milestone=changed_ms and not d.milestone,
            ))
        if assign:
            flow = "E" if d.flow == "INT" else d.flow
            login = assign.get(flow or "")
            if login and login not in cur.assignees:
                actions.append(Action(kind="assign", key=d.key, number=cur.number, assignee=login))
    return actions


def parse_labels_yml(text: str) -> list[dict[str, str]]:
    """`.github/labels.yml`(항목마다 name, color, description)을 읽는다."""
    labels: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        m = re.match(r'^\s*(-\s+)?(name|color|description):\s*"?(.*?)"?\s*$', line)
        if not m:
            continue
        if m.group(1):
            cur = {}
            labels.append(cur)
        if cur is not None:
            cur[m.group(2)] = m.group(3)
    return [l for l in labels if "name" in l]


def parse_assign(value: str | None) -> dict[str, str]:
    """`A=login1,B=login2` → {"A": "login1", ...}."""
    if not value:
        return {}
    out: dict[str, str] = {}
    for part in value.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip().upper()] = v.strip()
    return out


# ---------------------------------------------------------------- GitHub 연동(gh CLI)

class GhClient:
    """gh CLI 호출. 모든 호출은 인자 목록으로 넘겨 셸 해석을 거치지 않는다."""

    def __init__(self, repo: str):
        self.repo = repo

    def _run(self, args: list[str]) -> str:
        res = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:3])} 실패: {res.stderr.strip()}")
        return res.stdout

    def list_issues(self) -> list[ExistingIssue]:
        out = self._run(["issue", "list", "--repo", self.repo, "--state", "all", "--limit", "1000",
                         "--json", "number,title,body,labels,milestone,assignees,state"])
        items = json.loads(out or "[]")
        return [ExistingIssue(
            number=i["number"], title=i["title"], body=i.get("body") or "",
            labels=[l["name"] for l in i.get("labels") or []],
            milestone=(i.get("milestone") or {}).get("title"),
            assignees=[a["login"] for a in i.get("assignees") or []],
            state=i.get("state") or "OPEN",
        ) for i in items]

    def ensure_label(self, name: str, color: str, description: str) -> None:
        self._run(["label", "create", name, "--repo", self.repo, "--color", color,
                   "--description", description, "--force"])

    def list_milestones(self) -> list[str]:
        out = self._run(["api", f"repos/{self.repo}/milestones?state=all&per_page=100"])
        return [m["title"] for m in json.loads(out or "[]")]

    def create_milestone(self, title: str, due_mmdd: str) -> None:
        # 마감은 그날 23:59:59 KST(= 14:59:59Z)
        due = f"{YEAR}-{due_mmdd}T14:59:59Z"
        self._run(["api", "-X", "POST", f"repos/{self.repo}/milestones", "-f", f"title={title}", "-f", f"due_on={due}"])

    def create_issue(self, title: str, body: str, labels: list[str], milestone: str | None) -> int:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(body)
            path = f.name
        try:
            args = ["issue", "create", "--repo", self.repo, "--title", title, "--body-file", path]
            for l in labels:
                args += ["--label", l]
            if milestone:
                args += ["--milestone", milestone]
            url = self._run(args).strip().splitlines()[-1]
        finally:
            Path(path).unlink(missing_ok=True)
        return int(url.rstrip("/").rsplit("/", 1)[-1])

    def edit_issue(self, number: int, title: str | None, body: str | None, add: list[str],
                   remove: list[str], milestone: str | None, assignee: str | None = None,
                   clear_milestone: bool = False) -> None:
        args = ["issue", "edit", str(number), "--repo", self.repo]
        path = None
        if title:
            args += ["--title", title]
        if body is not None:
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
                f.write(body)
                path = f.name
            args += ["--body-file", path]
        for l in add:
            args += ["--add-label", l]
        for l in remove:
            args += ["--remove-label", l]
        if milestone:
            args += ["--milestone", milestone]
        elif clear_milestone:
            args += ["--remove-milestone"]
        if assignee:
            args += ["--add-assignee", assignee]
        try:
            self._run(args)
        finally:
            if path:
                Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------- 실행

def apply_plan(gh, desired: list[DesiredIssue], labels: list[dict[str, str]],
               assign: dict[str, str], log=print) -> dict[str, int]:
    """라벨 → 마일스톤 → 이슈 생성(1단계) → 본문·라벨 갱신과 담당 지정(2단계)."""
    for l in labels:
        gh.ensure_label(l["name"], l.get("color", "ededed"), l.get("description", ""))
    have_ms = set(gh.list_milestones())
    ms_created = 0
    for title, due in MILESTONES:
        if title not in have_ms:
            gh.create_milestone(title, due)
            ms_created += 1
    creates = plan_creates(desired, gh.list_issues())
    for a in creates:
        num = gh.create_issue(a.title or a.key, a.body or "", a.add_labels, a.milestone)
        log(f"생성 #{num} {a.key}")
    updates = plan_updates(desired, gh.list_issues(), assign)
    n_upd = n_asg = 0
    for a in updates:
        if a.kind == "update":
            gh.edit_issue(a.number, a.title, a.body, a.add_labels, a.remove_labels, a.milestone,
                          clear_milestone=a.clear_milestone)
            n_upd += 1
        elif a.kind == "assign":
            gh.edit_issue(a.number, None, None, [], [], None, assignee=a.assignee)
            n_asg += 1
    orphans = plan_orphans(desired, gh.list_issues())
    for num, key in orphans:
        log(f"원본에 없는 열린 이슈 #{num} {key} — 새 ID 이슈에 링크를 댓글로 남기고 닫을지 PM이 정한다(README 11절)")
    return {"labels": len(labels), "milestones_created": ms_created, "created": len(creates),
            "updated": n_upd, "assigned": n_asg, "orphans": len(orphans)}


def print_hours(tasks: list[Task], out=sys.stdout) -> None:
    table = hours_table(tasks)
    days = sorted({d for f in table.values() for d in f})
    out.write("흐름 | " + " | ".join(d[3:] for d in days) + " | 한도 | 초과 합계\n")
    for flow in sorted(table, key=lambda f: (f in UNASSIGNED_FLOWS, f)):
        row = table[flow]
        cells = " | ".join(f"{row.get(d, 0.0):g}" for d in days)
        if flow in UNASSIGNED_FLOWS:
            # 담당 미정(P-24): 어느 흐름의 한도에도 넣지 않고 별도 줄로만 보인다.
            out.write(f"{flow}(발표 트랙, 담당 미정) | {cells} | - | -\n")
            continue
        limit = TASK_LIMITS.get(flow, 7.5)
        over = sum(max(0.0, row.get(d, 0.0) - limit) for d in days)
        out.write(f"{flow} | {cells} | {limit:g} | {over:g}\n")


def main(argv: list[str] | None = None) -> int:
    # Windows 콘솔(cp949)에서 한글 출력이 깨지지 않게 한다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="태스크 마크다운 → GitHub 이슈 동기화(기본은 dry-run)")
    p.add_argument("--apply", action="store_true", help="실제로 GitHub에 쓴다")
    p.add_argument("--dry-run", action="store_true", help="출력만 한다(기본)")
    p.add_argument("--repo", help="OWNER/NAME (--apply에 필수)")
    p.add_argument("--assign", help="흐름별 담당자 GitHub 로그인. 예: A=login1,B=login2")
    p.add_argument("--hours", action="store_true", help="흐름별 일별 시간표를 출력")
    p.add_argument("--tasks-dir", default=str(ROOT / "docs" / "tasks"))
    args = p.parse_args(argv)

    tasks, decisions = load_all(Path(args.tasks_dir))
    if args.hours:
        print_hours(tasks)
        return 0
    labels = parse_labels_yml((ROOT / ".github" / "labels.yml").read_text(encoding="utf-8"))
    desired = desired_issues(tasks, decisions, args.repo)
    assign = parse_assign(args.assign)

    if not args.apply:
        kinds: dict[str, int] = {}
        for d in desired:
            k = next((l for l in d.labels if l.startswith("type:")), "?")
            kinds[k] = kinds.get(k, 0) + 1
        per_ms: dict[str, int] = {}
        for d in desired:
            per_ms[d.milestone or "(없음)"] = per_ms.get(d.milestone or "(없음)", 0) + 1
        print(f"[dry-run] 라벨 {len(labels)}개, 마일스톤 {len(MILESTONES)}개, 이슈 {len(desired)}개")
        print("  종류별: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
        print("  마일스톤별: " + ", ".join(f"{k} {v}" for k, v in per_ms.items()))
        missing = sorted({dep for d in desired for dep in d.deps} - {d.key for d in desired})
        print(f"  선행 참조 중 이슈가 없는 ID: {', '.join(missing) if missing else '없음'}")
        for d in desired:
            print(f"  {d.key:9} {d.milestone or '-':14} {','.join(d.labels):55} {d.title}")
        return 0

    if not args.repo:
        p.error("--apply에는 --repo OWNER/NAME이 필요하다")
    summary = apply_plan(GhClient(args.repo), desired, labels, assign)
    print(f"[apply] {args.repo}: 라벨 {summary['labels']}개 보장, 마일스톤 {summary['milestones_created']}개 생성, "
          f"이슈 {summary['created']}개 생성, {summary['updated']}개 갱신, 담당 지정 {summary['assigned']}건, "
          f"원본에 없는 열린 이슈 {summary['orphans']}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
