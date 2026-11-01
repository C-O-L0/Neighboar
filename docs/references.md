# References

## Data

- Housing & Development Board. *Resale flat prices based on registration date from Jan-2017 onwards* (dataset `d_8b84c4ee58e3cfc0ece0d773c8ca6abc`). data.gov.sg. https://data.gov.sg/datasets/d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view. Retrieved 26 September 2026; 241,357 rows, January 2017 to September 2026.
- Housing & Development Board. *HDB Property Information* (dataset `d_17f5382f26140b1fdae0ba2ef6239d2f`). data.gov.sg. https://data.gov.sg/datasets/d_17f5382f26140b1fdae0ba2ef6239d2f/view. Retrieved 26 September 2026; 13,357 rows.
- National Environment Agency. *Hawker Centres (GEOJSON)* (dataset `d_4a086da0a5553be1d89383cd90d07ecd`). data.gov.sg. https://data.gov.sg/datasets/d_4a086da0a5553be1d89383cd90d07ecd/view. Retrieved 26 September 2026; 129 features.
- Singapore Land Authority. *OneMap Search API* (`https://www.onemap.gov.sg/api/common/elastic/search`). Used once by `etl/geocode.py` to geocode 10,797 blocks; the result is committed in `data/cache/block_coordinates.csv`.

The three dataset files and their SHA-256 checksums are pinned in `etl/manifest.json`; `docs/import_report.md` records what was loaded from them.

## Map

- OpenStreetMap contributors. Standard map tiles, `https://tile.openstreetmap.org/`, used under the OpenStreetMap tile usage policy (https://operations.osmfoundation.org/policies/tiles/). Map data © OpenStreetMap contributors, available under the Open Database License (https://www.openstreetmap.org/copyright); every map in the app shows this attribution.

## Libraries and assets in the web pages

Vendored in `app/static/vendor/`, with their licence files and checksums (see the README there).

- Leaflet 1.9.4, interactive maps (https://leafletjs.com, BSD 2-Clause licence).
- Chart.js 4.4.7, charting library (https://www.chartjs.org, MIT licence).
- @kurkle/color 0.3.2, colour library bundled inside Chart.js (https://github.com/kurkle/color, MIT licence).
- Figtree, typeface by Erik Kennedy (https://github.com/erikdkennedy/figtree, SIL Open Font License 1.1).

## Databases and main software

Versions as pinned in `compose.yaml` and `requirements.lock`.

- PostgreSQL 18.6 (https://www.postgresql.org/docs/18/), image `postgres:18.6-bookworm`.
- MongoDB Community Server 8.3.11 (https://www.mongodb.com/docs/manual/), the project's image built on `mongodb/mongodb-community-server:8.3.11-ubi8-slim` (`db/mongo/Dockerfile`).
- FastAPI 0.143.5 and Starlette 1.8.0 (https://fastapi.tiangolo.com), Jinja2 3.1.6, Pydantic 2.14.1, Uvicorn 0.54.0.
- psycopg 3.3.6 (https://www.psycopg.org/psycopg3/) and PyMongo 4.19.0 (https://pymongo.readthedocs.io).
- pandas 3.0.6 and PyArrow 26.0.0, for cleaning and loading the data.
- Matplotlib 3.11.2, for the benchmark charts.
- pytest 9.1.1 and HTTPX 0.28.1, for the tests.
- Docker Compose (https://docs.docker.com/compose/).
