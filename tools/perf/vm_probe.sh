#!/usr/bin/env bash
# VM 안에서 실행하는 점검 스크립트(C-05-01). JSON 한 개를 표준 출력에 쓴다.
# 사용: ssh <접속 별칭> 'bash -s <논리 서버명>' < tools/perf/vm_probe.sh
#   예: ... 'bash -s ad-01' < tools/perf/vm_probe.sh
# hostname·IP는 출력하지 않는다(저장소에 옮길 때 논리 서버명만 남긴다. 기획서 6.3).
# 외부 의존 없음(bash, coreutils, /proc, systemctl이 있으면 서비스 상태).
set -u
NAME="${1:-unknown}"

json_escape() {
  local s="${1-}"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/ }"; s="${s//$'\r'/}"; s="${s//$'\t'/ }"
  printf '%s' "$s"
}

os_name="unknown"
if [ -r /etc/os-release ]; then
  os_name="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-${NAME:-unknown}}")"
fi
kernel="$(uname -r 2>/dev/null || echo unknown)"
python_ver="$( (python3 --version 2>&1 || echo none) | head -n1)"
vcpu="$(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo 2>/dev/null || echo 0)"

mem_total_mb=0; mem_avail_mb=0
if [ -r /proc/meminfo ]; then
  mem_total_mb=$(awk '/^MemTotal:/{printf "%d", $2/1024}' /proc/meminfo)
  mem_avail_mb=$(awk '/^MemAvailable:/{printf "%d", $2/1024}' /proc/meminfo)
fi
mem_used_mb=$(( ${mem_total_mb:-0} - ${mem_avail_mb:-0} ))

# 루트 파일 시스템(GB, 1024 기준 정수)
read -r disk_total_gb disk_used_gb disk_avail_gb <<EOF
$(df -BG / 2>/dev/null | awk 'NR==2{gsub("G","",$2); gsub("G","",$3); gsub("G","",$4); print $2, $3, $4}')
EOF
disk_total_gb="${disk_total_gb:-0}"; disk_used_gb="${disk_used_gb:-0}"; disk_avail_gb="${disk_avail_gb:-0}"

# 디스크별(OS·데이터): 블록 디스크 크기와 마운트별 사용량. 데이터 디스크가 어디에 붙었는지 본다.
disks_json=""
if command -v lsblk >/dev/null 2>&1; then
  while read -r dname dsize dtype; do
    [ "$dtype" = "disk" ] || continue
    disks_json+="{\"disk\":\"$(json_escape "$dname")\",\"size_gb\":\"$(json_escape "$dsize")\"},"
  done <<EOF
$(lsblk -d -n -o NAME,SIZE,TYPE --bytes 2>/dev/null | awk '{printf "%s %.1f %s\n", $1, $2/1073741824, $3}')
EOF
fi
disks_json="[${disks_json%,}]"
mounts_json=""
while read -r src target size used; do
  [ -z "${target:-}" ] && continue
  mounts_json+="{\"source\":\"$(json_escape "$src")\",\"target\":\"$(json_escape "$target")\",\"size_gb\":\"$(json_escape "$size")\",\"used_gb\":\"$(json_escape "$used")\"},"
done <<EOF
$(df -BG --output=source,target,size,used -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs 2>/dev/null | awk 'NR>1{gsub("G","",$3); gsub("G","",$4); print $1, $2, $3, $4}')
EOF
mounts_json="[${mounts_json%,}]"

# 서비스: systemd 유닛 이름 일부로 찾는다. 상태만(버전은 아래에서 가능한 것만).
services_json=""
if command -v systemctl >/dev/null 2>&1; then
  for key in kafka mysql mongod spark nifi garage prometheus grafana docker; do
    # 불러온 유닛(list-units)과 설치된 유닛 파일(list-unit-files)을 모두 본다. 부팅 뒤 한 번도 안 띄운 서비스도 나오게.
    units="$( { systemctl list-units --type=service --all --no-legend --plain 2>/dev/null | awk '{print $1}'; systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '{print $1}'; } | grep -i "$key" | grep -v '@\.service$' | sort -u | head -n3 | tr '\n' ' ')"
    [ -z "$units" ] && continue
    for u in $units; do
      st="$(systemctl is-active "$u" 2>/dev/null || true)"
      en="$(systemctl is-enabled "$u" 2>/dev/null || true)"
      services_json+="{\"unit\":\"$(json_escape "$u")\",\"active\":\"$(json_escape "$st")\",\"enabled\":\"$(json_escape "$en")\"},"
    done
  done
fi
services_json="[${services_json%,}]"

# 버전(설치돼 있을 때만)
ver() { "$@" 2>&1 | head -n1; }
versions_json=""
add_ver() { versions_json+="\"$1\":\"$(json_escape "$2")\","; }
command -v mysqld   >/dev/null 2>&1 && add_ver mysql   "$(ver mysqld --version)"
command -v mongod   >/dev/null 2>&1 && add_ver mongod  "$(ver mongod --version)"
command -v docker   >/dev/null 2>&1 && add_ver docker  "$(ver docker --version)"
command -v java     >/dev/null 2>&1 && add_ver java    "$(ver java -version)"
command -v prometheus >/dev/null 2>&1 && add_ver prometheus "$(ver prometheus --version)"
command -v grafana-server >/dev/null 2>&1 && add_ver grafana "$(ver grafana-server -v)"
for d in /opt/kafka /opt/kafka_*; do
  [ -d "$d/libs" ] || continue
  k="$(ls "$d/libs" 2>/dev/null | grep -oE '^kafka_[0-9.]+-[0-9.]+' | head -n1)"
  [ -n "$k" ] && add_ver kafka "$k" && break
done
for d in /opt/spark /opt/spark-*; do
  [ -r "$d/RELEASE" ] || continue
  add_ver spark "$(head -n1 "$d/RELEASE")"; break
done
versions_json="{${versions_json%,}}"

num() { case "${1-}" in ''|*[!0-9]*) printf 0 ;; *) printf '%s' "$1" ;; esac; }
vcpu=$(num "$vcpu"); mem_total_mb=$(num "$mem_total_mb"); mem_used_mb=$(num "$mem_used_mb")
disk_total_gb=$(num "$disk_total_gb"); disk_used_gb=$(num "$disk_used_gb"); disk_avail_gb=$(num "$disk_avail_gb")

printf '{"name":"%s","os":"%s","kernel":"%s","python":"%s","vcpu":%s,"mem_total_mb":%s,"mem_used_mb":%s,"disk_total_gb":%s,"disk_used_gb":%s,"disk_avail_gb":%s,"disks":%s,"mounts":%s,"services":%s,"versions":%s}\n' \
  "$(json_escape "$NAME")" "$(json_escape "$os_name")" "$(json_escape "$kernel")" "$(json_escape "$python_ver")" \
  "$vcpu" "$mem_total_mb" "$mem_used_mb" "$disk_total_gb" "$disk_used_gb" "$disk_avail_gb" \
  "$disks_json" "$mounts_json" "$services_json" "$versions_json"
