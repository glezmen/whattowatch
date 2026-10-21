# whattowatch

Személyes, magyar nyelvű film- és sorozatajánló. A nézési előzményekből, saját és külső
értékelésekből, valamint watchlistekből tanul, és a magyarországi, a felhasználó meglévő
előfizetésein elérhető műsorokból ajánl. A részletes specifikáció: `docs/spec.md`, a terv:
`docs/plan.md`.

## Telepítés

```
uv sync
cp .env.example .env     # töltsd ki a szükséges kulcsokat
uv run wtw db upgrade
uv run wtw serve         # http://127.0.0.1:8000/health
```

## Tesztek és minőségellenőrzés

```
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
