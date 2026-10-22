"""paperlink: граф "репозиторий - статья - автор" для open source организации.

  paperlink collect  --org aimclub          # GitHub: репозитории, README, CITATION.cff
  paperlink resolve                         # ссылки на статьи -> OpenAlex, контрибьюторы
  paperlink publish                         # граф -> Neo4j (если NEO4J_URI) или results/graph.cypher
  paperlink validate --org aimclub          # отчёт о полноте и подозрительных связях
  paperlink run      --org aimclub          # всё сразу
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import extract, graph, validate
from .sources import GitHub, OpenAlex, RateLimited
from .store import store_from_env

RESULTS = Path("results")


def collect(args, store) -> None:
    gh = GitHub(store)
    for org in args.org:
        repos = gh.org_repos(org)
        print(f"[collect] {org}: {len(repos)} репозиториев (без форков)")
        for i, repo in enumerate(repos, 1):
            files = gh.files(repo)
            links = extract.merge(extract.from_citation_cff(files["citation_cff"]),
                                  extract.from_readme(files["readme"]))
            store.put("paper_links", repo["full_name"], {"links": [l.__dict__ for l in links]})
            if links:
                print(f"  {i:>3}. {repo['full_name']}: {len(links)} ссылок "
                      f"({sum(l.role == 'own' for l in links)} own)")


def resolve(args, store) -> None:
    oa = OpenAlex(store)
    gh = GitHub(store)
    linked = [d for d in store.all("paper_links") if d["links"]]
    print(f"[resolve] репозиториев со ссылками: {len(linked)}")
    for doc in linked:
        for link in doc["links"]:
            oa.work(link["kind"], link["value"])
        if args.contributors:
            try:
                gh.contributors(doc["_id"], top=args.contributors)
            except RateLimited as exc:
                print(f"  ! {exc}; контрибьюторы собраны частично")
                args.contributors = 0
    print("[resolve] готово")


def publish(args, store) -> graph.Graph:
    g = graph.build(store)
    RESULTS.mkdir(exist_ok=True)
    graph.to_json(g, RESULTS / "graph.json")
    (RESULTS / "graph.cypher").write_text(graph.to_cypher(g), encoding="utf-8")
    uri = os.environ.get("NEO4J_URI")
    if uri and not args.no_neo4j:
        graph.to_neo4j(g, uri, os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))
        print(f"[publish] загружено в Neo4j {uri}: {g.count()}")
    else:
        print(f"[publish] results/graph.cypher и results/graph.json: {g.count()}")
    return g


def run_validate(args, store, g: graph.Graph | None = None) -> None:
    g = g or graph.build(store)
    md, metrics = validate.report(g, args.org or ["?"])
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "REPORT.md").write_text(md, encoding="utf-8")
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in metrics.items() if not isinstance(v, dict)}, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="paperlink", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", choices=["collect", "resolve", "publish", "validate", "run"])
    parser.add_argument("--org", action="append", default=[], help="GitHub-организация (можно несколько)")
    parser.add_argument("--contributors", type=int, default=5,
                        help="сколько топ-контрибьюторов брать для репозиториев со статьями (0 - не брать)")
    parser.add_argument("--no-neo4j", action="store_true", help="не загружать в Neo4j, только экспорт")
    args = parser.parse_args(argv)

    load_dotenv()
    store = store_from_env()
    if args.cmd in ("collect", "run") and not args.org:
        sys.exit("укажите --org")
    if args.cmd in ("collect", "run"):
        collect(args, store)
    if args.cmd in ("resolve", "run"):
        resolve(args, store)
    g = publish(args, store) if args.cmd in ("publish", "run") else None
    if args.cmd in ("validate", "run"):
        run_validate(args, store, g)


if __name__ == "__main__":
    main()
