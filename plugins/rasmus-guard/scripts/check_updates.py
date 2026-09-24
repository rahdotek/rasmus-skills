"""Finder plugins fra rasmus-skills, hvor forfatteren har udgivet noget nyt.

Skriver kun noget ud, hvis der er opdateringer. Output bliver til kontekst for Claude.
Resultatet caches i 6 timer, så sessionstart ikke bliver langsom.
"""
import json, os, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

MARKET = "rasmus-skills"
SELF = "rasmus-guard"
CACHE_HOURS = 6


def claude_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def data_dir():
    # Fast sted, så sessionstart-tjekket og gennemgangen altid deler de samme filer.
    d = os.path.join(claude_dir(), "rasmus-guard")
    os.makedirs(d, exist_ok=True)
    return d


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def market_dir():
    known = load(os.path.join(claude_dir(), "plugins", "known_marketplaces.json"), {})
    loc = (known.get(MARKET) or {}).get("installLocation")
    return loc or os.path.join(claude_dir(), "plugins", "marketplaces", MARKET)


def sources():
    mp = load(os.path.join(market_dir(), ".claude-plugin", "marketplace.json"), {})
    out = {}
    for p in mp.get("plugins", []):
        s = p.get("source")
        if isinstance(s, dict) and s.get("source") in ("url", "git-subdir"):
            out[p["name"]] = {"url": s["url"], "path": s.get("path", "")}
        elif isinstance(s, dict) and s.get("source") == "github":
            out[p["name"]] = {"url": "https://github.com/%s.git" % s["repo"], "path": ""}
    return out


def remote_head(url):
    r = subprocess.run(["git", "ls-remote", url, "HEAD"], capture_output=True, text=True, timeout=15,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    return r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else None


def remote_version(url, path, sha):
    # Kun GitHub: læs plugin.json-versionen på den nye commit.
    if "github.com/" not in url:
        return None
    repo = url.split("github.com/")[1].replace(".git", "").strip("/")
    rel = (path.strip("/") + "/" if path else "") + ".claude-plugin/plugin.json"
    try:
        with urllib.request.urlopen("https://raw.githubusercontent.com/%s/%s/%s" % (repo, sha, rel), timeout=10) as f:
            return json.load(f).get("version")
    except Exception:
        return None


def check_one(name, rec, src):
    try:
        head = remote_head(src["url"])
    except Exception:
        return None
    old = rec.get("gitCommitSha")
    if not head or not old or head == old:
        return None
    installed_version = rec.get("version", "")
    versioned = installed_version and not old.startswith(installed_version)
    new_version = None
    if versioned:
        new_version = remote_version(src["url"], src["path"], head)
        # Forfatteren har ikke hævet versionsnummeret, så Claude Code ville ikke opdatere alligevel.
        if not new_version or new_version == installed_version:
            return None
    return {"plugin": name, "url": src["url"], "path": src["path"], "old_sha": old, "new_sha": head,
            "old_version": installed_version, "new_version": new_version or head[:12]}


def main():
    cache = os.path.join(data_dir(), "last-check.json")
    c = load(cache, {})
    if c and time.time() - c.get("t", 0) < CACHE_HOURS * 3600:
        found = c.get("found", [])
    else:
        installed = load(os.path.join(claude_dir(), "plugins", "installed_plugins.json"), {}).get("plugins", {})
        src = sources()
        if not src:
            return  # marketplacen kunne ikke læses; gem intet og prøv igen næste gang
        jobs = []
        for key, recs in installed.items():
            name, _, market = key.partition("@")
            if market != MARKET or name == SELF or name not in src or not recs:
                continue
            jobs.append((name, recs[0], src[name]))
        with ThreadPoolExecutor(max_workers=8) as ex:
            found = [r for r in ex.map(lambda j: check_one(*j), jobs) if r]
        with open(cache, "w") as f:
            json.dump({"t": time.time(), "found": found}, f)
        with open(os.path.join(data_dir(), "pending.json"), "w") as f:
            json.dump(found, f, indent=2)
    if not found:
        return
    lines = ["%s: %s -> %s" % (u["plugin"], u["old_version"], u["new_version"]) for u in found]
    print("RASMUS-GUARD: Der er nye versioner af plugins fra rasmus-skills:\n- " + "\n- ".join(lines) + "\n"
          "Inden du går i gang med brugerens opgave: fortæl Rasmus det i én linje, og kør skillen "
          "rasmus-guard:review-updates. Den gennemgår ændringerne for tegn på hacking og opdaterer kun det, der er rent. "
          "Hvis Rasmus siger, at han vil springe over nu, så fortsæt med hans opgave.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
