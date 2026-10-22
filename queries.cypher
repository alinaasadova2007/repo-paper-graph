// Полезные запросы к графу (Neo4j Browser: http://localhost:7474)

// 1. Репозитории, реализующие статьи с авторами из ИТМО
MATCH (r:Repository)-[:IMPLEMENTS]->(p:Publication)<-[:AUTHORED {is_itmo: true}]-(a:Person)
RETURN r.id AS repo, p.title AS paper, p.year AS year, count(DISTINCT a) AS itmo_authors
ORDER BY itmo_authors DESC;

// 2. Авторы ИТМО, чьи статьи чаще всего имеют открытый код
MATCH (a:Person {is_itmo: true})-[:AUTHORED]->(:Publication)<-[:IMPLEMENTS]-(r:Repository)
RETURN a.name AS author, count(DISTINCT r) AS repos ORDER BY repos DESC LIMIT 10;

// 3. Одна статья - несколько репозиториев (форки, веб-интерфейсы, бенчмарки)
MATCH (p:Publication)<-[:IMPLEMENTS]-(r:Repository)
WITH p, collect(r.id) AS repos WHERE size(repos) > 1
RETURN p.title, repos;

// 4. Проверка корректности: «своя» статья по README, но не подтверждена (кандидаты на ручную разметку)
MATCH (r:Repository)-[m:MENTIONS_PAPER {role: 'own'}]->(p:Publication)
WHERE NOT (r)-[:IMPLEMENTS]->(p)
RETURN r.id, p.title, p.has_itmo_author;

// 5. Вероятные дубли авторов в OpenAlex: один GitHub-профиль похож на несколько Person
MATCH (g:GitHubProfile)-[:POSSIBLY_SAME_AS]->(p:Person)
WITH g, collect(p.name) AS names, count(p) AS n WHERE n > 1
RETURN g.login, names;

// 6. Покрытие: доля репозиториев организации со связанной статьёй
MATCH (r:Repository)
OPTIONAL MATCH (r)-[:IMPLEMENTS]->(p:Publication)
RETURN split(r.id, '/')[0] AS org, count(DISTINCT r) AS repos,
       count(DISTINCT CASE WHEN p IS NOT NULL THEN r END) AS with_paper;
