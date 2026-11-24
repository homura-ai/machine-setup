#!/usr/bin/env bash
set -euo pipefail
tools=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
export XDG_STATE_HOME="$tmp/state"
repo=$tmp/source
root=$tmp/worktrees
mkdir "$root"
git init -q "$repo"
git -C "$repo" config user.name 'Worktree Test'
git -C "$repo" config user.email 'worktrees@example.invalid'

printf 'node_modules/\nbuild/\nprivate-data/\n' > "$repo/.gitignore"
printf 'base\n' > "$repo/shared"
printf 'base\n' > "$repo/executable"
chmod +x "$repo/executable"
git -C "$repo" add .
git -C "$repo" commit -qm base
base=$(git -C "$repo" rev-parse HEAD)
git -C "$repo" update-ref refs/remotes/origin/main "$base"
git -C "$repo" branch existing "$base"

printf 'dirty\n' > "$repo/executable"
printf 'later\n' > "$repo/new"
git -C "$repo" add new
git -C "$repo" commit -qm later
mkdir "$repo/node_modules"
touch "$repo/node_modules/a dependency"

"$tools/wt-new" "$repo" "$root/safe old" existing
test "$(git -C "$root/safe old" rev-parse HEAD)" = "$base"
test "$(cat "$root/safe old/executable")" = base
test -x "$root/safe old/executable"
test ! -e "$root/safe old/new"
test -z "$(git -C "$root/safe old" status --porcelain)"
if cp --reflink=always "$repo/shared" "$tmp/reflink" 2>/dev/null; then
  test -e "$root/safe old/node_modules/a dependency"
fi

for name in active dirty private unpushed fresh assumed skipped mounted; do
  "$tools/wt-new" "$repo" "$root/$name" "test/$name" "$base"
done
printf 'private\n' > "$root/dirty/untracked"
mkdir "$root/private/private-data"
touch "$root/private/private-data/keep"
git -C "$root/unpushed" commit --allow-empty -qm local
mkdir "$root/safe old/build"
touch "$root/safe old/build/ignored output"
git -C "$root/assumed" update-index --assume-unchanged shared
git -C "$root/skipped" update-index --skip-worktree shared
printf 'local\n' > "$root/assumed/shared"
printf 'local\n' > "$root/skipped/shared"
mkdir "$root/mounted/build"
real_findmnt=$(command -v findmnt)
real_git=$(command -v git)
mkdir "$tmp/bin"
cat > "$tmp/bin/findmnt" <<'MOUNTS'
#!/usr/bin/env bash
"$WT_TEST_FINDMNT" "$@" || exit
printf '%s\n' "$WT_TEST_MOUNT"
MOUNTS
cat > "$tmp/bin/git" <<'GIT'
#!/usr/bin/env bash
if [[ "$1" == -C && "$2" == "$WT_TEST_MOUNTED_CHECKOUT" ]]; then
  touch "$WT_TEST_MOUNT_SCAN"
fi
exec "$WT_TEST_GIT" "$@"
GIT
chmod +x "$tmp/bin/findmnt" "$tmp/bin/git"
export PATH="$tmp/bin:$PATH" WT_TEST_FINDMNT="$real_findmnt" WT_TEST_GIT="$real_git"
export WT_TEST_MOUNT="$root/mounted/build" WT_TEST_MOUNTED_CHECKOUT="$root/mounted"
export WT_TEST_MOUNT_SCAN="$tmp/mount-scanned"
find "$root" -exec touch -d '9 days ago' -- {} +
touch "$root/fresh/shared"
(
  cd -- "$root/active"
  touch "$tmp/active-ready"
  exec sleep 60
) &
active=$!
trap 'kill "$active" 2>/dev/null || true; rm -rf -- "$tmp"' EXIT
for (( i=0; i<100; i++ )); do
  [[ ! -e "$tmp/active-ready" ]] || break
  sleep 0.02
done
test -e "$tmp/active-ready"

dry=$("$tools/wt-reap" "$root")
[[ "$dry" == "would remove $root/safe old"$'\n''wt-reap: 1 worktree(s) eligible' ]]
test -d "$root/safe old"
test ! -e "$XDG_STATE_HOME"
"$tools/wt-reap" --apply "$root"
test ! -e "$root/safe old"
test ! -e "$WT_TEST_MOUNT_SCAN"
for name in active dirty private unpushed fresh assumed skipped mounted; do
  test -d "$root/$name"
done
test "$(git -C "$repo" rev-parse existing)" = "$base"
test -n "$(sed -n '/removed.*branch=existing/p' "$XDG_STATE_HOME/wt-reap/log")"
echo 'Worktree tests passed'
