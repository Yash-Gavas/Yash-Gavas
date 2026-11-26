#!/usr/bin/env python3
"""
cards.py - render GitHub stat and repo cards as SVGs. Stdlib only.

Replaces github-readme-stats / github-profile-trophy / streak-stats, which are
shared public instances that go down (503), run out of quota (402) or time out.
These are files in your own repo, so they render as long as GitHub renders.

    python scripts/cards.py --user Yash-Gavas --leetcode YashGavas \
        --hackerrank yash_s_gavas --out assets

Writes <out>/card-stats-{dark,light}.svg, <out>/card-coding-{dark,light}.svg
(LeetCode badges incl. Knight/Guardian + HackerRank badges), plus one card per
repo listed in assets/projects.json, as <out>/card-<repo>-{dark,light}.svg.

Star and fork counts come from the live API on every run. Descriptions come from
projects.json when set, otherwise the repo's own GitHub description.

A token in $GITHUB_TOKEN unlocks the contribution and streak numbers (they need
the GraphQL API). Without one the card still renders, minus those three tiles.

HackerRank has no official API and sometimes blocks CI runners. If the fetch
fails, the card falls back to assets/hackerrank.json, e.g.
    [{"name": "Problem Solving", "stars": 5, "solved": 150}]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

UA = {"User-Agent": "cards.py"}
BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

THEMES = {
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "title": "#FFA116",
        "text": "#c9d1d9", "muted": "#8b949e", "value": "#e6edf3",
        "accent": "#FFA116", "hr": "#2EC866",
    },
    "light": {
        "bg": "#ffffff", "border": "#d0d7de", "title": "#C2410C",
        "text": "#1f2328", "muted": "#57606a", "value": "#1f2328",
        "accent": "#C2410C", "hr": "#1BA94C",
    },
}

# GitHub linguist colours for the languages likely to show up here
LANG_COLOR = {
    "JavaScript": "#f1e05a", "TypeScript": "#3178c6", "Python": "#3572A5",
    "HTML": "#e34c26", "CSS": "#563d7c", "C++": "#f34b7d", "C": "#555555",
    "Java": "#b07219", "Go": "#00ADD8", "Rust": "#dea584", "Shell": "#89e051",
    "PLpgSQL": "#336790", "Vue": "#41b883", "Ruby": "#701516", "PHP": "#4F5D95",
    "Jupyter Notebook": "#DA5B0B", "SCSS": "#c6538c", "Svelte": "#ff3e00",
}

FONT = "ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif"

# Octicon outlines, drawn on a 16x16 grid. These are paths rather than the
# characters U+2605 / U+2442 because those codepoints are missing from a lot of
# system fonts and fall back to a tofu box.
ICON_STAR = ("M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 "
             "2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 "
             "01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25z")
ICON_FORK = ("M5 5.372v.878c0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75v-.878a2.25 2.25 0 "
             "111.5 0v.878a2.25 2.25 0 01-2.25 2.25h-1.5v2.128a2.251 2.251 0 11-1.5 "
             "0V8.5h-1.5A2.25 2.25 0 013.5 6.25v-.878a2.25 2.25 0 111.5 0zM5 3.25a.75.75 0 "
             "10-1.5 0 .75.75 0 001.5 0zm6.75.75a.75.75 0 100-1.5.75.75 0 000 1.5zm-3 "
             "8.75a.75.75 0 100-1.5.75.75 0 000 1.5z")
ICON_REPO = ("M2 2.5A2.5 2.5 0 014.5 0h8.75a.75.75 0 01.75.75v12.5a.75.75 0 01-.75.75h-2.5a.75.75 "
             "0 110-1.5h1.75v-2h-8a1 1 0 00-.714 1.7.75.75 0 01-1.072 1.05A2.495 2.495 0 "
             "012 11.5v-9zm10.5-1V9h-8c-.356 0-.694.074-1 .208V2.5a1 1 0 011-1h8zM5 "
             "12.25v3.25a.25.25 0 00.4.2l1.45-1.087a.25.25 0 01.3 0L8.6 15.7a.25.25 0 "
             "00.4-.2v-3.25a.25.25 0 00-.25-.25h-3.5a.25.25 0 00-.25.25z")


def icon(path, x, y, size, fill):
    s = size / 16
    return (f'<path transform="translate({x:.1f},{y:.1f}) scale({s:.3f})" '
            f'fill="{fill}" d="{path}"/>')


# --------------------------------------------------------------------------- #
# api
# --------------------------------------------------------------------------- #


def rest(path: str, token: str | None):
    req = urllib.request.Request("https://api.github.com" + path, headers=dict(UA))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def graphql(query: str, variables: dict, token: str):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", data=body,
                                 headers={**UA, "Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


LEETCODE_QUERY = """
query($username:String!){
  matchedUser(username:$username){
    submitStatsGlobal{
      acSubmissionNum{ difficulty count }
    }
  }
  userContestRanking(username:$username){
    rating
    globalRanking
  }
}
"""


def fetch_leetcode(username: str):
    """Return (solved_total, easy, medium, hard, contest_rating|None) or None.

    LeetCode's own GraphQL endpoint - public, no auth needed for a public
    profile's solved counts. Same idiom as the GitHub GraphQL calls above:
    self-hosted rather than a third-party badge service that can vanish.
    """
    body = json.dumps({"query": LEETCODE_QUERY,
                       "variables": {"username": username}}).encode()
    req = urllib.request.Request(
        "https://leetcode.com/graphql", data=body,
        headers={**UA, "Content-Type": "application/json",
                 "Referer": f"https://leetcode.com/{username}/"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  leetcode stats unavailable ({e})", file=sys.stderr)
        return None

    user = (data.get("data") or {}).get("matchedUser")
    if not user:
        print(f"  leetcode stats unavailable: no such user '{username}'",
              file=sys.stderr)
        return None

    counts = {c["difficulty"]: c["count"]
              for c in user["submitStatsGlobal"]["acSubmissionNum"]}
    total = counts.get("All", 0)
    rating = None
    ranking = (data.get("data") or {}).get("userContestRanking")
    if ranking and ranking.get("rating"):
        rating = round(ranking["rating"])
    return total, counts.get("Easy", 0), counts.get("Medium", 0), counts.get("Hard", 0), rating


LEETCODE_BADGES_QUERY = """
query($username:String!){
  matchedUser(username:$username){
    badges{ displayName icon }
  }
}
"""

LEETCODE_CONTEST_QUERY = """
query($username:String!){
  userContestRanking(username:$username){
    rating
    topPercentage
    attendedContestsCount
    badge{ name }
  }
}
"""


def leetcode_gql(query: str, username: str):
    """POST one LeetCode GraphQL query. Returns the `data` dict or {}.

    Each query is sent on its own so one rejected field cannot wipe out the
    other half of the card, and GraphQL errors are printed so the Actions log
    says what went wrong.
    """
    body = json.dumps({"query": query, "variables": {"username": username}}).encode()
    req = urllib.request.Request(
        "https://leetcode.com/graphql", data=body,
        headers={**BROWSER_UA, "Content-Type": "application/json",
                 "Referer": f"https://leetcode.com/{username}/",
                 "Origin": "https://leetcode.com"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"  leetcode request failed ({e})", file=sys.stderr)
        return {}
    if payload.get("errors"):
        print(f"  leetcode graphql error: {payload['errors'][0].get('message')}",
              file=sys.stderr)
    return payload.get("data") or {}


def fetch_image_uri(url: str, max_bytes: int = 150_000):
    """Download an image and return it as a data: URI.

    GitHub serves README SVGs through <img>, which blocks any external
    resource inside them, so badge icons must be embedded, not linked.
    """
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://leetcode.com" + url
    try:
        req = urllib.request.Request(url, headers=BROWSER_UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(max_bytes + 1)
            ctype = r.headers.get_content_type()
    except Exception as e:  # noqa: BLE001 - any failure just means no icon
        print(f"  badge icon skipped ({e})", file=sys.stderr)
        return None
    if len(raw) > max_bytes:
        return None
    if not ctype.startswith("image/"):
        ctype = "image/png"
    return f"data:{ctype};base64,{base64.b64encode(raw).decode()}"


def fetch_leetcode_badges(username: str):
    """Return dict(contest=..., badges=[(name, data_uri|None)]) or None."""
    user = leetcode_gql(LEETCODE_BADGES_QUERY, username).get("matchedUser")
    contest = leetcode_gql(LEETCODE_CONTEST_QUERY, username).get("userContestRanking") or {}

    contest_info = None
    if contest.get("rating"):
        contest_info = {
            "badge": (contest.get("badge") or {}).get("name"),  # Knight / Guardian
            "rating": round(contest["rating"]),
            "top": contest.get("topPercentage"),
            "attended": contest.get("attendedContestsCount", 0),
        }

    raw = list((user or {}).get("badges") or [])
    if not raw and not contest_info:
        return None
    # contest tiers first, so Knight/Guardian always make the cut
    raw.sort(key=lambda b: 0 if b["displayName"] in ("Knight", "Guardian") else 1)
    badges = []
    for b in raw[:6]:
        uri = fetch_image_uri(b["icon"]) if b.get("icon") else None
        badges.append((b["displayName"], uri))
    return {"contest": contest_info, "badges": badges}


def fetch_hackerrank(username: str, fallback: Path):
    """Return [(badge_name, stars, solved)]. Tries the public endpoint, then a
    hand-edited JSON file (HackerRank blocks some CI IPs via Cloudflare)."""
    out = []
    url = f"https://www.hackerrank.com/rest/hackers/{username}/badges"
    try:
        req = urllib.request.Request(url, headers={**BROWSER_UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        for m in data.get("models", []):
            stars = int(m.get("stars") or 0)
            if m.get("badge_name") and stars > 0:
                out.append((m["badge_name"], min(stars, 5), int(m.get("solved") or 0)))
    except Exception as e:  # noqa: BLE001
        print(f"  hackerrank fetch failed ({e})", file=sys.stderr)

    if not out and fallback.exists():
        try:
            for x in json.loads(fallback.read_text(encoding="utf-8")):
                out.append((x["name"], min(int(x["stars"]), 5), int(x.get("solved", 0))))
            print(f"  hackerrank: using {fallback}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"  hackerrank fallback unreadable ({e})", file=sys.stderr)
    out.sort(key=lambda b: (-b[1], -b[2]))
    return out


CONTRIB_QUERY = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      contributionCalendar{
        totalContributions
        weeks{ contributionDays{ date contributionCount } }
      }
    }
  }
}
"""


