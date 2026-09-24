"""Gennemgår ventende opdateringer fra rasmus-skills for tegn på hacking.

Brug: python3 review_updates.py            (læser pending.json fra sessionstart-tjekket)
      python3 review_updates.py --recheck  (tjekker forfra, uden cache)
      python3 review_updates.py --done     (efter opdatering: ryd cachen)
Skriver en rapport pr. plugin med røde flag. Ændrer intet.
"""
import json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_updates as cu  # noqa: E402

RED_FLAGS = [  # stærke tegn: stop og undersøg
    (r"(curl|wget)[^\n|]*\|\s*(ba|z)?sh", "downloader og kører kode fra nettet (curl | sh)"),
    (r"base64\s+(-d|--decode)|b64decode|atob\(|String\.fromCharCode\(", "afkoder skjult indhold"),
    (r"[A-Za-z0-9+/]{300,}={0,2}", "meget lang kodet streng (kan skjule kode)"),
    (r"\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.npmrc|\.netrc|find-generic-password|find-internet-password|Login Data|Cookies\.binarycookies|(^|[\s\"'/(])\.env(\.local)?\b", "læser nøgler, adgangskoder, cookies eller .env"),
    (r"discord(app)?\.com/api/webhooks|api\.telegram\.org/bot|hooks\.slack\.com/services|webhook\.site|ngrok\.io|ngrok-free|pastebin\.com|requestbin|pipedream\.net", "sender data til webhook eller anonym tjeneste"),
    (r"https?://\d{1,3}(\.\d{1,3}){3}(?!\d)(?<!127\.0\.0\.1)", "kontakter en rå IP-adresse"),
    (r"(?i)ignore (all |any )?(previous|prior|other) instructions|do not (tell|inform|show) the user|without (asking|telling|informing) the user|skip (the |all )?confirmation|bypassPermissions|--dangerously", "instruktioner der vil omgå dig eller Claudes sikkerhed"),
    (r"(?i)(send|forward|upload|post|exfiltrat)[^\n]{0,60}(e-?mails?|gmail|inbox|passwords?|tokens?|credentials?|api[ _-]?keys?|ssh keys?)", "instruktioner om at sende mails, koder eller nøgler videre"),
    (r"(?i)crontab|launchctl|LaunchAgents|>>\s*~?/?\.?(zshrc|bashrc|bash_profile|profile)", "installerer noget, der starter automatisk på din Mac"),
]
WEAK_FLAGS = [  # normalt i programmer, men værd at kigge på uden for tests
    (r"(?<![.\w])(eval|exec)\s*\(|new Function\(|child_process|os\.system\(|subprocess\.(run|Popen|call|check_output)\(", "kører kommandoer eller dynamisk kode"),
    (r"(?i)\b(requests\.(get|post)|urllib\.request|fetch\(|axios|http\.request|https\.request)", "kontakter nettet"),
    (r"(?i)\"(postinstall|preinstall|install)\"\s*:", "npm-script der kører ved installation"),
]
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|fixtures|spec|examples?|demos?)/|_test\.|\.test\.|\.spec\.", re.I)
WATCH_FILES = re.compile(r"(^|/)(hooks/|hooks\.json|\.mcp\.json|plugin\.json|package\.json|settings\.json|install|setup)", re.I)


def git(args, cwd=None, timeout=120):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))


