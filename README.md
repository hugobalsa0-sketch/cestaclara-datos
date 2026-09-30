# cestaclara-datos

Catálogo de productos alimentarios a la venta en España que usa [CestaClara](https://cesta-clara-navy.vercel.app), derivado de [Open Food Facts](https://world.openfoodfacts.org).

Cada lunes, GitHub Actions ([`.github/workflows/datos.yml`](.github/workflows/datos.yml)):

1. Descarga el volcado completo de Open Food Facts ([`descargar.py`](descargar.py)) y sus taxonomías.
2. Se queda con los productos vendidos en España que tienen nombre y categoría o nivel de procesado ([`generar.py`](generar.py)).
3. Publica `cestaclara.db.gz` (SQLite) como *release* y avisa a la web para que se actualice.

Si algo falla o salen muchos menos productos de lo normal, no se publica nada y la web sigue con los datos de la semana anterior.

## Descargar los datos

La versión más reciente siempre está en:

```
https://github.com/hugobalsa0-sketch/cestaclara-datos/releases/latest/download/cestaclara.db.gz
```

## Licencia

- **Base de datos:** [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Es una base de datos derivada de Open Food Facts y se ofrece con la misma licencia. Los contenidos individuales, bajo [Database Contents License](https://opendatacommons.org/licenses/dbcl/1-0/).
- **Fotos** (enlazadas, no incluidas): [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/deed.es), de los colaboradores de Open Food Facts.
- **Scripts:** MIT.

Los datos son colaborativos y pueden contener errores.
