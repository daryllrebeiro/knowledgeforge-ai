# KnowledgeForge AI Privacy Policy

**Effective Date:** September 8, 2026  
**Status:** Production (Active)

KnowledgeForge AI ("KnowledgeForge", "we", "us", or "our") is committed to protecting the privacy and security of your personal data. This Privacy Policy describes how we collect, use, process, store, and protect your information when you access or use the KnowledgeForge AI multi-tenant retrieval-augmented generation (RAG) platform, APIs, and associated web services (the "Service").

---

## 1. Scope & Legal Roles

- **Data Controller**: KnowledgeForge AI acts as a Data Controller with respect to account management information (e.g., user email addresses, tenant ownership, authentication credentials, billing metadata, and telemetry logs).
- **Data Processor**: With respect to customer-uploaded documents, files, conversation prompts, and domain text ("Customer Data"), KnowledgeForge AI acts as a Data Processor on behalf of the customer tenant, who remains the Data Controller. Processing of Customer Data is governed by our Data Processing Addendum (DPA).

---

## 2. Information We Collect

### A. Information Provided Directly
- **Account & Registration Details**: Email address, hashed credentials (using Argon2id), tenant name, user full name (optional).
- **Billing & Subscription Information**: Stripe customer ID, subscription tier, billing period, transaction timestamps. (Payment card details are collected and processed directly by Stripe under PCI-DSS Level 1 compliance; KnowledgeForge AI never stores raw card numbers).
- **Customer Documents & Text**: PDF, DOCX, PPTX, CSV, HTML, Markdown, and text documents uploaded for indexing, chunking, embedding, and structured extraction.
- **Query & Conversation Data**: Natural-language questions submitted to `/ask` or `/conversations`, generated answers, citations, and conversation titles.

### B. Information Generated Automatically
- **System Telemetry & Request Logs**: Request identifiers (UUIDv4), client IP addresses (used for rate-limiting enforcement), HTTP status codes, latencies, token consumption estimates.
- **Security & Budget Counters**: Rate-limiting token buckets and daily usage counts maintained in transient cache.

---

## 3. Legal Bases for Processing (GDPR Article 6)

We process personal data under the following legal bases:
1. **Performance of a Contract (Art. 6(1)(b))**: Providing the core RAG platform, executing document search, answer generation, and account authentication.
2. **Legitimate Interests (Art. 6(1)(f))**: Preventing denial-of-service and brute-force attacks via rate limiting, enforcing budget caps, detecting security anomalies, and ensuring system reliability.
3. **Legal Obligations (Art. 6(1)(c))**: Retaining billing records and tax compliance data as mandated by applicable financial regulations.
4. **Consent (Art. 6(1)(a))**: Optional marketing updates or transactional email notifications where explicit consent was provided.

---

## 4. Subprocessors and Cross-Border Transfers

We share data with vetted third-party subprocessors strictly necessary to deliver the Service. Our full subprocessor list is maintained at [docs/subprocessors.md](subprocessors.md).
- When personal data originating in the European Economic Area (EEA), United Kingdom, or Switzerland is transferred internationally, we rely on the European Commission's Standard Contractual Clauses (SCCs) and appropriate technical safeguards (such as encryption in transit and at rest with AES-256).

---

## 5. Data Retention & Deletion Policy

- **Customer Documents & Embeddings**: Retained until explicitly deleted by tenant users (`DELETE /documents/{id}`) or upon complete account removal (`DELETE /auth/account`).
- **Account Removal**: When a tenant account is deleted, our database executes a hard cascade: all documents, chunks, pgvector embeddings, API keys, invitations, conversations, and GCS storage objects are deleted immediately and permanently.
- **Operational Logs**: Request logs (`request_logs`) are retained for operational diagnosis and billing reconciliation for 90 days, after which they are purged.
- **Unverified Accounts**: User accounts that remain unverified past 30 days are purged automatically.

---

## 6. Your Rights Under GDPR & CCPA

Depending on your jurisdiction, you have the following rights:
- **Right of Access & Portability (Art. 15 & 20)**: You may request a machine-readable export of all data associated with your tenant account via `GET /auth/account/export`.
- **Right to Rectification (Art. 16)**: You may update your profile or submit human-in-the-loop corrections to extracted documents at any time.
- **Right to Erasure / "Right to be Forgotten" (Art. 17)**: You may trigger full deletion of your tenant data via `DELETE /auth/account`.
- **Right to Restriction & Objection (Art. 18 & 21)**: You may object to certain processing activities by contacting privacy officials.

To exercise any of these rights, contact us at `privacy@knowledgeforge.ai`.

---

## 7. Security Safeguards

We implement defense-in-depth measures to safeguard personal data:
- AES-256 encryption at rest for all database volumes and object storage.
- TLS 1.3 encryption in transit for all external API endpoints.
- Strict multi-tenant query isolation enforcing tenant scoping on every database query.
- Automated vulnerability scanning and dependency audits on every build.

---

## 8. Contact Information

KnowledgeForge AI Data Protection Office:  
Email: `privacy@knowledgeforge.ai`  
Security Team: `security@knowledgeforge.ai`
