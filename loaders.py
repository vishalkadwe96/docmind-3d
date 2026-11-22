"""Text extraction for DOCX, PPTX, TXT/MD, web pages and YouTube transcripts.

Pure Python on purpose (stdlib + httpx + youtube-transcript-api), so nothing here needs a
compiled package that a Windows Application Control policy might block.

Every loader returns "pages": a list of (location_label, text) tuples, e.g.
("Slide 3", "..."), ("Section 2", "..."), ("12:30", "...").
"""
import io
import ipaddress
import logging
import re
import socket
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

log = logging.getLogger("docmind.loaders")

MAX_PAGE_BYTES = 5 * 1024 * 1024      # web pages / remote files
MAX_XML_BYTES = 50 * 1024 * 1024      # one unzipped XML member of a DOCX/PPTX
PAGE_CHARS = 2500                     # target size of one "section" for formats without pages


class LoadError(ValueError):
    """A problem we can show to the user as-is."""


# ---------------------------------------------------------------- helpers
def _paginate(paragraphs, label, limit=PAGE_CHARS):
    """Group paragraphs into sections of roughly `limit` characters."""
    pages, cur, size = [], [], 0
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if cur and size + len(p) > limit:
            pages.append((f"{label} {len(pages) + 1}", "\n".join(cur)))
            cur, size = [], 0
        cur.append(p)
        size += len(p) + 1
    if cur:
        pages.append((f"{label} {len(pages) + 1}", "\n".join(cur)))
    return pages


def _read_member(zf, name):
    info = zf.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise LoadError("This file is too large to read safely.")
    data = zf.read(name)
    # Office XML never contains a DOCTYPE; rejecting it blocks entity-expansion tricks.
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise LoadError("This file contains unsupported XML.")
    return ET.fromstring(data)


def _open_zip(data):
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise LoadError("This file is not a valid Office document.") from None


# ---------------------------------------------------------------- DOCX
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def load_docx(data: bytes):
    zf = _open_zip(data)
    try:
        root = _read_member(zf, "word/document.xml")
    except KeyError:
        raise LoadError("This doesn't look like a .docx file (old .doc files aren't supported).") from None
    paras = []
    for p in root.iter(_W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == _W + "t" and el.text:
                parts.append(el.text)
            elif el.tag == _W + "tab":
                parts.append("\t")
            elif el.tag == _W + "br":
                parts.append("\n")
        paras.append("".join(parts))
    pages = _paginate(paras, "Section")
    if not pages:
        raise LoadError("No text found in this document.")
    return pages


# ---------------------------------------------------------------- PPTX
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _slide_text(root):
    lines = []
    for p in root.iter(_A + "p"):
        t = "".join(el.text or "" for el in p.iter(_A + "t"))
        if t.strip():
            lines.append(t.strip())
    return lines


def load_pptx(data: bytes):
    zf = _open_zip(data)
    num = lambda n: int(re.search(r"(\d+)\.xml$", n).group(1))
    slides = sorted(
        (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=num
    )
    if not slides:
        raise LoadError("This doesn't look like a .pptx file (old .ppt files aren't supported).")
    notes = {
        num(n): n for n in zf.namelist() if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)
    }
    pages = []
    for i, name in enumerate(slides, 1):
        lines = _slide_text(_read_member(zf, name))
        n = notes.get(num(name))
        if n:
            note_lines = _slide_text(_read_member(zf, n))
            # notes slides repeat the slide number as a lone digit line; drop it
            note_lines = [l for l in note_lines if not l.isdigit()]
            if note_lines:
                lines.append("Speaker notes: " + " ".join(note_lines))
        if lines:
            pages.append((f"Slide {i}", "\n".join(lines)))
    if not pages:
        raise LoadError("No text found in this presentation.")
    return pages


# ---------------------------------------------------------------- plain text
def load_text(data: bytes):
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("latin-1", errors="replace")
    pages = _paginate(re.split(r"\n\s*\n", text.replace("\r\n", "\n")), "Part")
    if not pages:
        raise LoadError("This file is empty.")
    return pages


# ---------------------------------------------------------------- web pages
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside",
            "form", "iframe", "template", "button"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "blockquote", "pre",
             "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def html_to_pages(html_text: str):
    ex = _TextExtractor()
    ex.feed(html_text)
    ex.close()
    lines = [" ".join(l.split()) for l in "".join(ex.parts).split("\n")]
    lines = [l for l in lines if l]
    if sum(len(l) for l in lines) < 200:
        raise LoadError(
            "Couldn't find readable text on that page (it may need JavaScript or a login)."
        )
    return " ".join(ex.title.split()), _paginate(lines, "Section")


def _validate_url(url: str):
    """Only public http(s) addresses: blocks localhost / LAN / cloud-metadata targets (SSRF)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise LoadError("Please enter a full link starting with http:// or https://")
    if p.username or p.password:
        raise LoadError("Links with a username or password aren't supported.")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise LoadError("Couldn't find that website.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise LoadError("That address isn't allowed.")


def _fetch(url: str):
    """Download with redirect re-validation and a hard size cap. Returns (final_url, ctype, bytes, encoding)."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DocMind/1.0)", "Accept-Language": "en,hi;q=0.8"}
    for _ in range(5):
        _validate_url(url)
        try:
            with httpx.stream("GET", url, headers=headers, timeout=15, follow_redirects=False) as r:
                if r.is_redirect:
                    nxt = r.headers.get("location")
                    if not nxt:
                        raise LoadError("The site redirected without a destination.")
                    url = urljoin(url, nxt)
                    continue
                if r.status_code >= 400:
                    raise LoadError(f"The site returned an error (HTTP {r.status_code}).")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_PAGE_BYTES:
                        raise LoadError("That page is too large (max 5 MB).")
                return url, ctype, bytes(buf), r.encoding
        except httpx.HTTPError:
            raise LoadError("Couldn't download that page.") from None
    raise LoadError("Too many redirects.")


