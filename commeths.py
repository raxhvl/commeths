#!/usr/bin/env python3
"""commeths: track one GitHub user's merged Ethereum protocol PRs, quarter by quarter.

Run weekly (see .github/workflows/commeths.yml). It searches GitHub for the user's PRs
in the repos listed in config.json, writes the SVGs under assets/, and rewrites the part
of README.md between the commeths markers. Everything else in README.md is left alone.

Env:
  COMMETHS_USER  GitHub username to track (required)
  GITHUB_TOKEN   optional, raises the search rate limit

config.json:
  sections  {section: {short name: owner/repo}}
  recent    merged PRs listed per repo (default 3)
  quarters  quarters shown, current one included (default 4)
  since     optional "YYYY-MM-DD"; nothing before it is counted, open PRs included
"""

import datetime as dt, json, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")
START, END = "<!-- commeths:start -->", "<!-- commeths:end -->"
HOLD = 5  # seconds the filled chain stays up before the animation loops

# ---------- palette (Modern + Corners) ----------
K = dict(
    ground="#050505",
    acc="#3fd6c6",
    mag="#d78ac6",
    cyan="#7aa2f7",
    ink="#f2f2f2",
    body="#a6a9ad",
    muted="#8a8d91",
    plate="#0e0e0e",
    line="#222222",
    cline="#3a3d41",
)
SECTION_COLOURS = ["#e8c468", "#7aa2f7", "#d78ac6", "#9ccc65", "#ff9e64"]
MONO = "'IBM Plex Mono',SFMono-Regular,Menlo,monospace"
SANS = "Geist,'Helvetica Neue',Arial,sans-serif"


# ---------- data ----------
CAP = 1000  # GitHub search never returns more than 1000 results for one query


class TooMany(Exception):
    pass


def fetch(q, page):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "commeths"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    url = "https://api.github.com/search/issues?" + urllib.parse.urlencode(
        {"q": q, "per_page": 100, "page": page}
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers)
            ) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 3:  # search rate limit: wait it out
                time.sleep(61)
                continue
            if (
                e.code == 422
            ):  # e.g. a repo in config.json that doesn't exist or was renamed
                sys.exit(f"GitHub rejected the search {q!r}: {e.read().decode()}")
            raise
        if (
            data.get("incomplete_results") and attempt < 3
        ):  # search timed out server side
            time.sleep(5)
            continue
        if data.get("incomplete_results"):
            sys.exit(
                f"GitHub keeps returning incomplete results for {q!r}; refusing to write partial counts."
            )
        return data


def search(q):
    """Every item for a GitHub issue search. Fails loudly rather than return a partial list."""
    for attempt in range(2):
        first = fetch(q, 1)
        total = first["total_count"]
        if total > CAP:
            raise TooMany(q)
        seen = {it["id"]: it for it in first["items"]}
        page = 1
        while len(seen) < total and page < CAP // 100:
            page += 1
            data = fetch(q, page)
            if not data["items"]:
                break
            seen.update((it["id"], it) for it in data["items"])
        if (
            len(seen) == total
        ):  # results can shift while paging; only trust an exact match
            return list(seen.values())
    sys.exit(
        f"Search {q!r} returned {len(seen)} of {total} results twice; try again later."
    )


def search_merged(base, a, b):
    """Merged PRs in [a, b], splitting the date range when one query would pass GitHub's cap."""
    try:
        return search(f"{base} is:merged merged:{a}..{b}")
    except TooMany:
        if a == b:
            sys.exit(
                f"More than {CAP} PRs merged on {a}; GitHub search can't list them all."
            )
        mid = a + (b - a) // 2
        return search_merged(base, a, mid) + search_merged(
            base, mid + dt.timedelta(days=1), b
        )


def quarter_of(day):
    q = (day.month - 1) // 3
    start = dt.date(day.year, q * 3 + 1, 1)
    end = (dt.date(day.year + (q == 3), (q * 3 + 3) % 12 + 1, 1)) - dt.timedelta(days=1)
    return start, end, f"{day.year} Q{q + 1}"


def prev_quarter(start):
    return quarter_of(start - dt.timedelta(days=1))


