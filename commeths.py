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

import datetime as dt, hashlib, json, random, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")
START, END = "<!-- commeths:start -->", "<!-- commeths:end -->"
SHOW = 5  # weeks in view: three before this week, this week, and the next one
READ = 6  # seconds the chain holds on this week before it loops
EASE = "cubic-bezier(.65,0,.35,1)"  # ease-in-out cubic, for the roll to this week

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


def svg(W, H, label, style, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(label)}">'
        f"<style>{style}</style>{frame(W, H, body)}</svg>"
    )


def eth_cutout(x, y, size, fill, cyc):
    """Filled square with the line-art Ethereum glyph cut out of it, plus a short glitch burst
    once per quote cycle, lined up with the commits/commeths swap: the square splits into
    cyan and magenta ghosts, tears into sideways-shifted slices, flickers to a wireframe,
    throws off a few noise specks, then settles. Returns (markup, css)."""
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
    glyph = lambda colour: "".join(
        f'<polyline points="{P(l)}" fill="none" stroke="{colour}" stroke-width="1.25" stroke-linejoin="round"/>'
        for l in lines
    )
    sq = lambda colour, cls="": (
        f'<rect class="{cls}" x="{x}" y="{y}" width="{size}" height="{size}" fill="{colour}" mask="url(#ethcut)"/>'
    )
    bands = 5
    bh = size / bands
    rng = random.Random(7)  # fixed seed: the glitch looks the same every run
    out = [
        f'<mask id="ethcut" maskUnits="userSpaceOnUse" x="{x - 12}" y="{y - 12}" width="{size + 24}" height="{size + 24}">'
        f'<rect x="{x - 12}" y="{y - 12}" width="{size + 24}" height="{size + 24}" fill="#fff"/>{glyph("#000")}</mask>'
    ]
    out += [
        f'<clipPath id="eb{k}"><rect x="{x - 12}" y="{y + k * bh:.2f}" width="{size + 24}" height="{bh:.2f}"/></clipPath>'
        for k in range(bands)
    ]
    wire = lambda colour, cls: (
        f'<g class="{cls}"><rect x="{x + 0.5}" y="{y + 0.5}" width="{size - 1}" height="{size - 1}" fill="none" stroke="{colour}"/>{glyph(colour)}</g>'
    )
    out.append(
        wire(K["cyan"], "eg ec") + wire(K["mag"], "eg em")
    )  # colour-split outlines, behind
    out.append(
        '<g class="ef">'
        + "".join(
            f'<g clip-path="url(#eb{k})">{sq(fill, f"es{k}")}</g>' for k in range(bands)
        )
        + "</g>"
    )
    out.append(wire(fill, "eg eo"))  # the wireframe it flickers to
    specks = []
    for k in range(7):
        sx_ = x - 14 + rng.random() * (size + 28)
        sy_ = y - 10 + rng.random() * (size + 20)
        w = rng.choice((1, 2, 2, 3, 5))
        col = rng.choice((K["cyan"], K["mag"], fill, K["ink"]))
        specks.append(
            f'<rect class="eg en{k}" x="{sx_:.1f}" y="{sy_:.1f}" width="{w}" height="1.5" fill="{col}"/>'
        )
    out += specks

    # keyframes, in % of the quote's cycle; the word swaps at 45%
    kf = lambda name, frames: (
        f"@keyframes {name}{{" + "".join(f"{p}{{{v}}}" for p, v in frames) + "}"
    )
    css = [
        f".eg{{opacity:0}} .ec,.em,.eo,.ef,{','.join(f'.es{k}' for k in range(bands))},{','.join(f'.en{k}' for k in range(7))}{{animation-duration:{cyc}s;animation-iteration-count:infinite;animation-timing-function:steps(1,end)}}",
        ".ec{animation-name:ec} .em{animation-name:em} .eo{animation-name:eo} .ef{animation-name:ef}",
        kf(
            "ec",
            [
                ("0%,43%", "opacity:0"),
                ("44%", "opacity:.9;transform:translate(-4px,0)"),
                ("46%", "opacity:.9;transform:translate(-2px,1px)"),
                ("48%", "opacity:.8;transform:translate(-5px,0)"),
                ("50%", "opacity:.7;transform:translate(-1px,0)"),
                ("53%", "opacity:.5;transform:translate(-2px,0)"),
                ("55%,100%", "opacity:0;transform:none"),
            ],
        ),
        kf(
            "em",
            [
                ("0%,43%", "opacity:0"),
                ("44%", "opacity:.9;transform:translate(4px,0)"),
                ("46%", "opacity:.9;transform:translate(3px,-1px)"),
                ("48%", "opacity:.8;transform:translate(5px,0)"),
                ("50%", "opacity:.7;transform:translate(2px,0)"),
                ("53%", "opacity:.5;transform:translate(1px,0)"),
                ("55%,100%", "opacity:0;transform:none"),
            ],
        ),
        kf(
            "ef",
            [
                ("0%,46%", "opacity:1"),
                ("47%,48%", "opacity:0"),
                ("49%,50%", "opacity:1"),
                ("51%", "opacity:0"),
                ("52%,100%", "opacity:1"),
            ],
        ),
        kf(
            "eo",
            [
                ("0%,46%", "opacity:0"),
                ("47%,48%", "opacity:1"),
                ("49%,50%", "opacity:0"),
                ("51%", "opacity:1"),
                ("52%,100%", "opacity:0"),
            ],
        ),
    ]
    for k in range(bands):  # each slice tears sideways on its own few frames
        steps = [("0%,43%", "transform:none")]
        for p in (44, 45, 47, 49, 51):
            d = rng.choice((0, 0, -7, -4, -2, 2, 4, 8))
            steps.append((f"{p}%", f"transform:translateX({d}px)"))
        steps.append(("53%,100%", "transform:none"))
        css += [f".es{k}{{animation-name:es{k}}}", kf(f"es{k}", steps)]
    for k in range(7):  # specks blink on and off through the burst
        on = sorted(rng.sample(range(44, 56), 3))
        frames = [("0%", "opacity:0")]
        for p in on:
            frames += [(f"{p}%", "opacity:1"), (f"{p + 1}%", "opacity:0")]
        frames.append(("100%", "opacity:0"))
        css += [f".en{k}{{animation-name:en{k}}}", kf(f"en{k}", frames)]
    css.append(
        "@media (prefers-reduced-motion:reduce){.ec,.em,.eo,.ef,"
        + ",".join(f".es{k}" for k in range(bands))
        + ","
        + ",".join(f".en{k}" for k in range(7))
        + "{animation:none!important}}"
    )
    return "".join(out), "".join(css)