# ---------------------------------------------------------------- YouTube
def youtube_id(url: str):
    p = urlparse(url)
    host = (p.hostname or "").lower()
    host = host.removeprefix("www.").removeprefix("m.")
    vid = ""
    if host == "youtu.be":
        vid = p.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        if p.path == "/watch":
            vid = parse_qs(p.query).get("v", [""])[0]
        else:
            m = re.match(r"^/(?:shorts|embed|live|v)/([\w-]{11})", p.path)
            vid = m.group(1) if m else ""
    else:
        return None
    return vid if re.fullmatch(r"[\w-]{11}", vid or "") else None


def _ts(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _yt_snippets(vid):
    from youtube_transcript_api import YouTubeTranscriptApi  # pip install youtube-transcript-api

    prefer = ["en", "en-IN", "en-US", "en-GB", "hi"]
    api = YouTubeTranscriptApi()
    # v1.x uses an instance with .list(); older versions use static list_transcripts()
    tl = api.list(vid) if hasattr(api, "list") else YouTubeTranscriptApi.list_transcripts(vid)
    try:
        tr = tl.find_transcript(prefer)
    except Exception:
        tr = next(iter(tl))  # any language that exists (Hindi, Marathi, auto-generated...)
    out = []
    for s in tr.fetch():
        if isinstance(s, dict):
            out.append((float(s["start"]), s["text"]))
        else:
            out.append((float(s.start), s.text))
    return out


def _yt_windows(snips, span=60):
    pages, cur, start = [], [], 0.0
    for t, text in snips:
        text = " ".join(str(text).split())
        if not text:
            continue
        if not cur:
            start = t
        elif t - start >= span:
            pages.append((_ts(start), " ".join(cur)))
            cur, start = [], t
        cur.append(text)
    if cur:
        pages.append((_ts(start), " ".join(cur)))
    return pages


def _yt_title(url, vid):
    try:
        r = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=6,
        )
        if r.status_code == 200:
            return r.json().get("title") or f"YouTube {vid}"
    except Exception:
        pass
    return f"YouTube {vid}"


def load_youtube(url: str, vid: str):
    try:
        snips = _yt_snippets(vid)
    except ImportError:
        raise LoadError(
            "YouTube support isn't installed. Run: python -m pip install youtube-transcript-api"
        ) from None
    except Exception as e:
        log.warning("YouTube transcript failed for %s: %s", vid, type(e).__name__)
        raise LoadError(
            "Couldn't get a transcript for this video (captions may be turned off, "
            "or YouTube is blocking requests from this network)."
        ) from None
    pages = _yt_windows(snips)
    if not pages:
        raise LoadError("This video's transcript is empty.")
    return {
        "kind": "youtube",
        "name": _yt_title(url, vid)[:80],
        "pages": pages,
        "key": f"youtube:{vid}",
    }


# ---------------------------------------------------------------- URL entry point
def load_url(url: str):
    """Returns a dict: {"kind", "name", "key", "pages"} or, for a PDF link, {"kind": "pdf", "name", "data"}."""
    url = url.strip()
    vid = youtube_id(url)
    if vid:
        return load_youtube(url, vid)

    final_url, ctype, body, enc = _fetch(url)
    host = urlparse(final_url).hostname or "web page"

    if ctype == "application/pdf" or body[:5] == b"%PDF-":
        name = (urlparse(final_url).path.rsplit("/", 1)[-1] or host)[:80]
        return {"kind": "pdf", "name": name, "data": body, "key": final_url}

    if ctype and not (ctype.startswith("text/") or "html" in ctype or "xml" in ctype):
        raise LoadError("That link isn't a web page or PDF.")

    try:
        text = body.decode(enc or "utf-8", errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    if ctype == "text/plain":
        pages, title = load_text(body), ""
    else:
        title, pages = html_to_pages(text)
    return {"kind": "web", "name": (title or host)[:80], "pages": pages, "key": final_url}