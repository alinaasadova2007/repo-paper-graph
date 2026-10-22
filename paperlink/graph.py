"""Сборка графа из сырых данных и выгрузка в Neo4j / Cypher / JSON.

Метки и типы рёбер (Person, Publication, Repository, AUTHORED, IMPLEMENTS) взяты из схемы PAUK:

  (:Repository)-[:MENTIONS_PAPER {source, role, section}]->(:Publication)
  (:Repository)-[:IMPLEMENTS {evidence}]->(:Publication)      # только при подтверждении
  (:Person)-[:AUTHORED {position, is_itmo}]->(:Publication)
  (:GitHubProfile)-[:CONTRIBUTED_TO {contributions}]->(:Repository)
  (:GitHubProfile)-[:POSSIBLY_SAME_AS {score}]->(:Person)
  (:Repository)-[:OWNED_BY]->(:GitHubProfile {type: 'Organization'})

IMPLEMENTS создаётся, только если ссылка имеет role=own И есть независимое
подтверждение: у статьи есть автор из ИТМО или контрибьютор репозитория похож
на автора статьи. Так упоминание статьи о scikit-learn не превращается в IMPLEMENTS.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .extract import PaperLink
from .names import name_similarity
from .sources import is_itmo_authorship
from .store import RawStore

SAME_AS_THRESHOLD = 0.9


@dataclass
class Graph:
    nodes: dict[tuple[str, str], dict] = field(default_factory=dict)  # (label, id) -> props
    edges: list[tuple[str, str, str, str, str, dict]] = field(default_factory=list)
    # (from_label, from_id, rel, to_label, to_id, props)
    _edge_index: dict[tuple[str, str, str, str, str], dict] = field(default_factory=dict, repr=False)

    def node(self, label: str, node_id: str, **props) -> None:
        current = self.nodes.setdefault((label, node_id), {"id": node_id})
        current.update({k: v for k, v in props.items() if v is not None})

    def edge(self, a: tuple[str, str], rel: str, b: tuple[str, str], **props) -> None:
        """Одна пара узлов - одно ребро данного типа (как MERGE в Neo4j)."""
        key = (a[0], a[1], rel, b[0], b[1])
        if key in self._edge_index:
            self._edge_index[key].update(props)
            return
        self._edge_index[key] = props
        self.edges.append((*key, props))

    def count(self) -> dict:
        labels: dict[str, int] = defaultdict(int)
        rels: dict[str, int] = defaultdict(int)
        for label, _ in self.nodes:
            labels[label] += 1
        for e in self.edges:
            rels[e[2]] += 1
        return {"nodes": dict(labels), "edges": dict(rels)}


def build(store: RawStore) -> Graph:
    g = Graph()
    for repo in store.all("github_repos"):
        full = repo["_id"]
        rkey = ("Repository", full)
        g.node("Repository", full, name=repo.get("name"), url=repo.get("html_url"),
               description=repo.get("description"), stars=repo.get("stargazers_count"),
               language=repo.get("language"), license=(repo.get("license") or {}).get("spdx_id"),
               created_at=repo.get("created_at"), pushed_at=repo.get("pushed_at"))
        owner = repo.get("owner") or {}
        g.node("GitHubProfile", owner.get("login", full.split("/")[0]), login=owner.get("login"),
               type=owner.get("type", "Organization"))
        g.edge(rkey, "OWNED_BY", ("GitHubProfile", owner.get("login", full.split("/")[0])))

        contributors = (store.get("github_contributors", full) or {}).get("items", [])
        for c in contributors:
            g.node("GitHubProfile", c["login"], login=c["login"], name=c.get("name"), type="User",
                   url=c.get("html_url"))
            g.edge(("GitHubProfile", c["login"]), "CONTRIBUTED_TO", rkey, contributions=c.get("contributions"))

        links_doc = store.get("paper_links", full) or {"links": []}
        for raw_link in links_doc["links"]:
            link = PaperLink(**raw_link)
            work = (store.get("openalex_works", link.key) or {}).get("work")
            if not work:
                g.node("LinkCandidate", link.key, kind=link.kind, value=link.value)
                g.edge(rkey, "MENTIONS_PAPER", ("LinkCandidate", link.key), source=link.source,
                       role=link.role, section=link.section, resolved=False)
                continue
            pid = work["id"].rsplit("/", 1)[-1]
            pkey = ("Publication", pid)
            authorships = work.get("authorships", [])
            itmo_authors = [a for a in authorships if is_itmo_authorship(a)]
            g.node("Publication", pid, title=work.get("display_name"), year=work.get("publication_year"),
                   doi=(work.get("doi") or "").replace("https://doi.org/", "") or None,
                   venue=((work.get("primary_location") or {}).get("source") or {}).get("display_name"),
                   cited_by_count=work.get("cited_by_count"), has_itmo_author=bool(itmo_authors))
            g.edge(rkey, "MENTIONS_PAPER", pkey, source=link.source, role=link.role, section=link.section,
                   resolved=True)

            matched_contributors = []
            for pos, a in enumerate(authorships):
                author = a.get("author") or {}
                if not author.get("id"):
                    continue
                aid = author["id"].rsplit("/", 1)[-1]
                g.node("Person", aid, name=author.get("display_name"), orcid=author.get("orcid"))
                if is_itmo_authorship(a):
                    g.node("Person", aid, is_itmo=True)
                g.edge(("Person", aid), "AUTHORED", pkey, position=pos, is_itmo=is_itmo_authorship(a))
                for c in contributors:
                    score = name_similarity(c.get("name"), author.get("display_name"))
                    if score >= SAME_AS_THRESHOLD:
                        matched_contributors.append(c["login"])
                        g.edge(("GitHubProfile", c["login"]), "POSSIBLY_SAME_AS", ("Person", aid),
                               score=round(score, 3), evidence=f"name match via {full}")

            evidence = []
            if itmo_authors:
                evidence.append("itmo_author")
            if matched_contributors:
                evidence.append("contributor_is_author")
            if link.role == "own" and evidence:
                g.edge(rkey, "IMPLEMENTS", pkey, evidence=evidence, source=link.source)
    return g


# ---------- выгрузка ----------

def _cypher_value(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def to_cypher(g: Graph) -> str:
    """Идемпотентный Cypher-скрипт (MERGE), можно выполнить в Neo4j Browser или cypher-shell."""
    lines = [f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE;"
             for label in sorted({label for label, _ in g.nodes})]
    for (label, nid), props in g.nodes.items():
        sets = ", ".join(f"n.{k} = {_cypher_value(v)}" for k, v in props.items() if k != "id")
        lines.append(f"MERGE (n:{label} {{id: {_cypher_value(nid)}}})" + (f" SET {sets};" if sets else ";"))
    for fl, fid, rel, tl, tid, props in g.edges:
        sets = ", ".join(f"r.{k} = {_cypher_value(v)}" for k, v in props.items())
        lines.append(f"MATCH (a:{fl} {{id: {_cypher_value(fid)}}}), (b:{tl} {{id: {_cypher_value(tid)}}}) "
                     f"MERGE (a)-[r:{rel}]->(b)" + (f" SET {sets};" if sets else ";"))
    return "\n".join(lines) + "\n"


def to_neo4j(g: Graph, uri: str, user: str, password: str) -> None:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(user, password))
    by_label: dict[str, list[dict]] = defaultdict(list)
    for (label, _), props in g.nodes.items():
        by_label[label].append(props)
    by_rel: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for fl, fid, rel, tl, tid, props in g.edges:
        by_rel[(fl, rel, tl)].append({"a": fid, "b": tid, "props": props})

    with driver.session() as s:
        for label, rows in by_label.items():
            s.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
            s.run(f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row", rows=rows)
        for (fl, rel, tl), rows in by_rel.items():
            s.run(f"UNWIND $rows AS row MATCH (a:{fl} {{id: row.a}}) MATCH (b:{tl} {{id: row.b}}) "
                  f"MERGE (a)-[r:{rel}]->(b) SET r += row.props", rows=rows)
    driver.close()


def to_json(g: Graph, path: Path) -> None:
    payload = {
        "nodes": [{"label": l, **p} for (l, _), p in g.nodes.items()],
        "edges": [{"from": [fl, fid], "type": rel, "to": [tl, tid], **props}
                  for fl, fid, rel, tl, tid, props in g.edges],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
