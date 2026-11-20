#!/usr/bin/env python3
"""Fetch GitHub stats for a user and render a self-hosted terminal metrics SVG.

Zero third-party dependencies (Python 3 standard library only).
Works both inside GitHub Actions (with GITHUB_TOKEN) and locally without a token.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

LANG_COLORS: dict[str, str] = {
    "Rust": "#dea584",
    "Go": "#00add8",
    "Swift": "#f05138",
    "TypeScript": "#58a6ff",
    "Python": "#ffde57",
    "JavaScript": "#f1e05a",
    "Vue": "#41b883",
    "Shell": "#3fb950",
    "C": "#a8b9cc",
    "C++": "#f34b7d",
    "HTML": "#e34c26",
    "CSS": "#bb9af3",
    "Dockerfile": "#38bdf8",
    "Makefile": "#427819",
}


def http_get(url: str, token: str | None = None, accept: str = "application/vnd.github+json") -> bytes:
    headers = {
        "User-Agent": "Aho1ic-Profile-Metrics-Generator/1.0",
        "Accept": accept,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def fetch_graphql_contributions(username: str, token: str) -> int | None:
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"login": username}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "User-Agent": "Aho1ic-Profile-Metrics-Generator/1.0",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return int(
            data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]
        )
    except Exception:
        return None


def fetch_public_contributions(username: str) -> int | None:
    url = f"https://github.com/users/{username}/contributions"
    try:
        html = http_get(url, accept="text/html").decode("utf-8", errors="replace")
        m = re.search(r"([\d,]+)\s+contributions\s+in\s+the\s+last\s+year", html)
        if m:
            return int(m.group(1).replace(",", ""))
    except Exception:
        return None
    return None


def collect_metrics(username: str, token: str | None) -> dict:
    user = json.loads(http_get(f"https://api.github.com/users/{username}", token=token).decode("utf-8"))
    repos = json.loads(
        http_get(
            f"https://api.github.com/users/{username}/repos?per_page=100&type=owner&sort=updated",
            token=token,
        ).decode("utf-8")
    )

    non_fork_repos = [r for r in repos if not r.get("fork")]
    total_stars = sum(int(r.get("stargazers_count", 0)) for r in non_fork_repos)
    total_forks = sum(int(r.get("forks_count", 0)) for r in non_fork_repos)
    public_repos = int(user.get("public_repos", len(non_fork_repos)))
    followers = int(user.get("followers", 0))

    lang_bytes: dict[str, int] = {}
    for repo in non_fork_repos:
        if repo.get("name", "").lower() == username.lower():
            continue
        langs_url = repo.get("languages_url")
        if not langs_url:
            continue
        try:
            repo_langs = json.loads(http_get(langs_url, token=token).decode("utf-8"))
            for lang, count in repo_langs.items():
                lang_bytes[lang] = lang_bytes.get(lang, 0) + int(count)
        except Exception:
            primary = repo.get("language")
            if primary:
                lang_bytes[primary] = lang_bytes.get(primary, 0) + 1

    total_bytes = sum(lang_bytes.values()) or 1
    top_langs = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_langs_pct = [
        (lang, round(count * 100.0 / total_bytes, 1))
        for lang, count in top_langs
    ]

    contributions = None
    if token:
        contributions = fetch_graphql_contributions(username, token)
    if contributions is None:
        contributions = fetch_public_contributions(username)

    return {
        "username": username,
        "contributions": contributions,
        "total_stars": total_stars,
        "total_forks": total_forks,
        "public_repos": public_repos,
        "followers": followers,
        "top_langs": top_langs_pct,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d UTC"),
    }


def format_number(val: int | None) -> str:
    if val is None:
        return "N/A"
    return f"{val:,}"


def render_svg(data: dict) -> str:
    username = escape(data["username"])
    updated_at = escape(data["updated_at"])

    cards = [
        ("Yearly Contribs", format_number(data["contributions"]), "#58a6ff"),
        ("Total Stars", format_number(data["total_stars"]), "#e0af68"),
        ("Public Repos", format_number(data["public_repos"]), "#3fb950"),
        ("Forks / Fans", f"{data['total_forks']} / {data['followers']}", "#bb9af3"),
    ]

    card_blocks: list[str] = []
    card_w = 190
    card_gap = 16
    start_x = 26
    for idx, (label, value, color) in enumerate(cards):
        x = start_x + idx * (card_w + card_gap)
        card_blocks.append(
            f'  <g transform="translate({x}, 86)">\n'
            f'    <rect width="{card_w}" height="64" rx="10" fill="#161b22" stroke="#293240" />\n'
            f'    <text x="16" y="30" fill="{color}" font-size="21" font-weight="700" class="mono">{escape(value)}</text>\n'
            f'    <text x="16" y="50" fill="#8b949e" font-size="11.5" class="mono">{escape(label)}</text>\n'
            f'  </g>'
        )

    bar_blocks: list[str] = []
    bar_x = 154
    bar_max_w = 602
    base_y = 200
    row_step = 28

    top_langs = data["top_langs"] or [("Rust", 50.0), ("Go", 35.0), ("Swift", 15.0)]
    for idx, (lang, pct) in enumerate(top_langs):
        y = base_y + idx * row_step
        fill_w = max(8.0, round(bar_max_w * min(pct, 100.0) / 100.0, 1))
        color = LANG_COLORS.get(lang, "#58a6ff")
        bar_blocks.append(
            f'  <g transform="translate(26, {y})">\n'
            f'    <circle cx="5" cy="-4" r="4" fill="{color}" />\n'
            f'    <text x="18" y="0" fill="#c9d1d9" font-size="13" class="mono">{escape(lang)}</text>\n'
            f'    <rect x="{bar_x - 26}" y="-11" width="{bar_max_w}" height="12" rx="6" fill="#21262d" />\n'
            f'    <rect x="{bar_x - 26}" y="-11" width="{fill_w}" height="12" rx="6" fill="{color}" />\n'
            f'    <text x="808" y="0" fill="#8b949e" font-size="12.5" text-anchor="end" class="mono">{pct:.1f}%</text>\n'
            f'  </g>'
        )

    height = base_y + len(top_langs) * row_step + 14

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 860 {height}" width="860" height="{height}" role="img" aria-label="{username} GitHub Terminal Metrics">
  <defs>
    <style>
      .mono {{
        font-family: 'PingFang SC', 'PingFang TC', 'JetBrains Mono', ui-monospace, 'SFMono-Regular', Menlo, Consolas, sans-serif;
      }}
    </style>
  </defs>

  <rect x="0.5" y="0.5" width="859" height="{height - 1}" rx="12" fill="#0d1117" stroke="#30363d" />

  <path d="M1,12 Q1,1 12,1 L848,1 Q859,1 859,12 L859,40 L1,40 Z" fill="#161b22" />
  <line x1="1" y1="40.5" x2="859" y2="40.5" stroke="#30363d" />
  <circle cx="24" cy="21" r="6" fill="#ff5f56" />
  <circle cx="44" cy="21" r="6" fill="#ffbd2e" />
  <circle cx="64" cy="21" r="6" fill="#27c93f" />
  <text x="430" y="25" fill="#8b949e" font-size="12.5" text-anchor="middle" class="mono">{username.lower()}@github: ~/metrics — gh-stat</text>
  <text x="836" y="25" fill="#6e7681" font-size="11" text-anchor="end" class="mono">{updated_at}</text>

  <text x="26" y="69" font-size="13.5" class="mono">
    <tspan fill="#3fb950" font-weight="700">$</tspan>
    <tspan fill="#e6edf3" dx="8">gh-stat --user {username} --lang-top 5</tspan>
    <tspan fill="#6e7681" dx="12" font-size="12"># auto-updated via GitHub Actions</tspan>
  </text>

{chr(10).join(card_blocks)}

  <text x="26" y="176" font-size="12.5" class="mono">
    <tspan fill="#58a6ff">❯</tspan>
    <tspan fill="#8b949e" dx="6">Top Languages (by public repository bytes)</tspan>
  </text>

{chr(10).join(bar_blocks)}
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate self-hosted GitHub terminal metrics SVG.")
    parser.add_argument("--user", default="Aho1ic", help="GitHub username")
    parser.add_argument("--output", default="assets/metrics-terminal.svg", help="Output SVG file path")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    data = collect_metrics(args.user, token)
    svg = render_svg(data)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    print(f"Generated {out_path} for {args.user}: contribs={data['contributions']}, stars={data['total_stars']}, repos={data['public_repos']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
