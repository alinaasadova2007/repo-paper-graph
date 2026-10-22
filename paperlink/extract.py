"""Поиск ссылок на статьи в README и CITATION.cff.

Главный вопрос: это статья авторов репозитория или чужой инструмент / related work?
Поэтому каждой ссылке приписывается `role`:
  own      - ссылка из CITATION.cff или из раздела "Citation / How to cite / Публикации";
  mention  - любое другое упоминание (используемые библиотеки, related work, бенчмарки).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

# Скобки внутри DOI допустимы (10.1016/S0140-6736(20)30183-5), лишнюю закрывающую срезает normalize_doi.
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>\]}`,;]+)", re.IGNORECASE)
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~).*?^\s{0,3}\1", re.MULTILINE | re.DOTALL)
ARXIV_RE = re.compile(r"arxiv(?:\.org/(?:abs|pdf)/|[:\s]\s*)(\d{4}\.\d{4,5})", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$|^(.+)\n[=-]{3,}\s*$", re.MULTILINE)
OWN_SECTION_RE = re.compile(
    r"cit(e|ation|ing)|how to cite|reference us|publications?|papers?|"
    r"цитир|публикац|ссылка на|статьи",
    re.IGNORECASE,
)
# Ссылки, которые почти никогда не являются статьёй авторов (бейджи, Zenodo-архивы релизов).
NOISE_DOI_PREFIXES = ("10.5281/zenodo",)


@dataclass(frozen=True)
class PaperLink:
    kind: str   # doi | arxiv
    value: str  # нормализованный DOI или arXiv id
    source: str  # readme | citation_cff
    role: str   # own | mention
    section: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}"


def normalize_doi(doi: str) -> str:
    doi = doi.strip().rstrip(".").lower()
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1].rstrip(".")  # markdown-ссылка [текст](https://doi.org/...)
    doi = re.sub(r"\.(pdf|html?)$", "", doi)
    return doi


def _sections(markdown: str) -> list[tuple[str, str]]:
    """Делит markdown на (заголовок, текст раздела)."""
    fences = [m.span() for m in FENCE_RE.finditer(markdown)]
    marks = [(m.start(), (m.group(2) or m.group(3) or "").strip()) for m in HEADING_RE.finditer(markdown)
             if not any(a <= m.start() < b for a, b in fences)]  # "# комментарий" в блоке кода - не заголовок
    if not marks or marks[0][0] != 0:
        marks.insert(0, (0, ""))
    out = []
    for i, (start, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(markdown)
        out.append((title, markdown[start:end]))
    return out


def from_readme(text: str) -> list[PaperLink]:
    links: dict[str, PaperLink] = {}
    for title, body in _sections(text or ""):
        role = "own" if OWN_SECTION_RE.search(title) else "mention"
        # BibTeX-блок "@article{...}" почти всегда означает "процитируйте нас".
        if re.search(r"@(article|inproceedings|misc|software)\s*\{", body, re.IGNORECASE):
            role = "own"
        found = [("doi", normalize_doi(d)) for d in DOI_RE.findall(body)]
        found += [("arxiv", a) for a in ARXIV_RE.findall(body)]
        for kind, value in found:
            if kind == "doi" and value.startswith(NOISE_DOI_PREFIXES):
                continue
            link = PaperLink(kind, value, "readme", role, title[:80])
            prev = links.get(link.key)
            if prev is None or (prev.role == "mention" and role == "own"):
                links[link.key] = link
    return list(links.values())


def from_citation_cff(text: str) -> list[PaperLink]:
    try:
        data = yaml.safe_load(text or "") or {}
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    entries = [data.get("preferred-citation") or {}] + list(data.get("references") or [])
    links: list[PaperLink] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        role = "own" if i == 0 else "mention"
        candidates = [entry.get("doi")] + [ident.get("value") for ident in entry.get("identifiers", [])
                                            if isinstance(ident, dict) and ident.get("type") == "doi"]
        for doi in filter(None, candidates):
            value = normalize_doi(str(doi))
            if not value.startswith(NOISE_DOI_PREFIXES):
                links.append(PaperLink("doi", value, "citation_cff", role, "preferred-citation" if i == 0 else "references"))
    # DOI самого ПО (Zenodo) указывают на верхнем уровне, его пропускаем.
    return links


def merge(*groups: list[PaperLink]) -> list[PaperLink]:
    best: dict[str, PaperLink] = {}
    for group in groups:
        for link in group:
            prev = best.get(link.key)
            if prev is None or (prev.role == "mention" and link.role == "own"):
                best[link.key] = link
    return list(best.values())