def review(u):
    rep = {"plugin": u["plugin"], "from": u["old_version"], "to": u["new_version"], "flags": [], "notes": []}
    tmp = tempfile.mkdtemp(prefix="guard-")
    r = git(["clone", "--quiet", "--filter=blob:none", "--no-checkout", u["url"], tmp])
    if r.returncode != 0:
        rep["flags"].append("kunne ikke hente repoet: " + r.stderr.strip()[:200])
        return rep
    have_old = git(["cat-file", "-e", u["old_sha"] + "^{commit}"], cwd=tmp).returncode == 0
    if not have_old:
        rep["flags"].append("din installerede version findes ikke længere i forfatterens historik (historikken er omskrevet – klassisk tegn på hacking eller oprydning)")
        return rep
    if git(["merge-base", "--is-ancestor", u["old_sha"], u["new_sha"]], cwd=tmp).returncode != 0:
        rep["flags"].append("den nye version bygger ikke videre på din (force-push)")
    scope = [u["path"]] if u.get("path") else []
    stat = git(["diff", "--stat", u["old_sha"], u["new_sha"], "--"] + scope, cwd=tmp).stdout.strip().splitlines()
    rep["notes"].append("ændrede filer: %s" % (stat[-1].strip() if stat else "ingen"))
    log = git(["log", "--format=%h %an <%ae>: %s", u["old_sha"] + ".." + u["new_sha"], "--"] + scope, cwd=tmp).stdout.strip().splitlines()
    authors = sorted({l.split(" ", 1)[1].split(":")[0] for l in log if " " in l})
    rep["notes"].append("%d commits af: %s" % (len(log), ", ".join(authors[:6]) or "ukendt"))
    rep["commits"] = log[:15]
    names = git(["diff", "--name-status", u["old_sha"], u["new_sha"], "--"] + scope, cwd=tmp).stdout.splitlines()
    for n in names:
        parts = n.split("\t")
        if len(parts) >= 2 and WATCH_FILES.search(parts[-1]):
            rep["notes"].append("følsom fil ændret (%s): %s" % (parts[0], parts[-1]))
    diff = git(["diff", "--unified=0", u["old_sha"], u["new_sha"], "--"] + scope, cwd=tmp, timeout=300).stdout
    cur = None
    weak = {}
    for line in diff.splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else line[4:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pat, why in RED_FLAGS:
            if re.search(pat, line):
                hit = "%s: %s  ->  %s" % (cur, why, line[1:].strip()[:160])
                if hit not in rep["flags"]:
                    rep["flags"].append(hit)
        if cur and not TEST_PATH.search(cur):
            for pat, why in WEAK_FLAGS:
                if re.search(pat, line):
                    weak.setdefault((cur, why), 0)
                    weak[(cur, why)] += 1
    rep["weak"] = ["%s: %s (%d steder)" % (f, w, n) for (f, w), n in sorted(weak.items())]
    rep["diff_command"] = "git -C %s diff %s %s -- %s" % (tmp, u["old_sha"][:12], u["new_sha"][:12], " ".join(scope))
    return rep


def main():
    if "--done" in sys.argv:
        for f in ("last-check.json", "pending.json"):
            try:
                os.remove(os.path.join(cu.data_dir(), f))
            except OSError:
                pass
        print("Cache ryddet. Næste sessionstart tjekker forfra.")
        return
    if "--recheck" in sys.argv:
        try:
            os.remove(os.path.join(cu.data_dir(), "last-check.json"))
        except OSError:
            pass
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            cu.main()
    pending = cu.load(os.path.join(cu.data_dir(), "pending.json"), [])
    if not pending:
        print("Ingen ventende opdateringer.")
        return
    for u in pending:
        rep = review(u)
        print("=" * 70)
        print("PLUGIN: %s   %s -> %s" % (rep["plugin"], rep["from"], rep["to"]))
        for n in rep["notes"]:
            print("  info: " + n)
        for c in rep.get("commits", []):
            print("  commit: " + c)
        if rep.get("weak"):
            print("  VÆRD AT KIGGE PÅ (%d):" % len(rep["weak"]))
            for w in rep["weak"][:25]:
                print("   ~ " + w)
        if rep["flags"]:
            print("  RØDE FLAG (%d):" % len(rep["flags"]))
            for f in rep["flags"][:40]:
                print("   ! " + f)
        else:
            print("  RØDE FLAG: ingen fundet af scanneren")
        if rep.get("diff_command"):
            print("  se hele ændringen: " + rep["diff_command"])


if __name__ == "__main__":
    main()