def repo_of(item):
    return item["repository_url"].split("/repos/", 1)[1]


def search_url(q):
    return "https://github.com/search?" + urllib.parse.urlencode(
        {"q": q, "type": "pullrequests"}
    )


# ---------- svg pieces ----------
DEFS = ""


def esc(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def frame(W, H, body):
    L = 14
    return (
        f'<rect width="{W}" height="{H}" fill="{K["ground"]}"/>'
        f'<path d="M10,{10 + L} V10 H{10 + L} M{W - 10 - L},{H - 10} H{W - 10} V{H - 10 - L}" fill="none" stroke="{K["acc"]}" stroke-width="2"/>'
        + body
    )


def fringe(x, y, txt, cls, extra=""):
    return (
        f'<g aria-hidden="true"><text class="{cls}" x="{x - 2}" y="{y}" style="fill:{K["cyan"]}" {extra}>{txt}</text>'
        f'<text class="{cls}" x="{x + 2}" y="{y}" style="fill:{K["mag"]}" {extra}>{txt}</text></g>'
        f'<text class="{cls}" x="{x}" y="{y}" {extra}>{txt}</text>'
    )


def svg(W, H, label, style, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(label)}">'
        f"<style>{style}</style>{frame(W, H, body)}</svg>"
    )


def eth_cutout(x, y, size, fill):
    """Filled square with the line-art Ethereum glyph cut out of it."""
    h = size * 0.84
    sc = h / 1213
    gx = x + size / 2 - 400 * sc
    gy = y + (size - h) / 2
    P = lambda pts: " ".join(f"{gx + px * sc:.2f},{gy + py * sc:.2f}" for px, py in pts)
    lines = [
        [(400, 8), (8, 624), (400, 848), (792, 624), (400, 8)],
        [(400, 8), (400, 848)],
        [(8, 624), (400, 450), (792, 624)],
        [(8, 684), (400, 910), (792, 684), (400, 1205), (8, 684)],
        [(400, 910), (400, 1205)],
    ]
    cut = "".join(
        f'<polyline points="{P(l)}" fill="none" stroke="#000" stroke-width="1.25" stroke-linejoin="round"/>'
        for l in lines
    )
    return (
        f'<mask id="ethcut" maskUnits="userSpaceOnUse" x="{x}" y="{y}" width="{size}" height="{size}">'
        f'<rect x="{x}" y="{y}" width="{size}" height="{size}" fill="#fff"/>{cut}</mask>'
        f'<rect x="{x}" y="{y}" width="{size}" height="{size}" fill="{fill}" mask="url(#ethcut)"/>'
    )


