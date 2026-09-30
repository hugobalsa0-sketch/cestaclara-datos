"""
Genera cestaclara.db con los productos de España de Open Food Facts.

CestaClara no consulta Open Food Facts en cada búsqueda: lee este archivo
SQLite, que se regenera cada semana (ver .github/workflows/datos.yml). La app
lo lee en src/lib/catalog.ts; si cambias el esquema, cámbialo también allí.

Entradas (se descargan con `descargar.py`):
  - food.parquet: volcado completo de Open Food Facts (Hugging Face)
  - categories.json, additives.json, ingredients.json: taxonomías, para los
    nombres en español

Uso: python generar.py --origen <carpeta con las entradas> --destino cestaclara.db

Los datos son de Open Food Facts, bajo licencia ODbL: el archivo generado es
una base de datos derivada y se ofrece con la misma licencia.
"""

import argparse
import json
import os
import re
import sqlite3
import time

import duckdb

NUTRIENTS = {"proteins": "prot", "fat": "fat", "sugars": "sugar", "salt": "salt"}


def load_names(path: str) -> dict[str, str]:
    """Etiqueta → nombre en español, para las que tienen traducción."""
    with open(path, encoding="utf-8") as f:
        taxonomy = json.load(f)
    return {tag: node["name"]["es"] for tag, node in taxonomy.items() if node.get("name", {}).get("es")}


def pick_name(names) -> str | None:
    """Nombre en español si existe; si no, el principal del envase."""
    if not names:
        return None
    by_lang = {n["lang"]: (n["text"] or "").strip() for n in names}
    for lang in ("es", "main"):
        if by_lang.get(lang):
            return by_lang[lang]
    return next((t for t in by_lang.values() if t), None)


def image_url(code: str, images, lang: str | None) -> str | None:
    """URL de la foto frontal (400 px) en el servidor de imágenes de Open Food Facts."""
    if not images:
        return None
    fronts = {img["key"]: img for img in images if img["key"] and img["key"].startswith("front_") and img["rev"]}
    img = fronts.get("front_es") or fronts.get(f"front_{lang}") or next(iter(fronts.values()), None)
    if not img:
        return None
    padded = code.rjust(13, "0") if len(code) > 8 else code
    folder = f"{padded[0:3]}/{padded[3:6]}/{padded[6:9]}/{padded[9:]}" if len(padded) > 8 else padded
    return f"https://images.openfoodfacts.org/images/products/{folder}/{img['key']}.{img['rev']}.400.jpg"


def main_ingredient(raw: str | None, lang: str | None, ingredient_names: dict[str, str]):
    """(nombre, % declarado o 100 si es el único, nº de ingredientes). Nunca el % estimado."""
    try:
        listed = [i for i in json.loads(raw or "[]") if i.get("text")]
    except json.JSONDecodeError:
        return None, None, 0
    if not listed:
        return None, None, 0
    first = listed[0]
    percent = first.get("percent")
    if not isinstance(percent, (int, float)):
        percent = 100 if len(listed) == 1 else None
    # El texto viene en el idioma de la etiqueta: si no es español, usamos la traducción.
    translated = ingredient_names.get(first.get("id", ""))
    text = translated if translated and lang != "es" else first["text"].strip()
    return text[:1].upper() + text[1:], percent, len(listed)


ESTABLISHMENT = re.compile(r"^\s*ES\s*(\d{2})\s*\.\s*(\d{3,7})\s*/\s*([A-Z]{1,2})\b")
# En las etiquetas normalizadas el mismo código aparece como "es-40-20984-to-ec".
ESTABLISHMENT_TAG = re.compile(r"^es-(\d{2})-(\d{3,7})-([a-z]{1,2})(?:-|$)")


def emb_codes_of(text: str | None, tags) -> list[str]:
    """Códigos de envasador del texto y, en formato "ES 40.20984/TO", los que solo están en las etiquetas."""
    codes = [c.strip() for c in (text or "").split(",") if c.strip()]
    for tag in tags or []:
        m = ESTABLISHMENT_TAG.match(tag)
        if m:
            codes.append(f"ES {m[1]}.{m[2]}/{m[3].upper()}")
    return list(dict.fromkeys(codes))


def establishment_keys(codes: list[str]) -> list[str]:
    """Mismo criterio que establishmentKey() en src/lib/stores.ts."""
    keys = []
    for raw in codes:
        m = ESTABLISHMENT.match(raw.upper())
        if m:
            keys.append(f"ES {m[1]}.{m[2]}/{m[3]}")
    return list(dict.fromkeys(keys))


