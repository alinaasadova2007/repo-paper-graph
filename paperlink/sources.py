"""GitHub и OpenAlex: загрузка с кэшем в RawStore."""

from __future__ import annotations

import os
import time
from urllib.parse import quote

import requests

from .store import RawStore

ITMO_ROR = "https://ror.org/04txgxn49"
ITMO_OPENALEX = "https://openalex.org/I173089394"


class RateLimited(RuntimeError):
    pass


class GitHub:
    API = "https://api.github.com"
    RAW = "https://raw.githubusercontent.com"

    def __init__(self, store: RawStore, token: str | None = None):
        self.store = store
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/vnd.github+json",
                                     "User-Agent": "repo-paper-graph/0.1"})
        token = token or os.environ.get("GITHUB_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.remaining: int | None = None

    def _api(self, path: str, params: dict | None = None):
        resp = self.session.get(f"{self.API}{path}", params=params, timeout=30)
        self.remaining = int(resp.headers.get("X-RateLimit-Remaining", "1"))
        if resp.status_code == 403 and self.remaining == 0:
            raise RateLimited("GitHub API rate limit, задайте GITHUB_TOKEN")
        if resp.status_code in (204, 404, 409, 451):  # 204 - пустой репозиторий без контрибьюторов
            return None
        resp.raise_for_status()
        return resp.json()

    def org_repos(self, org: str, include_forks: bool = False) -> list[dict]:
        repos, page = [], 1
        while True:
            batch = self._api(f"/orgs/{org}/repos", {"per_page": 100, "page": page, "type": "public"})
            if not batch:
                break
            repos += batch
            if len(batch) < 100:
                break
            page += 1
        repos = [r for r in repos if include_forks or not r.get("fork")]
        for r in repos:
            self.store.put("github_repos", r["full_name"], r)
        return repos

    def raw_file(self, full_name: str, branch: str, names: tuple[str, ...]) -> tuple[str, str] | None:
        """Читает файл через raw.githubusercontent.com, не тратит лимит API."""
        for name in names:
            resp = self.session.get(f"{self.RAW}/{full_name}/{branch}/{name}", timeout=30)
            if resp.status_code == 200:
                return name, resp.text
        return None

    def files(self, repo: dict) -> dict:
        key = repo["full_name"]
        cached = self.store.get("github_files", key)
        if cached:
            return cached
        branch = repo.get("default_branch") or "main"
        readme = self.raw_file(key, branch, ("README.md", "readme.md", "README.rst", "README.MD", "README"))
        cff = self.raw_file(key, branch, ("CITATION.cff",))
        doc = {"readme_name": readme[0] if readme else None, "readme": readme[1] if readme else "",
               "citation_cff": cff[1] if cff else ""}
        self.store.put("github_files", key, doc)
        return doc

    def contributors(self, full_name: str, top: int = 5) -> list[dict]:
        cached = self.store.get("github_contributors", full_name)
        if cached is not None:
            return cached["items"]
        items = (self._api(f"/repos/{full_name}/contributors", {"per_page": top}) or [])[:top]
        items = [c for c in items if c.get("type") == "User"]
        for c in items:
            user = self.store.get("github_users", c["login"])
            if user is None:
                user = self._api(f"/users/{c['login']}") or {"login": c["login"]}
                self.store.put("github_users", c["login"], user)
            c["name"] = user.get("name")
        self.store.put("github_contributors", full_name, {"items": items})
        return items


class OpenAlex:
    API = "https://api.openalex.org"

    def __init__(self, store: RawStore, mailto: str | None = None):
        self.store = store
        self.session = requests.Session()
        self.mailto = mailto or os.environ.get("OPENALEX_MAILTO", "")
        self.api_key = os.environ.get("OPENALEX_API_KEY", "")  # бесплатный ключ, см. .env.example

    def work(self, kind: str, value: str) -> dict | None:
        key = f"{kind}:{value}"
        cached = self.store.get("openalex_works", key)
        if cached is not None:
            return cached.get("work")
        doi = value if kind == "doi" else f"10.48550/arxiv.{value}"
        params = {k: v for k, v in {"mailto": self.mailto, "api_key": self.api_key}.items() if v} or None
        work = None
        for attempt in range(4):
            try:
                resp = self.session.get(f"{self.API}/works/doi:{quote(doi, safe='/')}", params=params, timeout=30)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (400, 404):
                break
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            work = resp.json()
            break
        else:
            raise RateLimited("OpenAlex не отвечает или исчерпан лимит, задайте OPENALEX_API_KEY")
        self.store.put("openalex_works", key, {"work": work})
        return work


def is_itmo_authorship(authorship: dict) -> bool:
    for inst in authorship.get("institutions", []):
        if inst.get("ror") == ITMO_ROR or inst.get("id") == ITMO_OPENALEX:
            return True
    raw = " ".join(authorship.get("raw_affiliation_strings") or [])
    return "itmo" in raw.lower() or "итмо" in raw.lower()