def fetch_contributions(user: str, token: str | None):
    """Return (total, current_streak, longest_streak) or None without a token."""
    if not token:
        return None
    try:
        data = graphql(CONTRIB_QUERY, {"login": user}, token)
    except urllib.error.HTTPError as e:
        print(f"  contributions unavailable (HTTP {e.code})", file=sys.stderr)
        return None
    if data.get("errors"):
        print(f"  contributions unavailable: {data['errors'][0].get('message')}",
              file=sys.stderr)
        return None

    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = [(dt.date.fromisoformat(d["date"]), d["contributionCount"])
            for w in cal["weeks"] for d in w["contributionDays"]]
    days.sort()

    longest = run = 0
    for _, c in days:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)

    # Today counts only if it already has activity; an empty today does not
    # break a streak that was alive yesterday.
    current = 0
    for date, c in reversed(days):
        if c > 0:
            current += 1
        elif date != days[-1][0]:
            break
    return cal["totalContributions"], current, longest


# --------------------------------------------------------------------------- #
# svg helpers
# --------------------------------------------------------------------------- #


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def text_width(s: str, size: float) -> float:
    return len(s) * size * 0.53


def wrap(text: str, size: float, max_w: float, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_width(trial, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and words:
        # did everything fit?
        used = len(" ".join(lines).split())
        if used < len(words):
            while lines and text_width(lines[-1] + "…", size) > max_w:
                lines[-1] = lines[-1].rsplit(" ", 1)[0]
            lines[-1] += "…"
    return lines


def frame(w, h, c, body, label):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{esc(label)}" '
        f'font-family="{FONT}">'
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="10" '
        f'fill="{c["bg"]}" stroke="{c["border"]}"/>'
        f"{body}</svg>"
    )


# --------------------------------------------------------------------------- #
# cards
# --------------------------------------------------------------------------- #


def render_stats(user, sections, theme):
    """sections: list of (title, [(value, label), ...]) - each drawn as its own
    labelled block in one card, so GitHub and LeetCode sit together instead of
    as two separate images the eye has to reconcile."""
    c = THEMES[theme]
    pad = 22
    cols = 3
    rh = 46
    W = 480
    tw = (W - 2 * pad) / cols

    out = [
        f'<text x="{pad}" y="{pad + 14}" font-size="15" font-weight="700" '
        f'fill="{c["title"]}">{esc(user)}</text>',
        f'<text x="{W - pad}" y="{pad + 14}" font-size="11" text-anchor="end" '
        f'fill="{c["muted"]}">at a glance</text>',
        f'<line x1="{pad}" y1="{pad + 26}" x2="{W - pad}" y2="{pad + 26}" '
        f'stroke="{c["border"]}"/>',
    ]
    y = pad + 26
    for si, (title, tiles) in enumerate(sections):
        if not tiles:
            continue
        rows = (len(tiles) + cols - 1) // cols
        if si > 0:
            y += 14
            out.append(f'<line x1="{pad}" y1="{y:.0f}" x2="{W - pad}" y2="{y:.0f}" '
                        f'stroke="{c["border"]}" stroke-dasharray="3,3"/>')
            y += 20
        else:
            y += 24
        out.append(f'<text x="{pad}" y="{y:.0f}" font-size="10.5" font-weight="700" '
                    f'letter-spacing="0.06em" fill="{c["accent"]}">{esc(title.upper())}</text>')
        y += 22
        top = y
        for i, (value, label) in enumerate(tiles):
            cx = pad + (i % cols) * tw
            cy = top + (i // cols) * rh
            out.append(
                f'<text x="{cx:.0f}" y="{cy:.0f}" font-size="23" font-weight="700" '
                f'fill="{c["value"]}">{esc(value)}</text>'
            )
            out.append(
                f'<text x="{cx:.0f}" y="{cy + 17:.0f}" font-size="10.5" '
                f'fill="{c["muted"]}">{esc(label)}</text>'
            )
        y = top + (rows - 1) * rh + 17
    H = round(y + pad)
    return frame(W, H, c, "".join(out), f"{user} GitHub statistics")


def render_coding(lc, hr, theme):
    """LeetCode badge row (icons embedded as base64) + HackerRank hex badges."""
    c = THEMES[theme]
    W, pad = 480, 22
    cols = 6
    cw = (W - 2 * pad) / cols
    out = []
    y = pad + 14

    if lc:
        out.append(f'<text x="{pad}" y="{y}" font-size="15" font-weight="700" '
                   f'fill="{c["title"]}">LeetCode</text>')
        ct = lc.get("contest")
        if ct:
            bits = [ct["badge"]] if ct.get("badge") else []
            bits.append(f'{ct["rating"]} rating')
            if ct.get("top"):
                bits.append(f'top {ct["top"]:.1f}%')
            out.append(f'<text x="{W - pad}" y="{y}" font-size="11" text-anchor="end" '
                       f'fill="{c["muted"]}">{esc(" · ".join(bits))}</text>')
        y += 12
        out.append(f'<line x1="{pad}" y1="{y}" x2="{W - pad}" y2="{y}" stroke="{c["border"]}"/>')
        y += 14
        if lc["badges"]:
            size = 52
            for i, (name, uri) in enumerate(lc["badges"]):
                cx = pad + (i % cols) * cw + cw / 2
                if uri:
                    out.append(f'<image href="{uri}" x="{cx - size / 2:.1f}" y="{y}" '
                               f'width="{size}" height="{size}"/>')
                else:
                    out.append(f'<circle cx="{cx:.1f}" cy="{y + size / 2}" r="22" '
                               f'fill="none" stroke="{c["accent"]}" stroke-width="2"/>')
                label = name if len(name) <= 13 else name[:12] + "…"
                out.append(f'<text x="{cx:.1f}" y="{y + size + 14}" font-size="9.5" '
                           f'text-anchor="middle" fill="{c["muted"]}">{esc(label)}</text>')
            y += size + 22
        else:
            out.append(f'<text x="{pad}" y="{y + 10}" font-size="11" fill="{c["muted"]}">'
                       f'No badges yet</text>')
            y += 22

    if hr:
        if lc:
            y += 18
        out.append(f'<text x="{pad}" y="{y}" font-size="15" font-weight="700" '
                   f'fill="{c["hr"]}">HackerRank</text>')
        y += 12
        out.append(f'<line x1="{pad}" y1="{y}" x2="{W - pad}" y2="{y}" stroke="{c["border"]}"/>')
        y += 16
        r = 20
        for i, (name, stars, solved) in enumerate(hr[:12]):
            col, row = i % cols, i // cols
            cx = pad + col * cw + cw / 2
            cy = y + r + row * 84
            pts = " ".join(
                f"{cx + r * math.cos(math.radians(-90 + 60 * k)):.1f},"
                f"{cy + r * math.sin(math.radians(-90 + 60 * k)):.1f}"
                for k in range(6))
            out.append(f'<polygon points="{pts}" fill="{c["hr"]}"/>')
            out.append(f'<text x="{cx:.1f}" y="{cy + 6:.1f}" font-size="17" font-weight="700" '
                       f'text-anchor="middle" fill="#ffffff">{esc(name[0].upper())}</text>')
            label = name if len(name) <= 13 else name[:12] + "…"
            out.append(f'<text x="{cx:.1f}" y="{cy + r + 15:.1f}" font-size="9.5" '
                       f'text-anchor="middle" fill="{c["muted"]}">{esc(label)}</text>')
            sx = cx - (stars * 10 - 2) / 2
            for s in range(stars):
                out.append(icon(ICON_STAR, sx + s * 10, cy + r + 21, 8, c["accent"]))
        rows = (min(len(hr), 12) + cols - 1) // cols
        y += r * 2 + (rows - 1) * 84 + 28

    H = round(y + pad - 6)
    return frame(W, H, c, "".join(out), "LeetCode and HackerRank badges")


def render_repo(repo, theme):
    c = THEMES[theme]
    W, H = 420, 132
    pad = 18
    out = []

    out.append(icon(ICON_REPO, pad, pad, 15, c["muted"]))
    out.append(
        f'<text x="{pad + 22}" y="{pad + 12}" font-size="14.5" font-weight="700" '
        f'fill="{c["title"]}">{esc(repo["name"])}</text>'
    )

    desc = repo.get("description") or "No description yet."
    for i, line in enumerate(wrap(desc, 11.5, W - 2 * pad, 3)):
        out.append(
            f'<text x="{pad}" y="{pad + 36 + i * 16}" font-size="11.5" '
            f'fill="{c["text"]}">{esc(line)}</text>'
        )

    fy = H - pad - 2
    x = pad
    if repo.get("language"):
        col = LANG_COLOR.get(repo["language"], c["muted"])
        out.append(f'<circle cx="{x + 5}" cy="{fy - 4}" r="5" fill="{col}"/>')
        out.append(
            f'<text x="{x + 15}" y="{fy}" font-size="11" fill="{c["muted"]}">'
            f'{esc(repo["language"])}</text>'
        )
        x += 15 + text_width(repo["language"], 11) + 18

    for path, count in ((ICON_STAR, repo.get("stars", 0)),
                        (ICON_FORK, repo.get("forks", 0))):
        out.append(icon(path, x, fy - 11, 12, c["muted"]))
        out.append(
            f'<text x="{x + 17}" y="{fy}" font-size="11" fill="{c["muted"]}">'
            f'{count}</text>'
        )
        x += 17 + text_width(str(count), 11) + 18

    return frame(W, H, c, "".join(out), f'{repo["name"]} repository card')


# --------------------------------------------------------------------------- #


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", required=True)
    p.add_argument("--out", type=Path, default=Path("assets"))
    p.add_argument("--projects", type=Path, default=Path("assets/projects.json"),
                   help="repos to render cards for, with description overrides")
    p.add_argument("--leetcode", metavar="USERNAME",
                   help="add LeetCode tiles to the stat card + badges to the coding card")
    p.add_argument("--hackerrank", metavar="USERNAME",
                   help="add HackerRank badges to the coding card")
    p.add_argument("--hackerrank-fallback", type=Path,
                   default=Path("assets/hackerrank.json"),
                   help="hand-edited badges used when the HackerRank fetch is blocked")
    p.add_argument("--coding-fallback", type=Path, default=Path("assets/coding.json"),
                   help="hand-edited LeetCode/HackerRank data used when fetching is blocked")
    args = p.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    args.out.mkdir(parents=True, exist_ok=True)

    user = rest(f"/users/{args.user}", token)
    repos = []
    page = 1
    while True:
        batch = rest(f"/users/{args.user}/repos?per_page=100&page={page}&type=owner", token)
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    owned = [r for r in repos if not r["fork"]]
    stars = sum(r["stargazers_count"] for r in owned)

    github_tiles = [(f"{stars:,}", "Total stars"),
                     (f"{user['public_repos']:,}", "Public repos"),
                     (f"{user['followers']:,}", "Followers")]

    contrib = fetch_contributions(args.user, token)
    if contrib:
        total, current, longest = contrib
        github_tiles += [(f"{total:,}", "Contributions (1y)"),
                          (f"{current:,}", "Current streak"),
                          (f"{longest:,}", "Longest streak")]
    else:
        print("  note: no usable token, skipping contribution tiles", file=sys.stderr)

    if args.projects.exists():
        shipped = len(json.loads(args.projects.read_text(encoding="utf-8"))["projects"])
        github_tiles.append((f"{shipped:,}", "Projects shipped"))

    sections = [("GitHub", github_tiles)]

    if args.leetcode:
        lc = fetch_leetcode(args.leetcode)
        if lc:
            solved, easy, medium, hard, rating = lc
            leetcode_tiles = [(f"{solved:,}", "Solved"),
                               (f"{easy:,}", "Easy"),
                               (f"{medium:,}", "Medium"),
                               (f"{hard:,}", "Hard")]
            if rating:
                leetcode_tiles.append((f"{rating:,}", "Contest rating"))
            sections.append(("LeetCode", leetcode_tiles))

    for theme in ("dark", "light"):
        dest = args.out / f"card-stats-{theme}.svg"
        dest.write_text(render_stats(args.user, sections, theme), encoding="utf-8")
    tile_count = sum(len(t) for _, t in sections)
    print(f"wrote card-stats-*.svg  ({len(sections)} sections, {tile_count} tiles)")

    # ---- coding badges card (LeetCode + HackerRank) ----
    lc_badges = fetch_leetcode_badges(args.leetcode) if args.leetcode else None
    hr_badges = (fetch_hackerrank(args.hackerrank, args.hackerrank_fallback)
                 if args.hackerrank else [])

    manual = {}
    if args.coding_fallback.exists():
        try:
            manual = json.loads(args.coding_fallback.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"  coding fallback unreadable ({e})", file=sys.stderr)
    if not lc_badges and manual.get("leetcode"):
        m = manual["leetcode"]
        lc_badges = {"contest": m.get("contest"),
                     "badges": [(b, None) for b in m.get("badges", [])]}
        print("  leetcode: using fallback file", file=sys.stderr)
    if not hr_badges and manual.get("hackerrank"):
        hr_badges = [(x["name"], min(int(x["stars"]), 5), int(x.get("solved", 0)))
                     for x in manual["hackerrank"]]
        print("  hackerrank: using fallback file", file=sys.stderr)

    if not (lc_badges or hr_badges):
        if (args.out / "card-coding-dark.svg").exists():
            print("  coding badges unavailable, keeping existing card", file=sys.stderr)
        else:  # placeholder so the README image never breaks
            lc_badges = {"contest": None, "badges": []}
    if lc_badges or hr_badges:
        for theme in ("dark", "light"):
            dest = args.out / f"card-coding-{theme}.svg"
            dest.write_text(render_coding(lc_badges, hr_badges, theme), encoding="utf-8")
        n_lc = len(lc_badges["badges"]) if lc_badges else 0
        print(f"wrote card-coding-*.svg  ({n_lc} leetcode badges, {len(hr_badges)} hackerrank badges)")

    if not args.projects.exists():
        print(f"no {args.projects}, skipping repo cards")
        return
    wanted = json.loads(args.projects.read_text(encoding="utf-8"))["projects"]
    by_name = {r["name"].lower(): r for r in repos}

    for entry in wanted:
        src = by_name.get(entry["repo"].lower())
        if not src:
            print(f"  !! {entry['repo']} not found on the account, skipped")
            continue
        slug = entry.get("display") or src["name"]
        card = {
            "name": slug,
            "description": entry.get("description") or src.get("description"),
            "language": entry.get("language") or src.get("language"),
            "stars": src["stargazers_count"],
            "forks": src["forks_count"],
        }
        for theme in ("dark", "light"):
            dest = args.out / f"card-{slug}-{theme}.svg"
            dest.write_text(render_repo(card, theme), encoding="utf-8")
        print(f"wrote card-{slug}-*.svg  "
              f"({card['stars']}star {card['forks']}fork {card['language']}, "
              f"repo: {src['name']})")


if __name__ == "__main__":
    main()
