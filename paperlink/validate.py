"""Отчёт о полноте и корректности графа: что нашлось, что подозрительно, что проверить руками."""

from __future__ import annotations

from collections import defaultdict

from .graph import Graph


def report(g: Graph, orgs: list[str]) -> tuple[str, dict]:
    repos = {nid: p for (label, nid), p in g.nodes.items() if label == "Repository"}
    pubs = {nid: p for (label, nid), p in g.nodes.items() if label == "Publication"}

    mentions = defaultdict(list)
    implements = defaultdict(list)
    unresolved = []
    own_without_evidence = []
    for fl, fid, rel, tl, tid, props in g.edges:
        if rel == "MENTIONS_PAPER":
            mentions[fid].append((tl, tid, props))
            if not props.get("resolved"):
                unresolved.append((fid, tid))
        elif rel == "IMPLEMENTS":
            implements[fid].append(tid)
    for repo, items in mentions.items():
        for tl, tid, props in items:
            if tl == "Publication" and props.get("role") == "own" and tid not in implements[repo]:
                own_without_evidence.append((repo, tid))

    foreign_mentions = [(repo, tid) for repo, items in mentions.items() for tl, tid, props in items
                        if tl == "Publication" and props.get("role") == "mention"
                        and not pubs[tid].get("has_itmo_author")]
    same_as = [e for e in g.edges if e[2] == "POSSIBLY_SAME_AS"]

    # Один GitHub-профиль похож на несколько разных Person -> вероятный дубль автора в OpenAlex.
    persons_by_profile: dict[str, set[str]] = defaultdict(set)
    for _, fid, _, _, tid, _ in same_as:
        persons_by_profile[fid].add(tid)
    person_duplicates = {login: sorted(ids) for login, ids in persons_by_profile.items() if len(ids) > 1}

    n = len(repos) or 1
    metrics = {
        "repositories": len(repos),
        "repos_with_any_paper_link": len(mentions),
        "repos_with_implements": len(implements),
        "coverage_any_link": round(len(mentions) / n, 3),
        "coverage_implements": round(len(implements) / n, 3),
        "publications": len(pubs),
        "publications_with_itmo_author": sum(1 for p in pubs.values() if p.get("has_itmo_author")),
        "unresolved_links": len(unresolved),
        "own_links_without_evidence": len(own_without_evidence),
        "foreign_mentions_not_implements": len(foreign_mentions),
        "possible_same_as": len(same_as),
        "possible_person_duplicates": len(person_duplicates),
        **g.count(),
    }

    lines = [f"# Отчёт о графе: {', '.join(orgs)}", "",
             "| метрика | значение |", "|---|---|"]
    for k, v in metrics.items():
        if not isinstance(v, dict):
            lines.append(f"| {k} | {v} |")
    lines += ["", f"Узлы: {metrics['nodes']}  ", f"Рёбра: {metrics['edges']}", ""]

    lines += ["## Репозитории, реализующие статьи (IMPLEMENTS)", ""]
    for repo, tids in sorted(implements.items()):
        for tid in tids:
            p = pubs[tid]
            lines.append(f"- **{repo}** → {p.get('title')} ({p.get('year')}), doi: {p.get('doi')}")

    lines += ["", "## Проверить вручную", "",
              "### Своя статья по README, но нет подтверждения (нет автора ИТМО и совпадений с контрибьюторами)", ""]
    for repo, tid in own_without_evidence[:40]:
        p = pubs[tid]
        lines.append(f"- {repo} → {p.get('title')} ({p.get('year')})")
    lines += ["", "### Ссылки, которые не удалось разрешить в OpenAlex", ""]
    for repo, key in unresolved[:40]:
        lines.append(f"- {repo}: `{key}`")
    lines += ["", "### Кандидаты: GitHub-профиль = автор статьи", ""]
    for fl, fid, rel, tl, tid, props in same_as[:40]:
        person = g.nodes.get((tl, tid), {})
        lines.append(f"- @{fid} ~ {person.get('name')} (score {props.get('score')})")
    lines += ["", "### Возможные дубли авторов в OpenAlex (один GitHub-профиль ~ несколько Person)", ""]
    for login, ids in person_duplicates.items():
        names = [f"{g.nodes[('Person', i)].get('name')} ({i})" for i in ids]
        lines.append(f"- @{login}: " + "; ".join(names))
    lines += ["", f"### Упоминания чужих работ без авторов ИТМО: {len(foreign_mentions)} "
              "(в граф идут только как MENTIONS_PAPER, не как IMPLEMENTS)", ""]
    for repo, tid in foreign_mentions[:15]:
        lines.append(f"- {repo} → {pubs[tid].get('title')}")

    no_links = sorted(set(repos) - set(mentions))
    lines += ["", f"## Репозитории без ссылок на статьи: {len(no_links)}", "",
              ", ".join(f"`{r.split('/')[-1]}`" for r in no_links[:80])]
    return "\n".join(lines) + "\n", metrics
