# IA10 Renderer Bridge (public deployment copy)

Minimal HTTP bridge for submitting Shotstack Edit API render jobs.

## Security
- No API keys or bearer tokens are stored in this repository.
- Set `SHOTSTACK_API_KEY` as a secret environment variable in Render.
- Render generates `IA10_BRIDGE_TOKEN`; use it as a bearer token when calling `POST /render`.
- `GET /health` exposes only whether required settings are present, never their values.
- This is a deployment copy; the original project repository remains private.

## Deploy on Render
1. Create a Blueprint from this repository and review `render.yaml`.
2. Add the Shotstack Sandbox API key to the `SHOTSTACK_API_KEY` secret field.
3. Deploy and verify `/health`.
4. Only test a real render after confirming the key and request payload.

## API
- `GET /health`: service and configuration status.
- `POST /render`: validates the Shotstack edit payload and queues it for the background worker; returns HTTP 202 with a job ID. Requires `Authorization: Bearer <IA10_BRIDGE_TOKEN>`.
- `POST /jobs`: same queue, with optional `title`; returns HTTP 201 with a job ID.
- `GET /jobs`: lists recent jobs (authorized).
- `GET /jobs/{id}`: checks one job (authorized).

A healthy endpoint does not mean rendering or social publishing is configured.
