"""Reddit thread crawler — official OAuth API (read-only, application-only).

Why this design: Reddit killed anonymous `.json` access (returns 403 "Blocked").
The official API still works with a free app's credentials via the
application-only ("client_credentials") OAuth flow — read-only, NO reddit
password required.

ONE-TIME SETUP (free, ~60 seconds):
  1. Go to https://www.reddit.com/prefs/apps
  2. "create another app..." -> choose type **script**
  3. name: anything; redirect uri: http://localhost:8080  (unused but required)
  4. After creating:
       - client_id  = the string UNDER the app name (~14 chars)
       - client_secret = the "secret" field
  5. Export them:
       export REDDIT_CLIENT_ID=xxxx
       export REDDIT_CLIENT_SECRET=xxxx

USAGE:
  python reddit_crawler.py <thread_url_or_id> [--out file.txt] [--max-more 50]
  e.g. python reddit_crawler.py https://www.reddit.com/r/algotrading/comments/abc123/title/

Outputs the post + full nested comment tree as indented text (and JSON if --json).
"""
import os, sys, re, json, time, argparse
import urllib.request
import xml.etree.ElementTree as ET
import html as _html
import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "ignore")


def fetch_post_via_rss(url):
    """No-auth path: fetch the ORIGINAL POST via Reddit RSS. Comments NOT included
    (Reddit RSS returns the submission only). Works without app credentials."""
    base = url.split("?")[0].rstrip("/")
    rss = base + "/.rss"
    feed = _http_get(rss)
    root = ET.fromstring(feed)
    e = root.find("a:entry", ATOM)
    if e is None:
        # subreddit feed (multiple) — return list of thread links instead
        items = []
        for it in root.findall("a:entry", ATOM):
            t = it.find("a:title", ATOM)
            l = it.find("a:link", ATOM)
            items.append((t.text if t is not None else "", l.get("href") if l is not None else ""))
        return {"kind": "listing", "items": items}
    title = e.find("a:title", ATOM)
    author = e.find("a:author/a:name", ATOM)
    content = e.find("a:content", ATOM)
    updated = e.find("a:updated", ATOM)
    raw = content.text if content is not None and content.text else ""
    # content is HTML-escaped markdown-ish; strip tags crudely for readable text
    text = _html.unescape(raw)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    auth_txt = (author.text if author is not None else "").lstrip("/")
    auth_txt = auth_txt[2:] if auth_txt.startswith("u/") else auth_txt
    return {"kind": "post",
            "title": title.text if title is not None else "",
            "author": auth_txt,
            "updated": updated.text if updated is not None else "",
            "body": text.strip(),
            "url": base}


def get_token():
    cid = os.environ.get("REDDIT_CLIENT_ID")
    sec = os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("ERROR: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET env vars "
                 "(see header for how to get them).")
    r = requests.post(TOKEN_URL,
                      auth=(cid, sec),
                      data={"grant_type": "client_credentials"},
                      headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def thread_id_from_url(s):
    """Accept a full URL or a bare base36 id."""
    m = re.search(r"/comments/([a-z0-9]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-z0-9]+", s):
        return s
    sys.exit(f"Could not parse thread id from: {s}")


def api_get(token, path, params=None):
    h = {"User-Agent": UA, "Authorization": f"bearer {token}"}
    for attempt in range(4):
        r = requests.get(f"{API}{path}", headers=h, params=params, timeout=25)
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def expand_more(token, link_id, children_ids, max_more):
    """Fetch collapsed 'load more comments' nodes via /api/morechildren (batched)."""
    out = []
    BATCH = 90
    fetched = 0
    for i in range(0, len(children_ids), BATCH):
        if fetched >= max_more:
            break
        batch = children_ids[i:i + BATCH]
        data = api_get(token, "/api/morechildren", {
            "api_type": "json", "link_id": link_id,
            "children": ",".join(batch), "limit_children": "false",
        })
        things = data.get("json", {}).get("data", {}).get("things", [])
        out.extend(things)
        fetched += len(batch)
        time.sleep(0.6)  # be polite to rate limit
    return out


def walk(token, link_id, children, depth, lines, stats, max_more):
    for c in children:
        kind = c.get("kind")
        d = c.get("data", {})
        if kind == "t1":  # a comment
            author = d.get("author", "[deleted]")
            score = d.get("score", 0)
            body = (d.get("body") or "").strip().replace("\n", "\n" + "  " * (depth + 1))
            pad = "  " * depth
            lines.append(f"{pad}- u/{author} ({score}): {body}")
            stats["comments"] += 1
            replies = d.get("replies")
            if isinstance(replies, dict):
                walk(token, link_id, replies["data"]["children"], depth + 1, lines, stats, max_more)
        elif kind == "more":  # collapsed nodes
            ids = d.get("children", [])
            if ids:
                stats["more_expanded"] += 1
                things = expand_more(token, link_id, ids, max_more)
                walk(token, link_id, things, depth, lines, stats, max_more)


def crawl(url, max_more=50):
    token = get_token()
    tid = thread_id_from_url(url)
    data = api_get(token, f"/comments/{tid}", {"limit": 500, "depth": 50})
    post = data[0]["data"]["children"][0]["data"]
    comments = data[1]["data"]["children"]

    lines = []
    lines.append("=" * 80)
    lines.append(f"TITLE: {post.get('title')}")
    lines.append(f"SUBREDDIT: r/{post.get('subreddit')}   AUTHOR: u/{post.get('author')}   "
                 f"SCORE: {post.get('score')}   COMMENTS: {post.get('num_comments')}")
    lines.append(f"URL: https://reddit.com{post.get('permalink')}")
    lines.append("=" * 80)
    body = (post.get("selftext") or "").strip()
    if body:
        lines.append(body)
    lines.append("\n" + "-" * 30 + " COMMENTS " + "-" * 30)

    link_id = "t3_" + tid
    stats = {"comments": 0, "more_expanded": 0}
    walk(token, link_id, comments, 0, lines, stats, max_more)
    return "\n".join(lines), post, stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="thread URL or base36 id")
    ap.add_argument("--out", help="write to file")
    ap.add_argument("--full", action="store_true",
                    help="fetch full comment tree via OAuth (needs REDDIT_CLIENT_ID/SECRET + "
                         "completed Reddit developer registration). Default = post-only via RSS (no auth).")
    ap.add_argument("--max-more", type=int, default=50)
    args = ap.parse_args()

    if args.full:
        text, post, stats = crawl(args.url, args.max_more)
        note = f"[{stats['comments']} comments, {stats['more_expanded']} 'more' nodes]"
    else:
        r = fetch_post_via_rss(args.url)
        if r["kind"] == "listing":
            text = "SUBREDDIT LISTING:\n" + "\n".join(f"  {t}\n    {l}" for t, l in r["items"])
        else:
            text = (f"{'='*80}\nTITLE: {r['title']}\nAUTHOR: u/{r['author']}\n"
                    f"URL: {r['url']}\nUPDATED: {r['updated']}\n{'='*80}\n{r['body']}")
        note = "[post-only via RSS — no comments; use --full for comments]"

    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"Wrote {len(text):,} chars -> {args.out}  {note}")
    else:
        print(text)
        print(f"\n{note}", file=sys.stderr)
