# repo-paper-graph

Граф знаний "репозиторий - статья - автор" для open source организаций на GitHub: сбор данных через GitHub API и OpenAlex, хранение в MongoDB, граф в Neo4j и автоматический отчёт о качестве связей.

![Python](https://img.shields.io/badge/python-3.10+-ffd166?style=for-the-badge&logo=python&logoColor=black)
![Neo4j](https://img.shields.io/badge/graph-Neo4j-f89b29?style=for-the-badge&logo=neo4j&logoColor=white)
![MongoDB](https://img.shields.io/badge/storage-MongoDB-ff0f7b?style=for-the-badge&logo=mongodb&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-555555?style=for-the-badge)

## Задача

Нужно понять, какие репозитории организации реализуют научные статьи и кто их авторы. Главная сложность в том, чтобы отличить код статьи от простого упоминания: если README ссылается на статью про scikit-learn, это не значит, что репозиторий её реализует.

Проект идёт от кода к статьям: находит ссылки на статьи (DOI, arXiv) в README и `CITATION.cff`, разрешает их через OpenAlex и проверяет, реализует ли репозиторий статью. Подход дополняет пайплайны вроде [PAUK](https://github.com/ITMO-NSS-team/PAUK), которые идут от статей к коду.

## Результаты

Прогон на GitHub-организациях ИТМО [aimclub](https://github.com/aimclub) и [ITMO-NSS-team](https://github.com/ITMO-NSS-team) (22.10.2026):

| Метрика | Значение |
|---|---|
| Репозитории | 141 |
| Статьи / авторы | 37 / 211 |
| Подтверждённые связи `IMPLEMENTS` | 21 (в 16 репозиториях) |
| Отсеянные упоминания чужих работ | 15 |
| Кандидаты "GitHub-профиль = автор" | 13 |

Основные наблюдения:

- Ссылка хоть на какую-то статью есть в 22 репозиториях из 141 (16%), подтверждённая связь со своей статьёй - в 16 (11%). `CITATION.cff` не нашёлся ни в одном репозитории, все ссылки взяты из README. Поэтому поиск от кода нужно совмещать с поиском от статей.
- 15 упоминаний чужих работ (GEPA, MLAgentBench, TabReD и др.) попали в граф только как `MENTIONS_PAPER`. Наивный подход превратил бы их в ложные `IMPLEMENTS`.
- В OpenAlex найден вероятный дубль автора: Yulia Borisova и Julia Borisova записаны как разные люди, но совпадают с одним GitHub-профилем.
- Статью о FEDOT (Future Generation Computer Systems, 2021) как свою указывают три репозитория: FEDOT, веб-интерфейс FEDOT.Web и бенчмарк pytsbe. Строго говоря, реализует её только FEDOT, остальные с ней связаны. Правило по разделу "Citation" этого не различает, и это главный источник неточности `IMPLEMENTS`.

Полный отчёт: [results/REPORT.md](results/REPORT.md). Примеры Cypher-запросов: [queries.cypher](queries.cypher), их результаты: [results/neo4j_queries.txt](results/neo4j_queries.txt).

## Как отличается своя статья от чужой

Каждая найденная ссылка получает `role`:

| Role | Источник |
|---|---|
| `own` | DOI из `CITATION.cff` (preferred-citation), из разделов "Citation", "How to cite", "Публикации" или из BibTeX-блока |
| `mention` | любое другое упоминание: библиотеки, related work, датасеты |

Ребро `IMPLEMENTS` создаётся только для `own` и только при независимом подтверждении:

| Подтверждение | Условие |
|---|---|
| `itmo_author` | у статьи в OpenAlex есть автор с аффилиацией ИТМО (ROR `04txgxn49`) |
| `contributor_is_author` | имя одного из основных контрибьюторов совпадает с автором статьи, с учётом транслитерации ("Андрей Гетманов" = "Andrey Getmanov") |

## Схема графа

```text
(:Repository)-[:MENTIONS_PAPER {source, role, section}]->(:Publication | :LinkCandidate)
(:Repository)-[:IMPLEMENTS {evidence}]->(:Publication)
(:Person)-[:AUTHORED {position, is_itmo}]->(:Publication)
(:GitHubProfile)-[:CONTRIBUTED_TO {contributions}]->(:Repository)
(:GitHubProfile)-[:POSSIBLY_SAME_AS {score}]->(:Person)
(:Repository)-[:OWNED_BY]->(:GitHubProfile)
```

`POSSIBLY_SAME_AS` хранит кандидата на совпадение, а не установленный факт.

## Пайплайн

| Шаг | Что делает |
|---|---|
| `collect` | Получает репозитории организации, README и `CITATION.cff` через GitHub API. Сырые ответы сохраняются в MongoDB (или в `data/raw/`) и не изменяются. |
| `resolve` | Извлекает DOI и arXiv-ссылки с `role`, разрешает их через OpenAlex, загружает контрибьюторов для репозиториев со статьями. |
| `publish` | Загружает граф в Neo4j через `MERGE` (повторный запуск не создаёт дублей) или экспортирует в `results/graph.cypher` и `results/graph.json`. |
| `validate` | Строит отчёт: покрытие, свои статьи без подтверждения, возможные дубли авторов, упоминания чужих работ. |

## Быстрый старт

```bash
pip install -e ".[mongo,neo4j,dev]"
cp .env.example .env    # GITHUB_TOKEN и OPENALEX_API_KEY (бесплатные) снимают лимиты API, файл читается автоматически

# без баз данных: сырые данные в data/raw/, граф в results/graph.cypher
paperlink run --org aimclub --no-neo4j

# с MongoDB и Neo4j в Docker
docker compose up -d mongo neo4j
export MONGO_URI=mongodb://localhost:27017 NEO4J_URI=bolt://localhost:7687 \
       NEO4J_USER=neo4j NEO4J_PASSWORD=paperlink-dev
paperlink run --org aimclub --org ITMO-NSS-team
# Neo4j Browser: http://localhost:7474

pytest -q    # офлайн-тесты, MongoDB заменяется на mongomock
```

Шаги можно запускать по отдельности. Сырые данные сохраняются, поэтому граф перестраивается без повторных запросов к API.

## Структура проекта

```text
paperlink/
├── sources.py    # клиенты GitHub API и OpenAlex
├── store.py      # хранилище сырых данных: MongoDB или JSON
├── extract.py    # поиск DOI и arXiv в README и CITATION.cff, определение role
├── names.py      # нормализация и сравнение имён с транслитерацией
├── graph.py      # построение графа, загрузка в Neo4j, экспорт в Cypher и JSON
├── validate.py   # отчёт о полноте и подозрительных связях
└── cli.py        # командная строка
queries.cypher    # примеры запросов к графу
results/          # граф, метрики и отчёт по организациям ИТМО
tests/            # офлайн-тесты
```

## Ограничения и развитие

- Без `GITHUB_TOKEN` GitHub API даёт 60 запросов в час, поэтому в текущем прогоне контрибьюторы собраны не для всех репозиториев и сигнал `contributor_is_author` неполный.
- `role` определяется эвристиками по разделам README. `IMPLEMENTS` по сути означает "репозиторий указывает статью как свою" (см. пример с pytsbe), а свою статью вне раздела "Citation" правило пропускает (EPDE_GECCO_experiments). Следующий шаг - ручная разметка выборки для оценки precision и recall и LLM-классификатор по контексту ссылки.
- Имена сравниваются нечётко, поэтому "Ivan Petrov" и "Ivan Petrova" получают 0.96 и становятся кандидатами `POSSIBLY_SAME_AS`. Это одна из причин, почему совпадение имени хранится как кандидат, а не как факт.
- Для надёжного сопоставления людей нужны дополнительные признаки: ORCID в профиле GitHub, email в коммитах, общие репозитории.
- Возможные новые источники: Zenodo, PyPI, Papers with Code, GitLab, Gitverse.

## Лицензия

MIT
