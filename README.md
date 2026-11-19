# memora-python-services

Python services used by the MEMORA backend (`memora-backend`). One folder, requirements file,
Dockerfile and image per service. Services run on the Mini PC in one Compose project, joined to
the external Docker network `memora-network`, with no host ports.

| Service | Folder | Image (GHCR, private) | Caller | Contract |
|---|---|---|---|---|
| Personalization processing worker | `services/personalization_worker` | `memora-personalization-worker` | Operations (PERSO-013) | `memora-backend/docs/api/personalization/processing-worker.md` |
| LightRAG (later) | `services/lightrag` | `memora-lightrag` | Experience | to be planned |

Rules:

- The contract lives in `memora-backend`. Change it there first.
- Services are stateless and never write MEMORA databases. Storage is Garage (S3 API) with one access key per service.
- `shared/` holds technical helpers only (logging, S3 client, secret check), no business logic.
- Never commit `.env` files or secrets.
