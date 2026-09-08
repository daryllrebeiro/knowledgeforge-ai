# KnowledgeForge AI — Authorized Subprocessors

KnowledgeForge AI uses third-party subprocessors to deliver cloud hosting, artificial intelligence, email transmission, and payment processing services. Prior to onboarding any subprocessor, KnowledgeForge AI conducts security reviews and enters into Data Processing Agreements incorporating the European Commission's Standard Contractual Clauses (SCCs).

## Subprocessor Directory

| Subprocessor | Purpose / Service | Data Transferred | Corporate Headquarters | Data Location |
|---|---|---|---|---|
| **Google Cloud Platform (GCP)** | Cloud infrastructure (Cloud Run, Cloud SQL PostgreSQL, Cloud Storage, Secret Manager, Pub/Sub) | Encrypted customer documents, embeddings, conversation logs, user authentication hashes | Mountain View, California, USA | Primary region: `us-central1` (or EU regions upon enterprise tenant configuration) |
| **Google AI / Gemini API** | AI foundational models (embeddings, query condensation, grounded text generation, structured document extraction) | Anonymized chunk text, queries, and document excerpts during active retrieval/extraction calls | Mountain View, California, USA | Global Google AI infrastructure (no customer data used for model training) |
| **Stripe, Inc.** | Payment processing, billing subscription management, and invoicing | Billing contact details, tenant identifier, payment card metadata (tokenized directly by Stripe), subscription status | South San Francisco, California, USA | USA / EU (PCI-DSS Level 1 certified) |
| **Postmark / Twilio SendGrid** | Transactional email delivery (email verification, password reset, account notices) | User email address, tenant name, single-use verification links | Chicago, Illinois / Denver, Colorado, USA | USA |
| **Redis / Google Cloud Memorystore** | High-performance in-memory caching (rate limiting, daily token budgets, circuit breaker states) | Transient token counts, rate-limit buckets, tenant UUID keys | Mountain View, California, USA | `us-central1` (same region as application runtime) |

## Subprocessor Notification & Changes

Tenants will be notified at least 30 days in advance of any new subprocessor additions via account administrative announcements or email notifications. Tenants may object to new subprocessors on data protection grounds by contacting `privacy@knowledgeforge.ai`.
