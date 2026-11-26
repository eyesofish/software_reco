# 1. Users and JWT

## Goal and why
Authenticate browser users once in Spring before entering the business services.
## Files involved and exact changes
Backend pom: managed Security/resource-server/test dependencies. New auth package: user entity/repository, BCrypt demo seeding, login/me controller, Nimbus HS256 encoder/decoder, issuer/audience/time validators, stateless security chain. application.yml: required secret, demo passwords and Agent credential. Flyway V4: app_users and nullable conversation owner FK/index. No password/token logging. Login returns access_token/token_type/expires_in/user; 30-minute tokens, UUID subject. Anonymous access only login/health/CORS. Disable H2 UI.
## Out of scope
Registration, password recovery, refresh/revocation and OAuth provider integration.
## Done condition and verification
Java tests prove login, password hashing, signature/expiry/issuer/audience rejection and protected entrypoints. Use explicit test credentials.
## Expected output and next step
Authenticated principal and persisted users ready for ownership checks.
## Risks
SSE async dispatch must not reauthenticate a committed response. Secrets must be initialized before ordinary startup.