def number(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


SCHEMA = """
CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  brands TEXT,          -- separadas por comas, tal cual
  stores TEXT,          -- JSON: etiquetas de tiendas
  nova INTEGER,
  quantity TEXT,
  grams REAL,
  emb TEXT,             -- JSON: códigos de envasador
  image TEXT,
  cats TEXT,            -- JSON: etiquetas de categoría, de general a concreta
  prot REAL, fat REAL, sugar REAL, salt REAL,
  main_text TEXT, main_pct REAL, ing_n INTEGER NOT NULL,
  additives TEXT,       -- JSON: etiquetas "en:e250"
  pop INTEGER NOT NULL  -- escaneos únicos: para ordenar por popularidad
);
CREATE TABLE names (tag TEXT PRIMARY KEY, es TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE emb (key TEXT NOT NULL, id INTEGER NOT NULL);
CREATE VIRTUAL TABLE search USING fts5(
  name, brands, cats, tags,
  content='', tokenize='unicode61 remove_diacritics 2', prefix='3'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origen", required=True)
    ap.add_argument("--destino", required=True)
    # Si salen muchos menos productos de lo normal, algo falló en el volcado:
    # mejor no publicar y que la app siga con los datos de la semana anterior.
    ap.add_argument("--minimo", type=int, default=0)
    ap.add_argument("--memoria", default="3GB")
    args = ap.parse_args()
    t0 = time.time()

    categories = load_names(os.path.join(args.origen, "categories.json"))
    additives = load_names(os.path.join(args.origen, "additives.json"))
    ingredients = load_names(os.path.join(args.origen, "ingredients.json"))

    tmp = args.destino + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)

    con = duckdb.connect()
    # Sin límite, DuckDB usa hasta el 80 % de la memoria: en un portátil lo frena todo.
    con.execute(f"SET memory_limit = '{args.memoria}'")
    con.execute("SET threads = 4")
    cur = con.execute(
        """
        SELECT code, product_name, brands, stores_tags, nova_group, quantity, product_quantity,
               emb_codes, emb_codes_tags, images, lang, categories_tags, nutriments, ingredients,
               additives_tags, coalesce(unique_scans_n, 0)
        FROM read_parquet(?)
        WHERE list_contains(countries_tags, 'en:spain') AND NOT coalesce(obsolete, false)
        """,
        [os.path.join(args.origen, "food.parquet")],
    )

    used_tags: set[str] = set()
    count = 0
    while rows := cur.fetchmany(5000):
        for (code, pnames, brands, stores, nova, quantity, pq, emb, emb_tags, images, lang, cats, nutr, ing, adds, pop) in rows:
            name = pick_name(pnames)
            cats = [c for c in (cats or []) if re.match(r"^[a-z]{2}:[a-z0-9-]+$", c)]
            # Sin nombre, o sin categoría ni nivel de procesado, el producto no sirve para comparar.
            if not code or not code.isdigit() or not name or not (cats or nova):
                continue
            nutrients = {NUTRIENTS[n["name"]]: n["100g"] for n in (nutr or []) if n["name"] in NUTRIENTS}
            main_text, main_pct, ing_n = main_ingredient(ing, lang, ingredients)
            adds = [a for a in (adds or []) if re.match(r"^en:e\d", a)]
            emb_list = emb_codes_of(emb, emb_tags)
            inserted = db.execute(
                """INSERT OR IGNORE INTO products (code, name, brands, stores, nova, quantity, grams, emb, image, cats,
                   prot, fat, sugar, salt, main_text, main_pct, ing_n, additives, pop)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    code, name, brands or None, json.dumps(stores or [], ensure_ascii=False),
                    nova if nova in (1, 2, 3, 4) else None, quantity or None, number(pq),
                    json.dumps(emb_list, ensure_ascii=False), image_url(code, images, lang),
                    json.dumps(cats), nutrients.get("prot"), nutrients.get("fat"), nutrients.get("sugar"),
                    nutrients.get("salt"), main_text, main_pct, ing_n, json.dumps(adds), pop or 0,
                ),
            )
            # El volcado a veces repite un código: nos quedamos con la primera ficha.
            if not inserted.rowcount:
                continue
            cur_id = inserted.lastrowid
            count += 1
            used_tags.update(cats)
            used_tags.update(adds)
            db.execute(
                "INSERT INTO search (rowid, name, brands, cats, tags) VALUES (?,?,?,?,?)",
                (cur_id, name, brands or "", " ".join(categories.get(c, "") for c in cats), " ".join(cats)),
            )
            for key in establishment_keys(emb_list):
                db.execute("INSERT INTO emb (key, id) VALUES (?,?)", (key, cur_id))
        print(f"  {count} productos…", flush=True)

    if count < args.minimo:
        db.close()
        os.remove(tmp)
        raise SystemExit(f"Solo {count} productos (mínimo {args.minimo}): no se publica.")

    names = {**categories, **additives}
    db.executemany("INSERT INTO names VALUES (?,?)", [(t, names[t]) for t in used_tags if t in names])
    db.execute("CREATE INDEX emb_key ON emb (key)")
    db.execute("INSERT INTO meta VALUES ('generado', ?)", (time.strftime("%Y-%m-%d"),))
    db.execute("INSERT INTO meta VALUES ('productos', ?)", (str(count),))
    db.execute("INSERT INTO search (search) VALUES ('optimize')")
    db.commit()
    db.execute("VACUUM")
    db.close()
    os.replace(tmp, args.destino)
    print(f"Listo: {count} productos en {time.time() - t0:.0f}s, {os.path.getsize(args.destino) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
