"""Publish the fork to GitHub through the REST API.

github.com's git endpoint is unreachable from this machine, so this script
builds the commit server-side: upload blobs, extend the remote tree, create
the commit, move the branch, tag it. Only changed files are uploaded.
"""
import base64, json, os, subprocess, sys, urllib.request, urllib.error

OWNER, REPO, BRANCH = "Baimacao", "gpui-patched", "main"
ROOT = os.path.dirname(os.path.abspath(__file__))
TOKEN = open(r"E:\DSH\home\.github-token").read().strip()
API = "https://api.github.com"


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {method} {path}: {e.read().decode()[:600]}")


def git(*a):
    return subprocess.check_output(["git", *a], cwd=ROOT, text=True).strip()


def main():
    head = git("rev-parse", "HEAD")
    remote = call("GET", f"/repos/{OWNER}/{REPO}/branches/{BRANCH}")["commit"]
    prev, base_tree = remote["sha"], remote["commit"]["tree"]["sha"]
    print(f"local head {head[:12]}  remote head {prev[:12]}")

    # The remote history was created through this API, so its commit SHAs never
    # match the local ones even when the trees are identical. Compare file
    # contents instead of commits.
    remote_blobs = {e["path"]: e["sha"] for e in
                    call("GET", f"/repos/{OWNER}/{REPO}/git/trees/{base_tree}?recursive=1")["tree"]
                    if e["type"] == "blob"}
    tracked = [f for f in git("ls-files").splitlines() if f.strip()]
    changed = []
    for path in tracked:
        full = os.path.join(ROOT, path)
        blob = call("POST", f"/repos/{OWNER}/{REPO}/git/blobs", {
            "content": base64.b64encode(open(full, "rb").read()).decode(),
            "encoding": "base64",
        })
        if remote_blobs.get(path) != blob["sha"]:
            changed.append((path, blob["sha"]))
    if not changed:
        print("nothing to publish")
        return
    print("changed:", ", ".join(p for p, _ in changed))

    entries = [{"path": p, "mode": "100644", "type": "blob", "sha": sha}
               for p, sha in changed]
    for p, sha in changed:
        print(f"  blob {p} {sha[:8]}")

    tree = call("POST", f"/repos/{OWNER}/{REPO}/git/trees",
                {"base_tree": base_tree, "tree": entries})["sha"]
    commit = call("POST", f"/repos/{OWNER}/{REPO}/git/commits",
                  {"message": git("log", "-1", "--pretty=%B"), "tree": tree, "parents": [prev]})
    call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
         {"sha": commit["sha"], "force": True})
    print(f"branch {BRANCH} -> {commit['sha'][:12]}")

    tag = sys.argv[1] if len(sys.argv) > 1 else None
    if tag:
        call("POST", f"/repos/{OWNER}/{REPO}/git/refs",
             {"ref": f"refs/tags/{tag}", "sha": commit["sha"]})
        print(f"tag {tag} created")
    return commit["sha"]


if __name__ == "__main__":
    main()
