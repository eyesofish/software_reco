# 3. Internal Agent authentication

## Goal and why
Keep user authentication at the business boundary and prevent direct unauthenticated execution.
## Files involved and exact changes
Dedicated Spring Agent HTTP clients add X-API-Key for sync, SSE, session state and assets; never forward browser JWT. Redact sensitive headers. FastAPI auth rejects empty configuration instead of dev bypass. Runtime initializer generates persistent local secrets/passwords into ignored runtime configuration; start-all scripts and Compose load them consistently. FastAPI debug port binds loopback; container execution stays on the internal network.
## Out of scope
Service discovery, mutual TLS and secret rotation infrastructure.
## Done condition and verification
HTTP stub asserts the service header on every upstream route; missing/wrong key rejected; empty configuration fails closed. Startup validates configuration.
## Expected output and next step
Frontend needs only user JWT and the Spring URL.
## Risks
Do not attach credentials to unrelated hosts; ensure logs omit headers and credentials.