def quote_parts():
    W, X0, Y1, Y2, CW, cyc = 760, 52, 84, 146, 24.0, 4.0
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
    eth, eth_css = eth_cutout(ox + 24, oy + 24, 52, K["acc"], cyc)
    obj = (
        f'<rect x="{ox}" y="{oy}" width="100" height="100" fill="none" stroke="{K["line"]}"/>'
        f'<rect x="{ox + 16}" y="{oy + 16}" width="68" height="68" fill="none" stroke="{K["cline"]}"/>'
        + eth
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
    return style + eth_css, body


GLITCH_CSS = (
    ".gl{opacity:0}"
    "@keyframes gl{0%,93%{opacity:0;transform:none}94%{opacity:1;transform:translateX(3px)}95%{opacity:0}"
    "96%{opacity:1;transform:translateX(-2px)}97%,100%{opacity:0;transform:none}}"
    "@media (prefers-reduced-motion:reduce){.gl{animation:none!important}}"
)


def barcode(x0, x1, y, seed, name):
    """A separator drawn like a block hash: bar widths come from a hash of this run's data,
    so it changes whenever the numbers do. A few short stretches glitch teal now and then."""
    digest = hashlib.sha256(seed.encode()).hexdigest()
    bars, x, k = [], x0, 0
    while x < x1:
        n = int(digest[k % 64], 16)
        w, gap = 1 + n % 4, 2 + (n >> 2)
        bars.append((x, min(w, x1 - x)))
        x += w + gap
        k += 1
    rect = lambda x, w, fill: (
        f'<rect x="{x}" y="{y}" width="{w}" height="10" fill="{fill}"/>'
    )
    out, css = "".join(rect(x, w, K["cline"]) for x, w in bars), ""
    for g in range(
        3
    ):  # three glitch spots; where and how often comes from the hash too,
        n = int(digest[g * 8 : g * 8 + 8], 16)  # so separators never flicker in sync
        gx = x0 + n % int(x1 - x0 - 48)
        run = [(x, w) for x, w in bars if gx <= x < gx + 18 + n % 30]
        out += (
            f'<g class="gl {name}{g}">'
            + "".join(rect(x, w, K["acc"]) for x, w in run)
            + "</g>"
        )
        dur, delay = 5 + (n >> 4) % 40 / 10, (n >> 12) % 70 / 10
        css += f".{name}{g}{{animation:gl {dur}s {delay}s infinite steps(1,end)}}"
    return out, css


def chain_parts(starts, cur, first, weeks, pending, colours, total):
    """The chain, in its own units: (style, body, width, height).
    Every week of the quarter sits on one strip, but only a window of SHOW weeks shows. On each
    loop the strip rolls from the quarter's first week to the window that holds today, 4th from
    the left (or last, in the quarter's final weeks), then holds so people can read it."""
    N = len(starts)
    lo = max(0, min(cur - (SHOW - 2), N - SHOW))
    B, G, X0, Y0 = 72, 16, 24, 84
    WW = SHOW * (B + G) - G  # window width
    W = X0 + WW + X0
    sx = lambda i: X0 + i * (B + G)
    cx = lambda i: sx(i) + B / 2
    clip = lambda x, y, w, h, c=8: (
        f"M{x},{y} H{x + w - c} L{x + w},{y + c} V{y + h} H{x + c} L{x},{y + h - c} Z"
    )
    mid = Y0 + B / 2

    # timeline (seconds): fade in, roll, arrive, hold for reading, fade out, loop
    D = min(1.2 + 0.12 * lo, 2.2) if lo else 0
    TA = 0.5 + D
    TE = TA + 0.3 + READ
    CYC = round(TE + 0.4, 2)
    pct = lambda t: f"{t / CYC * 100:.2f}%"
    dx = lo * (B + G)
    css = (
        f".roll{{animation:roll {CYC}s infinite}} .win{{animation:win {CYC}s infinite}} .ovl{{animation:ovl {CYC}s infinite}}"
        f"@keyframes roll{{0%,{pct(0.5)}{{transform:translateX(0);animation-timing-function:{EASE}}}{pct(TA)},100%{{transform:translateX(-{dx}px)}}}}"
        f"@keyframes win{{0%{{opacity:0}}{pct(0.3)},{pct(TE)}{{opacity:1}}100%{{opacity:0}}}}"
        f"@keyframes ovl{{0%,{pct(TA)}{{opacity:0}}{pct(TA + 0.3)},{pct(TE)}{{opacity:1}}100%{{opacity:0}}}}"
    )

    strip = []  # everything that rolls
    for m in sorted(
        {d.month for d in starts}, key=lambda m: (m - starts[0].month) % 12
    ):
        idx = [i for i, d in enumerate(starts) if d.month == m]
        a, b = sx(idx[0]), sx(idx[-1]) + B
        strip.append(
            f'<text class="mo" x="{a}" y="{Y0 - 24}">// {starts[idx[0]].strftime("%b").upper()}</text>'
            f'<line stroke="{K["line"]}" x1="{a}" y1="{Y0 - 16}" x2="{b}" y2="{Y0 - 16}"/>'
        )
    # the chain runs on past both ends of the quarter
    strip.append(
        f'<line x1="{sx(0) - G - 40}" y1="{mid}" x2="{sx(0)}" y2="{mid}" stroke="{K["cline"]}" stroke-width="1.5" stroke-dasharray="2 3"/>'
        f'<line x1="{sx(N - 1) + B}" y1="{mid}" x2="{sx(N - 1) + B + G + 40}" y2="{mid}" stroke="{K["line"]}" stroke-width="1.5" stroke-dasharray="2 3"/>'
    )
    for i, wk in enumerate(starts):
        x = sx(i)
        live = first <= i <= cur
        if i > 0:
            dash = "" if live and i > first else 'stroke-dasharray="2 3"'
            strip.append(
                f'<line x1="{x - G}" y1="{mid}" x2="{x}" y2="{mid}" stroke="{K["cline"] if live else K["line"]}" stroke-width="1.5" {dash}/>'
            )
        strip.append(
            f'<text class="lb" x="{cx(i)}" y="{Y0 + B + 20}" opacity="{1 if live else 0.5}">{wk.strftime("%m.%d")}</text>'
        )
        if not live:  # upcoming, or before `since`
            strip.append(
                f'<path d="{clip(x + 0.5, Y0 + 0.5, B - 1, B - 1)}" fill="none" stroke="{K["line"]}" stroke-dasharray="2 3"/>'
            )
            continue
        prs = weeks[i]
        strip.append(
            f'<path d="{clip(x + 0.5, Y0 + 0.5, B - 1, B - 1)}" fill="{K["plate"]}" stroke="{K["cline"]}"/>'
            f'<text class="n" x="{cx(i)}" y="{Y0 + 27}" style="fill:{K["ink"] if prs else K["line"]}">{len(prs):02d}</text>'
        )
        shown = prs[:12]  # the count above stays exact; the block holds at most 3 rows
        Sz, P, cols = 9, 3, 4
        rows = (len(shown) + cols - 1) // cols
        for j, s in enumerate(shown):
            r, c = divmod(j, cols)
            nr = min(cols, len(shown) - r * cols)
            px = cx(i) - (nr * Sz + (nr - 1) * P) / 2 + c * (Sz + P)
            py = Y0 + B - 11 - Sz - (rows - 1 - r) * (Sz + P)
            strip.append(
                f'<rect x="{px:.1f}" y="{py:.1f}" width="{Sz}" height="{Sz}" fill="{colours[s]}"/>'
            )

    # what appears once the strip arrives: focus on today's block, its tag, the mempool
    xb = X0 + (cur - lo) * (B + G)
    ovl = [
        f'<path class="cur" d="{clip(xb + 0.5, Y0 + 0.5, B - 1, B - 1)}" fill="none"/>'
        f'<rect x="{xb + 2}" y="{Y0 + B + 29}" width="5" height="9" fill="{K["acc"]}"/><text class="tag" x="{xb + 11}" y="{Y0 + B + 38}">PROPOSING</text>'
    ]
    if starts[lo].month == starts[lo - 1].month if lo else False:
        # the window starts mid-month, so that month's label rolled off the left: pin it
        ovl.append(
            f'<text class="mo" x="{X0}" y="{Y0 - 24}">// {starts[lo].strftime("%b").upper()}</text>'
        )
    ty = Y0 + B + 50
    if pending:
        label = f"MEMPOOL (OPEN PRS) · {len(pending):02d}"
        shown = pending[:12]
        Sz, P = 9, 4
        lw = len(label) * 8.2 + 18
        tw = lw + len(shown) * (Sz + P) + 8
        tx = max(X0, min(xb - G / 2 - 10, X0 + WW - tw))
        flow = f"M{xb - G / 2},{ty} V{Y0 + B - 14} H{xb}"
        ovl.append(
            f'<path d="{flow}" fill="none" stroke="{K["line"]}" stroke-width="1.5"/>'
            f'<path class="flow" d="{flow}" fill="none" stroke="{K["acc"]}" stroke-width="1.5"/>'
            f'<rect x="{tx + 0.5}" y="{ty + 0.5}" width="{tw - 1}" height="20" fill="none" stroke="{K["cline"]}" stroke-dasharray="3 3"/>'
            f'<text class="mp" x="{tx + 10}" y="{ty + 14}">{label}</text>'
            + "".join(
                f'<rect x="{tx + lw + j * (Sz + P) + 0.5}" y="{ty + 6.5}" width="{Sz - 1}" height="{Sz - 1}" fill="none" stroke="{colours[s]}" stroke-width="1.5"/>'
                for j, s in enumerate(shown)
            )
        )
    ly = ty + 20 + 36
    leg, lx = [], X0
    for name, col in colours.items():
        leg.append(
            f'<rect x="{lx}" y="{ly - 9}" width="9" height="9" fill="{col}"/><text class="lg" x="{lx + 15}" y="{ly}">{esc(name.upper())}</text>'
        )
        lx += 15 + len(name) * 8 + 26
    H = ly + 14
    title = f"{total} merged PRs"
    body = (
        f'<text class="h" x="{X0}" y="30">{title}</text>'
        + f'<text class="sub" x="{X0 + len(title) * 9.6 + 18}" y="29">SLOT {cur + 1:02d}/{N}</text>'
        + f'<clipPath id="win"><rect x="{X0 - G + 2}" y="{Y0 - 40}" width="{WW + 2 * G - 4}" height="{B + 72}"/></clipPath>'
        + f'<g clip-path="url(#win)"><g class="win"><g class="roll" transform="translate(-{dx},0)">{"".join(strip)}</g></g></g>'
        + f'<g class="ovl">{"".join(ovl)}</g>'
        + f'<line stroke="{K["line"]}" x1="{X0}" y1="{ly - 22}" x2="{X0 + WW}" y2="{ly - 22}"/>'
        + "".join(leg)
    )
    style = (
        f".h{{font:600 16px {MONO};fill:{K['ink']};letter-spacing:-.01em}}"
        f".sub,.mo,.lb,.tag,.lg,.mp{{font-family:{MONO}}}"
        f".sub{{font-size:9px;fill:{K['body']};letter-spacing:.08em}} .mo{{font-size:11px;fill:{K['muted']};letter-spacing:.1em}}"
        f".lb{{font-size:12px;fill:{K['muted']};text-anchor:middle}} .n{{font:500 17px {MONO};text-anchor:middle}}"
        f".tag{{font-size:11px;fill:{K['acc']};letter-spacing:.1em}} .lg{{font-size:11px;fill:{K['muted']};letter-spacing:.08em}}"
        f".mp{{font-size:11px;fill:{K['muted']};letter-spacing:.08em}}"
        f".cur{{stroke:{K['acc']};stroke-width:1.5;stroke-dasharray:5 4;animation:pulse 1.6s ease-in-out infinite}}"
        "@keyframes pulse{50%{stroke-opacity:.25}}"
        ".flow{stroke-dasharray:4 20;stroke-opacity:.7;animation:flow 1.2s linear infinite}"
        "@keyframes flow{from{stroke-dashoffset:24}to{stroke-dashoffset:0}}" + css
    )
    return style, body, W, H


def header_svg(starts, qlabel, span, cur, first, weeks, pending, colours):
    """Hero quote, then this quarter's chain, as one image, separated by block-hash barcodes."""
    W, X0 = 760, 52
    qstyle, qbody = quote_parts()
    total = sum(map(len, weeks))
    cstyle, cbody, cw, ch = chain_parts(
        starts, cur, first, weeks, pending, colours, total
    )
    S = (W - 2 * X0) / (
        cw - 2 * 24
    )  # the chain's window spans the same width as the quote
    seed = f"{qlabel}|{total}|{weeks}|{len(pending)}"
    y = 238
    sep1, g1 = barcode(X0, W - X0, y, seed, "ga")
    qy = y + 40
    band = (
        f'<text class="qm" x="{X0}" y="{qy}">&gt; THIS QUARTER</text>'
        + f'<text class="qh" x="{X0}" y="{qy + 36}">{esc(qlabel)}</text>'
        + f'<text class="qr" x="{W - X0}" y="{qy + 34}">{span}</text>'
    )
    cy = qy + 56
    chain = (
        f'<g transform="translate({X0 - 24 * S:.2f},{cy}) scale({S:.4f})">{cbody}</g>'
    )
    by = cy + ch * S + 18
    sep2, g2 = barcode(X0, W - X0, by, seed + "|end", "gb")
    H = round(by + 10 + 30)
    style = (
        qstyle
        + cstyle
        + f".qm{{font:12px {MONO};fill:{K['muted']};letter-spacing:.12em}}"
        + f".qh{{font:600 30px {MONO};fill:{K['ink']};letter-spacing:.01em}}"
        + f".qr{{font:14px {MONO};fill:{K['acc']};letter-spacing:.12em;text-anchor:end}}"
        + GLITCH_CSS
        + g1
        + g2
        + "@media (prefers-reduced-motion:reduce){.roll,.win,.ovl,.cur,.flow{animation:none!important}}"
    )
    label = (
        "The fork is always six months away. Commeths are not. Mike Tython, probably. "
        f"{qlabel}, {span.title()}: {total} merged PRs, week {cur + 1} of {len(starts)}, {len(pending)} open."
    )
    return svg(W, H, label, style, qbody + sep1 + band + chain + sep2)


def heading_svg(label, count, index, colour):
    W, H, X0 = 1100, 60, 24
    sw = (
        f'<rect x="{X0}" y="22" width="14" height="14" fill="{colour}"/>'
        if colour
        else f'<rect x="{X0 + 0.5}" y="22.5" width="13" height="13" fill="none" stroke="{K["cline"]}"/>'
    )
    bar, bar_css = barcode(
        X0 + 70 + len(label) * 15, W - 80, 25.5, f"{label}|{count}|{index}", "g"
    )
    body = (
        sw
        + f'<text class="m" x="{X0 + 26}" y="35">{index:02d}</text>'
        + f'<text class="h" x="{X0 + 56}" y="39">{esc(label)}</text>'
        + bar
        + (
            f'<text class="c" x="{W - 24}" y="38">{count:02d}</text>'
            if count is not None
            else ""
        )
    )
    style = (
        f".h{{font:600 24px {MONO};fill:{K['ink']};letter-spacing:.02em}} .m{{font:12px {MONO};fill:{K['muted']}}}"
        f".c{{font:500 22px {MONO};fill:{K['acc']};text-anchor:end}}"
        + GLITCH_CSS
        + bar_css
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

    def write(name, text):
        """Write an asset; return its README src with a content hash so browsers refetch it when it changes."""
        open(os.path.join(ASSETS, name), "w").write(text)
        return f"assets/{name}?v={hashlib.sha1(text.encode()).hexdigest()[:8]}"

    header = write(
        "header.svg",
        header_svg(
            starts,
            qlabel,
            f"{a:%b %d} — {qe:%b %d}".upper(),
            cur,
            first,
            weeks,
            pend_secs,
            colours,
        ),
    )

    md = [
        f'<img src="{header}" alt="“The fork ith alwayth thix monthth away. Commeths are not.” — Mike Tython, probably. {qlabel}: {len(merged)} merged PRs" width="100%">',
        "",
    ]
    for n, (sec, repos) in enumerate(sections.items(), 1):
        sec_merged = [it for it in merged if sec_of(it) == sec]
        sec_open = [it for it in pending if sec_of(it) == sec]
        if not sec_merged and not sec_open:
            continue
        slug = "h-" + "".join(ch for ch in sec.lower() if ch.isalnum()) + ".svg"
        src = write(slug, heading_svg(sec, len(sec_merged), n, colours[sec]))
        md += [
            f'<img src="{src}" alt="{sec} · {len(sec_merged)} merged" width="100%">',
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
        src = write(
            "h-past.svg", heading_svg("Past quarters", None, len(sections) + 1, None)
        )
        md += [
            f'<img src="{src}" alt="Past quarters" width="100%">',
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
    md += [
        '<div align="center">',
        "",
        f"**[All contributions →]({search_url(base + ' is:merged')})**",  # all time, ignores since
        "",
        f"<sub>Updated {today} · counts merged PRs only</sub>",
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