def quote_svg():
    W, H, X0, Y1, Y2, CW, cyc = 760, 230, 52, 84, 146, 24.0, 4.0
    style = f""".l{{font:400 27px {SANS};fill:{K["body"]};letter-spacing:-.01em}} .w{{font:600 40px {MONO};fill:{K["ink"]}}}
.m{{font:11px {MONO};fill:{K["muted"]};letter-spacing:.12em}} .a{{font:12px {MONO};fill:{K["acc"]};letter-spacing:.12em;text-anchor:end}}
.A{{animation:A {cyc}s infinite steps(1,end)}} .B{{animation:B {cyc}s infinite steps(1,end)}}
.cy{{animation:cy {cyc}s infinite steps(1,end)}} .mg{{animation:mg {cyc}s infinite steps(1,end)}} .tail{{animation:tail {cyc}s infinite steps(1,end)}}
@keyframes A{{0%,44%{{opacity:1;transform:none}}45%{{opacity:0}}46%{{opacity:1;transform:translateX(3px) skewX(-8deg)}}47%{{opacity:0}}48%{{opacity:1;transform:translateX(-2px)}}49%,94%{{opacity:0}}95%{{opacity:1;transform:translateX(-3px)}}96%{{opacity:0}}97%{{opacity:1;transform:skewX(6deg)}}98%{{opacity:0}}99%,100%{{opacity:1;transform:none}}}}
@keyframes B{{0%,44%{{opacity:0}}45%{{opacity:1;transform:translateX(-4px)}}46%{{opacity:0}}47%{{opacity:1;transform:skewX(8deg)}}48%{{opacity:0}}49%,94%{{opacity:1;transform:none}}95%{{opacity:0}}96%{{opacity:1;transform:translateX(4px) skewX(-6deg)}}97%,100%{{opacity:0}}}}
@keyframes cy{{0%,44%{{transform:none}}45%{{transform:translateX(-9px)}}46%{{transform:translateX(5px)}}47%{{transform:translateX(-6px)}}48%,94%{{transform:none}}95%{{transform:translateX(-8px)}}96%{{transform:translateX(6px)}}97%,100%{{transform:none}}}}
@keyframes mg{{0%,44%{{transform:none}}45%{{transform:translateX(8px)}}46%{{transform:translateX(-5px)}}47%{{transform:translateX(7px)}}48%,94%{{transform:none}}95%{{transform:translateX(9px)}}96%{{transform:translateX(-4px)}}97%,100%{{transform:none}}}}
@keyframes tail{{0%,46%{{transform:translateX(-{CW}px)}}47%,96%{{transform:none}}97%,100%{{transform:translateX(-{CW}px)}}}}
@media (prefers-reduced-motion:reduce){{.A,.cy,.mg,.tail{{animation:none!important}}.A{{opacity:0}}.tail{{transform:none}}.B{{animation:none!important;opacity:1}}}}"""

    def word(txt, cls):
        tl = f'textLength="{len(txt) * CW}"'
        main = txt.replace("eth", f'<tspan style="fill:{K["acc"]}">eth</tspan>')
        return (
            f'<g class="{cls}"><g aria-hidden="true"><text class="w cy" x="{X0 - 2}" y="{Y2}" {tl} style="fill:{K["cyan"]}">{txt}</text>'
            f'<text class="w mg" x="{X0 + 2}" y="{Y2}" {tl} style="fill:{K["mag"]}">{txt}</text></g>'
            f'<text class="w" x="{X0}" y="{Y2}" {tl}>{main}</text></g>'
        )

    ox, oy = W - 150, 40
    obj = (
        f'<rect x="{ox}" y="{oy}" width="100" height="100" fill="none" stroke="{K["line"]}"/>'
        f'<rect x="{ox + 16}" y="{oy + 16}" width="68" height="68" fill="none" stroke="{K["cline"]}"/>'
        + eth_cutout(ox + 24, oy + 24, 52, K["acc"])
        + "".join(
            f'<rect x="{ox + k * 7}" y="{oy + 110}" width="{(1, 3, 2, 1, 4, 1, 2)[k % 7]}" height="12" fill="{K["cline"]}"/>'
            for k in range(14)
        )
    )
    tailx = X0 + 8 * CW + 12
    body = (
        f'<text class="m" x="{X0}" y="38">&gt; ETHEREUM PROTOCOL CONTRIBUTIONS</text>'
        f'<text class="l" x="{X0}" y="{Y1}">“The fork ith alwayth thix monthth away.</text>'
        f"{word('commits', 'A')}{word('commeths', 'B')}"
        f'<text class="l tail" x="{tailx}" y="{Y2}">are not.”</text>'
        f'<text class="a" x="{tailx + 108}" y="{Y2 + 38}">— MIKE TYTHON, PROBABLY</text>'
        f'<g aria-hidden="true">{obj}</g>'
    )
    return svg(
        W,
        H,
        "The fork is always six months away. Commeths are not. Mike Tython, probably.",
        style,
        body,
    )


