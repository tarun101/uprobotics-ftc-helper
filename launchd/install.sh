#!/bin/bash
# Per-user installation; no administrator access required.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
label=${1:-me.uprobotics.ftc-index}
user_id=$(/usr/bin/id -u)
if [ "$user_id" -eq 0 ]; then
  echo "Run this as the indexer user, without sudo." >&2
  exit 1
fi
boot_dir=/Users/Shared/ftc-tools-$user_id
agent_dir="$HOME/Library/LaunchAgents"
/opt/homebrew/bin/python3 - "$repo" "$HOME" "$boot_dir" "$label" <<'PY'
import datetime, os, pathlib, plistlib, shutil, sys
repo, home, boot = map(pathlib.Path, sys.argv[1:4])
boot.mkdir(mode=0o700, exist_ok=True)
if boot.is_symlink() or boot.stat().st_uid != os.getuid():
    raise SystemExit(f"Refusing unexpected owner or symlink at {boot}")
boot.chmod(0o700)
label = sys.argv[4] + ".plist"
target = boot / label
agent = home / "Library/LaunchAgents" / label
agent.parent.mkdir(parents=True, exist_ok=True)
p = plistlib.loads((repo / "launchd" / (label + ".template")).read_bytes())
def expand(value):
    if isinstance(value, str):
        return value.replace("__REPO__", str(repo)).replace("__HOME__", str(home))
    if isinstance(value, list):
        return [expand(v) for v in value]
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    return value
p = expand(p)
assert pathlib.Path(p["ProgramArguments"][0]).is_file(), "Indexer Python environment missing"
p["LimitLoadToSessionType"] = "Aqua"
stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
if target.exists():
    shutil.copy2(target, boot / (label + '.previous-' + stamp))
temporary = boot / (label + '.new')
temporary.write_bytes(plistlib.dumps(p))
temporary.chmod(0o644)
temporary.replace(target)
if not (agent.is_symlink() and agent.resolve() == target):
    if agent.exists() or agent.is_symlink():
        agent.rename(agent.with_name(label + '.disabled-' + stamp))
    agent.symlink_to(target)
print(f"Installed {agent} -> {target}")
PY
/usr/bin/plutil -lint "$boot_dir/$label.plist"
/bin/launchctl enable "gui/$user_id/$label"
if /bin/launchctl print "gui/$user_id/$label" >/dev/null 2>&1; then
  echo "Existing job retained; the installed configuration takes effect at the next login."
else
  /bin/launchctl bootstrap "gui/$user_id" "$agent_dir/$label.plist"
fi
/bin/launchctl print "gui/$user_id/$label"
echo "Installed for this user's future logins. No administrator access was used."
