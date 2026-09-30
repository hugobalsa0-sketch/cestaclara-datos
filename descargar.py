"""
Descarga las entradas de generar.py: el volcado de Open Food Facts (~8 GB) y
sus taxonomías. Si la descarga se corta, continúa donde se quedó.

Uso: python descargar.py <carpeta>
"""

import os
import sys
import time
import urllib.request

PARQUET = "https://huggingface.co/datasets/openfoodfacts/product-database/resolve/main/food.parquet"
TAXONOMIES = "https://static.openfoodfacts.org/data/taxonomies/{}.json"
UA = {"User-Agent": "CestaClara/1.0 (datos semanales; contacto@cestaclara.es)"}


def total_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def download(url: str, path: str) -> None:
    size = total_size(url)
    for attempt in range(30):
        done = os.path.getsize(path) if os.path.exists(path) else 0
        if done == size:
            return
        if done > size:
            os.remove(path)
            done = 0
        try:
            req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={done}-"})
            with urllib.request.urlopen(req, timeout=120) as r, open(path, "ab") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if done % (500 << 20) < (1 << 20):
                        print(f"  {done / 1e9:.1f} de {size / 1e9:.1f} GB", flush=True)
        except OSError as e:
            print(f"  corte ({e}); reintento {attempt + 1}", flush=True)
            time.sleep(min(60, 5 * (attempt + 1)))
    raise RuntimeError(f"No se pudo descargar {url}")


def main():
    folder = sys.argv[1]
    os.makedirs(folder, exist_ok=True)
    for name in ("categories", "additives", "ingredients"):
        req = urllib.request.Request(TAXONOMIES.format(name), headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r, open(os.path.join(folder, f"{name}.json"), "wb") as f:
            f.write(r.read())
    download(PARQUET, os.path.join(folder, "food.parquet"))
    print("Descarga completa")


if __name__ == "__main__":
    main()
