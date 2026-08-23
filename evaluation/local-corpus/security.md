# Security

Signed JWTs carry user and tenant identity. Database retrieval receives the tenant ID
explicitly, authentication is rate limited, browser access is deny-by-default, and
responses include baseline security headers.
