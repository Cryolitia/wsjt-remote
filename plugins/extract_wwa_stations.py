from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse


class WwaStationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href") or ""
        call = _call_from_qrz_href(href)
        if call and call not in self._seen:
            self.calls.append(call)
            self._seen.add(call)


def main() -> None:
    root = Path(__file__).resolve().parent
    source = root / "1.html"
    target = root / "wwa_stations.txt"

    parser = WwaStationParser()
    parser.feed(source.read_text(encoding="utf-8", errors="replace"))
    target.write_text("\n".join(parser.calls) + ("\n" if parser.calls else ""), encoding="utf-8")
    print(f"wrote {len(parser.calls)} calls to {target}")


def _call_from_qrz_href(href: str) -> str:
    parsed = urlparse(href)
    if parsed.netloc.lower() not in {"www.qrz.com", "qrz.com"}:
        return ""
    parts = [unquote(part).strip().upper() for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() != "db":
        return ""
    return parts[1]


if __name__ == "__main__":
    main()