def chain_svg(starts, qlabel, cur, first, weeks, pending, colours):
    """starts: first day of each block (Monday weeks, clipped to the quarter). cur: block holding today.
    first: first block inside `since`. weeks: per block, section names (one per merged PR).
    pending: section names of open PRs."""
    WEEKS = len(starts)
    B, G, X0, Y0 = 72, 16, 40, 104
    W = X0 + WEEKS * (B + G) - G + 32
    H = Y0 + B + 140
    cx = lambda i: X0 + i * (B + G) + B / 2
    clip = lambda x, y, w, h, c=8: (
        f"M{x},{y} H{x + w - c} L{x + w},{y + c} V{y + h} H{x + c} L{x},{y + h - c} Z"
    )
    out, css = [], []
    t0 = lambda i: 0.3 + (i - first) * 0.4  # block i starts fading in
    filled = max(  # when the last tx lands
        t0(i) + 0.45 + 0.07 * max(min(len(weeks[i]), 12) - 1, 0) + 0.2
        for i in range(first, cur + 1)
    )
    CYC = round(filled + HOLD + 0.8, 2)  # then hold for reading, fade out, loop
    pct = lambda t: f"{t / CYC * 100:.2f}"
    pop = lambda name, t, fade: css.append(
        f"@keyframes {name}{{0%,{pct(t)}%{{opacity:0}}{pct(t + fade)}%,{pct(CYC - 0.8)}%{{opacity:1}}100%{{opacity:0}}}}"
    )
    anim = lambda name: f'style="animation:{name} {CYC}s ease-out infinite"'
    for m in sorted(
        {d.month for d in starts}, key=lambda m: (m - starts[0].month) % 12
    ):
        idx = [i for i, d in enumerate(starts) if d.month == m]
        a, b = X0 + idx[0] * (B + G), X0 + idx[-1] * (B + G) + B
        out.append(
            f'<text class="mo" x="{a}" y="{Y0 - 27}">// {starts[idx[0]].strftime("%b").upper()}</text>'
            f'<line stroke="{K["line"]}" x1="{a}" y1="{Y0 - 18}" x2="{b}" y2="{Y0 - 18}"/>'
        )
    for i, wk in enumerate(starts):
        x = X0 + i * (B + G)
        live = first <= i <= cur
        lab = f'<text class="lb" x="{cx(i)}" y="{Y0 + B + 22}" opacity="{1 if live else 0.5}">{wk.strftime("%m.%d")}</text>'
        link = ""
        if i > 0:
            dash = "" if live and i > first else 'stroke-dasharray="2 3"'
            link = f'<line x1="{x - G}" y1="{Y0 + B / 2}" x2="{x}" y2="{Y0 + B / 2}" stroke="{K["cline"] if live else K["line"]}" stroke-width="1.5" {dash}/>'
        if not live:  # upcoming, or before `since`
            out.append(
                f'{link}<path d="{clip(x + 0.5, Y0 + 0.5, B - 1, B - 1)}" fill="none" stroke="{K["line"]}" stroke-dasharray="2 3"/>{lab}'
            )
            continue
        prs = weeks[i]
        pop(f"b{i}", t0(i), 0.5)
        focus = (  # the slot being proposed: dashed accent outline that breathes, plus its tag
            f'<path class="cur" d="{clip(x + 0.5, Y0 + 0.5, B - 1, B - 1)}" fill="none"/>'
            f'<rect x="{cx(i) - 38}" y="{Y0 + B + 34}" width="6" height="9" fill="{K["acc"]}"/><text class="tag" x="{cx(i) - 28}" y="{Y0 + B + 43}">PROPOSING</text>'
            if i == cur
            else ""
        )
        out.append(
            f'<g {anim(f"b{i}")}>{link}<path d="{clip(x + 0.5, Y0 + 0.5, B - 1, B - 1)}" fill="{K["plate"]}" stroke="{K["cline"]}"/>{focus}'
            f'<text class="n" x="{cx(i)}" y="{Y0 + 28}" style="fill:{K["ink"] if prs else K["line"]}">{len(prs):02d}</text></g>'
        )
        shown = prs[:12]  # the count above stays exact; the block holds at most 3 rows
        Sz, P, cols = 9, 3, 4
        rows = (len(shown) + cols - 1) // cols
        for j, s in enumerate(shown):
            r, c = divmod(j, cols)
            nr = min(cols, len(shown) - r * cols)
            px = cx(i) - (nr * Sz + (nr - 1) * P) / 2 + c * (Sz + P)
            py = Y0 + B - 11 - Sz - (rows - 1 - r) * (Sz + P)
            pop(f"t{i}_{j}", t0(i) + 0.45 + j * 0.07, 0.2)
            out.append(
                f'<rect x="{px:.1f}" y="{py:.1f}" width="{Sz}" height="{Sz}" fill="{colours[s]}" {anim(f"t{i}_{j}")}/>'
            )
        out.append(lab)
    if pending:
        label = f"MEMPOOL (OPEN PRS) · {len(pending):02d}"
        shown = pending[:16]
        Sz, P = 9, 4
        lw = len(label) * 7.7 + 22
        tw = lw + len(shown) * (Sz + P) + 10
        xb = X0 + cur * (B + G)
        tx = min(xb - G, W - 32 - tw)  # under the proposing block
        ty = Y0 + B + 58
        # a faint line with pulses flowing from the mempool up the gap into the proposing block
        flow = f"M{xb - G / 2},{ty} V{Y0 + B - 14} H{xb}"
        out.append(
            f"<g {anim(f'b{cur}')}>"
            f'<path d="{flow}" fill="none" stroke="{K["line"]}" stroke-width="1.5"/>'
            f'<path class="flow" d="{flow}" fill="none" stroke="{K["acc"]}" stroke-width="1.5"/>'
            f'<rect x="{tx + 0.5}" y="{ty + 0.5}" width="{tw - 1}" height="22" fill="none" stroke="{K["cline"]}" stroke-dasharray="3 3"/>'
            f'<text x="{tx + 12}" y="{ty + 15}" style="fill:{K["muted"]};letter-spacing:.1em">{label}</text>'
            + "".join(
                f'<rect x="{tx + lw + j * (Sz + P) + 0.5}" y="{ty + 7.5}" width="{Sz - 1}" height="{Sz - 1}" fill="none" stroke="{colours[s]}" stroke-width="1.5"/>'
                for j, s in enumerate(shown)
            )
            + "</g>"
        )
    ly, lx, leg = H - 22, X0, []
    for name, col in colours.items():
        sw = f'<rect x="{lx}" y="{ly - 9}" width="9" height="9" fill="{col}"/>'
        leg.append(
            sw + f'<text class="lg" x="{lx + 16}" y="{ly}">{esc(name.upper())}</text>'
        )
        lx += 16 + len(name) * 7.2 + 30
    total = sum(map(len, weeks))
    title = f"{total} merged PRs"
    body = (
        fringe(X0, 48, title, "h")
        + f'<text class="sub" x="{X0 + len(title) * 18 + 28}" y="47">{qlabel.replace(" ", ".")} // SLOT {cur + 1:02d}/{WEEKS}</text>'
        + "".join(out)
        + f'<line stroke="{K["line"]}" x1="{X0}" y1="{ly - 26}" x2="{W - 32}" y2="{ly - 26}"/>'
        + "".join(leg)
    )
    style = (
        f"text{{font-family:{MONO};fill:{K['muted']};font-size:11px}}"
        f".h{{font-size:30px;font-weight:600;fill:{K['ink']};letter-spacing:-.01em}}"
        f".sub{{font-size:12px;fill:{K['body']};letter-spacing:.06em}} .mo{{letter-spacing:.1em}}"
        f".lb{{text-anchor:middle}} .n{{font-size:20px;font-weight:500;text-anchor:middle}} .tag{{fill:{K['acc']};letter-spacing:.1em}} .lg{{letter-spacing:.08em}}"
        + f".cur{{stroke:{K['acc']};stroke-width:1.5;stroke-dasharray:5 4;animation:pulse 1.6s ease-in-out infinite}}"
        + "@keyframes pulse{50%{stroke-opacity:.25}}"
        + ".flow{stroke-dasharray:4 20;stroke-opacity:.7;animation:flow 1.2s linear infinite}"
        + "@keyframes flow{from{stroke-dashoffset:24}to{stroke-dashoffset:0}}"
        + "".join(css)
        + "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
    )
    return svg(
        W,
        H,
        f"{qlabel}: {total} merged PRs, slot {cur + 1} of {WEEKS}, {len(pending)} open",
        style,
        body,
    )


