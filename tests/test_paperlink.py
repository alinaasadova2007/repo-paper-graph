"""Офлайн-тесты: извлечение ссылок, сопоставление имён, сборка графа, оба хранилища."""

import mongomock

from paperlink import extract, graph, validate
from paperlink.names import canonical, name_similarity
from paperlink.store import JsonStore, MongoStore

README = """# Cool tool
Uses scikit-learn (https://doi.org/10.5555/1953048.2078195).
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.123.svg)](https://doi.org/10.5281/zenodo.123)

## Citation
```
@article{ours2023, doi = {10.1016/j.ours.2023.001}}
```
Preprint: arXiv:2401.12345
"""

CFF = """cff-version: 1.2.0
doi: 10.5281/zenodo.999
preferred-citation:
  type: article
  doi: 10.1016/J.OURS.2023.001
"""


def test_extract_roles():
    links = {l.key: l for l in extract.merge(extract.from_citation_cff(CFF), extract.from_readme(README))}
    assert links["doi:10.1016/j.ours.2023.001"].role == "own"
    assert links["arxiv:2401.12345"].role == "own"
    assert links["doi:10.5555/1953048.2078195"].role == "mention"
    assert not any("zenodo" in k for k in links)  # DOI релизов на Zenodo - шум


def test_names():
    assert canonical("Гетманов Андрей") == canonical("Andrey Getmanov")
    assert name_similarity("Николай Никитин", "Nikolay O. Nikitin") >= 0.9
    assert name_similarity("nicl-nno", "Nikolay Nikitin") < 0.9  # ник не считается совпадением
    assert name_similarity("Dreamlone", "Mikhail Sarafanov") == 0.0  # одно слово не сравниваем


def _work(itmo: bool, author="Andrey Getmanov"):
    inst = [{"ror": "https://ror.org/04txgxn49"}] if itmo else [{"ror": "https://ror.org/other"}]
    return {"id": "https://openalex.org/W1", "display_name": "Our paper", "publication_year": 2023,
            "doi": "https://doi.org/10.1016/j.ours.2023.001",
            "authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": author},
                             "institutions": inst}]}


def _fill(store, role="own", itmo=True, contributor_name=None):
    store.put("github_repos", "org/tool", {"name": "tool", "owner": {"login": "org", "type": "Organization"}})
    link = extract.PaperLink("doi", "10.1016/j.ours.2023.001", "readme", role, "Citation")
    store.put("paper_links", "org/tool", {"links": [link.__dict__]})
    store.put("openalex_works", link.key, {"work": _work(itmo)})
    items = [{"login": "dev", "type": "User", "name": contributor_name, "contributions": 10}]
    store.put("github_contributors", "org/tool", {"items": items})


def test_implements_needs_evidence(tmp_path):
    store = JsonStore(tmp_path)
    _fill(store, role="own", itmo=True)
    assert graph.build(store).count()["edges"].get("IMPLEMENTS") == 1

    store2 = JsonStore(tmp_path / "b")
    _fill(store2, role="mention", itmo=True)  # чужое упоминание, не IMPLEMENTS
    assert "IMPLEMENTS" not in graph.build(store2).count()["edges"]

    store3 = JsonStore(tmp_path / "c")
    _fill(store3, role="own", itmo=False)  # нет автора ИТМО и совпадения с контрибьютором
    g = graph.build(store3)
    assert "IMPLEMENTS" not in g.count()["edges"]
    _, metrics = validate.report(g, ["org"])
    assert metrics["own_links_without_evidence"] == 1


def test_contributor_match_in_mongo():
    store = MongoStore("mongodb://fake", client=mongomock.MongoClient())
    _fill(store, role="own", itmo=False, contributor_name="Андрей Гетманов")
    g = graph.build(store)
    assert g.count()["edges"]["POSSIBLY_SAME_AS"] == 1
    assert g.count()["edges"]["IMPLEMENTS"] == 1  # подтверждено совпадением контрибьютора с автором
    cypher = graph.to_cypher(g)
    assert "MERGE (a)-[r:IMPLEMENTS]->(b)" in cypher


def test_doi_with_parentheses_and_markdown_link():
    text = ("## Citation\nSee [the paper](https://doi.org/10.1016/j.ours.2023.001).\n"
            "Also https://doi.org/10.1016/S0140-6736(20)30183-5 here.\n")
    keys = {l.key for l in extract.from_readme(text)}
    assert keys == {"doi:10.1016/j.ours.2023.001", "doi:10.1016/s0140-6736(20)30183-5"}


def test_comment_in_code_block_is_not_heading():
    text = "## Installation\n```bash\n# Citation helper\npip install tool\n```\nBased on https://doi.org/10.5555/abc.1\n"
    [link] = extract.from_readme(text)
    assert link.role == "mention" and link.section == "Installation"
