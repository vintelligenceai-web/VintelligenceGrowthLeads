import urllib.request

# Generic identifier on purpose: outbound research requests carry no company or personal details.
UA = "biz-research-bot/1.0"


def fetch(url, data=None, timeout=20, max_bytes=1_500_000):
    """GET (or POST when `data` is bytes). Returns (final_url, text). Raises on network/HTTP errors."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_bytes)
        charset = r.headers.get_content_charset() or "utf-8"
        try:
            return r.geturl(), raw.decode(charset, "replace")
        except LookupError:
            return r.geturl(), raw.decode("utf-8", "replace")