def heading_svg(label, count, index, colour):
    W, H, X0 = 1100, 60, 24
    sw = (
        f'<rect x="{X0}" y="22" width="14" height="14" fill="{colour}"/>'
        if colour
        else f'<rect x="{X0 + 0.5}" y="22.5" width="13" height="13" fill="none" stroke="{K["cline"]}"/>'
    )
    body = (
        sw
        + f'<text class="m" x="{X0 + 26}" y="35">{index:02d}</text>'
        + fringe(X0 + 56, 39, esc(label), "h")
        + f'<line x1="{X0 + 70 + len(label) * 15}" y1="30.5" x2="{W - 80}" y2="30.5" stroke="{K["line"]}"/>'
        + (
            f'<text class="c" x="{W - 24}" y="38">{count:02d}</text>'
            if count is not None
            else ""
        )
    )
    style = (
        f".h{{font:600 24px {MONO};fill:{K['ink']};letter-spacing:.02em}} .m{{font:12px {MONO};fill:{K['muted']}}}"
        f".c{{font:500 22px {MONO};fill:{K['acc']};text-anchor:end}}"
    )
    return svg(
        W, H, label if count is None else f"{label}: {count} merged", style, body
    )


# ---------- markdown ----------
def md_title(t):
    return (
        t.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def short_date(iso):
    d = dt.date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}"


