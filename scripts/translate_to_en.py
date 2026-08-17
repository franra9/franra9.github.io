#!/usr/bin/env python3
"""Create English copies of site content for review.

Reads Catalan/mixed source files and writes English counterparts under
/en/ permalinks. Skips a target file when translation_source_hash still
matches, so reviewed edits are kept until the source changes.

Requires DEEPL_API_KEY (preferred) or OPENAI_API_KEY.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASH_KEY = "translation_source_hash"
OF_KEY = "translation_of"

PAGE_JOBS = [
    {
        "source": "index.html",
        "target": "en/index.html",
        "permalink": "/en/",
        "rewrite_home_links": True,
    },
    {
        "source": "_pages/cv.md",
        "target": "_pages/cv-en.md",
        "permalink": "/en/cv/",
    },
    {
        "source": "_pages/resume.md",
        "target": "_pages/resume-en.md",
        "permalink": "/en/resume/",
    },
]

KEEP_RE = re.compile(
    r"(<iframe\b[^>]*>.*?</iframe>"
    r"|<img\b[^>]*>"
    r"|```[\s\S]*?```"
    r"|\{\{[\s\S]*?\}\}"
    r"|\{%[\s\S]*?%\})",
    re.IGNORECASE | re.DOTALL,
)

INTERNAL_HREFS = (
    ('href="/cv/"', 'href="/en/cv/"'),
    ("href='/cv/'", "href='/en/cv/'"),
    ('href="/resume/"', 'href="/en/resume/"'),
    ("href='/resume/'", "href='/en/resume/'"),
    ("](/cv/)", "](/en/cv/)"),
    ("](/resume/)", "](/en/resume/)"),
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def split_front_matter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", text
    return parts[1].strip("\n"), parts[2].lstrip("\n")


def yaml_get(front: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.*)$", front, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
        return value[1:-1]
    return value


def yaml_set(front: str, key: str, value: str) -> str:
    quoted = '"' + value.replace('"', '\\"') + '"'
    line = f"{key}: {quoted}"
    pattern = re.compile(rf"^{re.escape(key)}\s*:.*$", re.MULTILINE)
    if pattern.search(front):
        return pattern.sub(line, front, count=1)
    if front.strip():
        return front.rstrip() + "\n" + line + "\n"
    return line + "\n"


def protect(text: str) -> tuple[str, list[str]]:
    kept: list[str] = []

    def repl(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return f"@@KEEP{len(kept) - 1}@@"

    return KEEP_RE.sub(repl, text), kept


def restore(text: str, kept: list[str]) -> str:
    for i, chunk in enumerate(kept):
        text = text.replace(f"@@KEEP{i}@@", chunk)
    return text


def rewrite_internal_links(text: str) -> str:
    for old, new in INTERNAL_HREFS:
        text = text.replace(old, new)
    return text


def deepl_endpoint(api_key: str) -> str:
    if api_key.endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


def translate_deepl(text: str, api_key: str) -> str:
    data = urllib.parse.urlencode(
        {
            "text": text,
            "target_lang": "EN",
            "preserve_formatting": "1",
        }
    ).encode()
    request = urllib.request.Request(
        deepl_endpoint(api_key),
        data=data,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode())
    return payload["translations"][0]["text"]


def translate_openai(text: str, api_key: str) -> str:
    body = json.dumps(
        {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Translate into natural English. Keep Markdown and HTML "
                        "structure exactly. Do not wrap the result in code fences. "
                        "Do not translate URLs, file paths, @@KEEP...@@ placeholders, "
                        "or HTML attribute values. Keep people's names. Output only "
                        "the translation."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode())
    return payload["choices"][0]["message"]["content"].strip()


def translate_text(text: str) -> str:
    if not text.strip():
        return text
    protected, kept = protect(text)
    deepl_key = os.environ.get("DEEPL_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    try:
        if deepl_key:
            translated = translate_deepl(protected, deepl_key)
        elif openai_key:
            translated = translate_openai(protected, openai_key)
        else:
            raise SystemExit(
                "Set DEEPL_API_KEY (preferred) or OPENAI_API_KEY as a repository secret."
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"Translation API error {exc.code}: {detail}") from exc
    return restore(translated, kept)


def write_english(source: Path, target: Path, permalink: str, body_extra: str | None = None) -> bool:
    source_digest = file_hash(source)
    if target.exists():
        existing_front, _ = split_front_matter(target.read_text(encoding="utf-8"))
        if yaml_get(existing_front, HASH_KEY) == source_digest:
            print(f"skip (unchanged): {source.relative_to(ROOT)}")
            return False

    original = source.read_text(encoding="utf-8")
    front, body = split_front_matter(original)
    title = yaml_get(front, "title")
    if title:
        front = yaml_set(front, "title", translate_text(title))
    front = yaml_set(front, "permalink", permalink)
    front = yaml_set(front, OF_KEY, str(source.relative_to(ROOT)))
    front = yaml_set(front, HASH_KEY, source_digest)

    english_body = rewrite_internal_links(translate_text(body))
    if body_extra and body_extra not in english_body:
        english_body = body_extra + english_body

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\n{front.strip()}\n---\n\n{english_body.lstrip()}", encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)}")
    return True


def post_jobs() -> list[dict[str, str]]:
    jobs = []
    for source in sorted((ROOT / "_posts").glob("*.md")):
        if source.name.endswith("-en.md"):
            continue
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", source.stem)
        jobs.append(
            {
                "source": str(source.relative_to(ROOT)),
                "target": str(source.with_name(f"{source.stem}-en.md").relative_to(ROOT)),
                "permalink": f"/en/{slug}/",
            }
        )
    return jobs


def ensure_english_nav() -> bool:
    path = ROOT / "_data" / "navigation.yml"
    text = path.read_text(encoding="utf-8")
    if "url: /en/" in text:
        return False
    addition = '  - title: "English"\n    url: /en/\n'
    path.write_text(text.rstrip() + "\n" + addition, encoding="utf-8")
    print("updated _data/navigation.yml")
    return True


def main() -> int:
    os.chdir(ROOT)
    changed = False
    home_note = (
        '<p><a href="/">Original version</a></p>\n\n'
    )
    for job in PAGE_JOBS + post_jobs():
        extra = home_note if job.get("rewrite_home_links") else None
        changed |= write_english(
            ROOT / job["source"],
            ROOT / job["target"],
            job["permalink"],
            extra,
        )
    changed |= ensure_english_nav()
    if not changed:
        print("No English files need updating.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