def main():
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    user = os.environ.get("COMMETHS_USER") or cfg.get("user")
    if not user:
        sys.exit(
            "Set COMMETHS_USER (a repository variable in the workflow) to the GitHub username to track."
        )
    recent, quarters = cfg.get("recent", 3), cfg.get("quarters", 4)
    sections = cfg["sections"]  # {section: {short name: owner/repo}}
    colours = {
        s: SECTION_COLOURS[i % len(SECTION_COLOURS)] for i, s in enumerate(sections)
    }
    section_of = {
        repo.lower(): s for s, repos in sections.items() for repo in repos.values()
    }
    short_of = {
        repo.lower(): name
        for repos in sections.values()
        for name, repo in repos.items()
    }
    all_repos = [r for repos in sections.values() for r in repos.values()]
    repo_q = " ".join(f"repo:{r}" for r in all_repos)
    base = f"is:pr author:{user} {repo_q}"

    def sec_of(it):
        repo = repo_of(it).lower()
        if (
            repo not in section_of
        ):  # GitHub answered with a name config.json doesn't use
            sys.exit(
                f"{repo_of(it)} isn't in config.json (renamed or moved?). Update it and rerun."
            )
        return section_of[repo]

    today = dt.datetime.now(dt.timezone.utc).date()  # GitHub's merged_at is UTC too
    qs, qe, qlabel = quarter_of(today)
    since = dt.date.fromisoformat(cfg["since"]) if cfg.get("since") else dt.date.min
    if since > today:
        sys.exit(f"since ({since}) is in the future.")
    a = max(qs, since)  # counting starts here this quarter
    merged = search_merged(base, a, qe) if a <= qe else []
    pending = search(
        f"{base} is:open" + (f" created:>={since}" if cfg.get("since") else "")
    )
    for it in merged:
        it["_merged"] = it["pull_request"]["merged_at"]
    merged.sort(key=lambda it: it["_merged"], reverse=True)
    pending.sort(key=lambda it: it["created_at"], reverse=True)

    # one block per calendar week (Mon-Sun), clipped to the quarter, so the last block always holds today
    monday = qs - dt.timedelta(days=qs.weekday())
    starts = [qs] + [
        monday + dt.timedelta(weeks=k) for k in range(1, (qe - monday).days // 7 + 1)
    ]
    block = lambda day: max(i for i, d in enumerate(starts) if d <= day)
    cur, first = block(today), block(a)
    weeks = [[] for _ in starts]
    for it in merged:
        weeks[block(dt.date.fromisoformat(it["_merged"][:10]))].append(sec_of(it))
    order = list(sections)
    for w in weeks:
        w.sort(key=order.index)
    pend_secs = sorted(map(sec_of, pending), key=order.index)

    past = []
    s = qs
    for _ in range(quarters - 1):
        ps, pe, pl = prev_quarter(s)
        s = ps
        if pe < since:
            break
        clipped, ps = since > ps, max(ps, since)
        counts = {sec: 0 for sec in sections}
        for it in search_merged(base, ps, pe):
            counts[sec_of(it)] += 1
        past.append(
            (
                pl + (f" <sub>from {short_date(str(ps))}</sub>" if clipped else ""),
                ps,
                pe,
                counts,
            )
        )
    if not any(sum(c.values()) for *_, c in past):
        past = []  # nothing merged before this quarter (within since): skip the table

    os.makedirs(ASSETS, exist_ok=True)
    write = lambda name, text: open(os.path.join(ASSETS, name), "w").write(text)
    write("quote.svg", quote_svg())
    write("chain.svg", chain_svg(starts, qlabel, cur, first, weeks, pend_secs, colours))

    md = [
        f'<img src="assets/chain.svg" alt="{qlabel}: {len(merged)} merged PRs, one block per week" width="100%">',
        "",
    ]
    for n, (sec, repos) in enumerate(sections.items(), 1):
        sec_merged = [it for it in merged if sec_of(it) == sec]
        sec_open = [it for it in pending if sec_of(it) == sec]
        if not sec_merged and not sec_open:
            continue
        slug = "h-" + "".join(ch for ch in sec.lower() if ch.isalnum()) + ".svg"
        write(slug, heading_svg(sec, len(sec_merged), n, colours[sec]))
        md += [
            f'<img src="assets/{slug}" alt="{sec} · {len(sec_merged)} merged" width="100%">',
            "",
        ]
        for name, repo in repos.items():
            rows = [it for it in sec_merged if repo_of(it).lower() == repo.lower()]
            if not rows:
                continue
            owner = repo.split("/")[0]
            md.append(
                f'<img src="https://github.com/{owner}.png" width="16"> **[{name}](https://github.com/{repo})** · {len(rows)}'
            )
            lines = [
                f"> [{md_title(it['title'])}]({it['html_url']}) <sub>{short_date(it['_merged'])}</sub>"
                for it in rows[:recent]
            ]
            if len(rows) > recent:
                more = search_url(
                    f"is:pr is:merged author:{user} repo:{repo} merged:{a}..{qe}"
                )
                lines.append(
                    f"> <sub>[+{len(rows) - recent} more this quarter →]({more})</sub>"
                )
            md += ["<br>\n".join(lines), ""]
        if sec_open:
            md += ["<sub>**Mempool (open PRs)**</sub>", ""]
            lines = []
            for it in sec_open:
                age = (today - dt.date.fromisoformat(it["created_at"][:10])).days
                lines.append(
                    f"> ○ [{md_title(it['title'])}]({it['html_url']}) <sub>{short_of[repo_of(it).lower()]} · open {age}d</sub>"
                )
            md += ["<br>\n".join(lines), ""]

    if past:
        write("h-past.svg", heading_svg("Past quarters", None, len(sections) + 1, None))
        md += [
            '<img src="assets/h-past.svg" alt="Past quarters" width="100%">',
            "",
            "| Quarter | " + " | ".join(sections) + " | Total |",
            "|---|" + "--:|" * (len(sections) + 1),
        ]
        for pl, ps, pe, counts in past:
            link = search_url(f"{base} is:merged merged:{ps}..{pe}")
            cells = " | ".join(
                str(counts[sec]) if counts[sec] else "–" for sec in sections
            )
            md.append(f"| [**{pl}**]({link}) | {cells} | **{sum(counts.values())}** |")
        md.append("")
    since_q = f" merged:>={since}" if cfg.get("since") else ""
    since_note = f" since {since}" if cfg.get("since") else ""
    md += [
        '<div align="center">',
        "",
        f"**[All contributions{since_note} →]({search_url(base + ' is:merged' + since_q)})**",
        "",
        f"<sub>Updated {today} · counts merged PRs{since_note} only</sub>",
        "",
        "</div>",
    ]

    path = os.path.join(ROOT, "README.md")
    readme = open(path).read() if os.path.exists(path) else f"{START}\n{END}\n"
    if START not in readme or END not in readme:
        readme += f"\n{START}\n{END}\n"
    head, rest = readme.split(START, 1)
    tail = rest.split(END, 1)[1]
    open(path, "w").write(head + START + "\n" + "\n".join(md) + "\n" + END + tail)
    print(f"{user}: {len(merged)} merged in {qlabel}, {len(pending)} open")


if __name__ == "__main__":
    main()
