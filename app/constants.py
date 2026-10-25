API_DESCRIPTION = """
## AMU Student Resource Management System API

The **AMU Student Resource Management System** provides high-performance, enterprise-grade backend services for academic content distribution, collaborative sharing, and secure storage management across Students, Teachers, and Administrators.

### Key Capabilities & Architecture Pillars

1. **Enterprise Identity & Auth**:
   - RFC 7519 JSON Web Token (JWT) authentication with refresh token rotation.
   - Self-service email registration with transactional verification links.
   - Secure password reset workflows with time-limited tokens.
   - Role-Based Access Control (`Student`, `Teacher`, `Administrator`).

2. **Hierarchical Content Organization**:
   - Directed Acyclic Graph (DAG) folder hierarchies with cyclic reference prevention.
   - Strict per-user storage isolation (`users/{user_id}/{folder}/...`).

3. **Storage Quota & Two-Phase Upload Protocol**:
   - Atomic reservation checking under row-level database locks.
   - Dual backend support: Supabase Cloud Storage and MinIO/S3 object store.
   - Real-time client upload progress tracking with Server-Sent Events (SSE).
   - Automated quota enforcement (1 GiB for Students, 15 GiB for Teachers).

4. **Event-Driven Storage Webhooks**:
   - Supabase Storage Webhook receiver with HMAC secret verification.
   - Automated quota commitment, checksum validation, and private resource creation upon object creation.

5. **Granular Privacy & Sharing Governance**:
   - Resources default to `PRIVATE` to protect student and faculty data.
   - Access grants with fine-grained permissions (`VIEW`, `EDIT`) and instant revocation.
   - Time-limited signed download URLs (15-minute expiry) preventing persistent URL leakage.

6. **High-Performance Search & Observability**:
   - PostgreSQL trigram and full-text tsvector search.
   - OpenTelemetry distributed tracing and structured JSON logging.
"""

TAGS_METADATA = [
    {
        "name": "Health",
        "description": "System liveness, readiness, and database connectivity diagnostic probes.",
    },
    {
        "name": "Authentication",
        "description": "User registration, JWT token generation, refresh rotation, email verification, and password recovery.",
    },
    {
        "name": "Users",
        "description": "User profile retrieval and account state verification.",
    },
    {
        "name": "Folders",
        "description": "Hierarchical directory management, DAG tree structures, cycle prevention, and content listing.",
    },
    {
        "name": "Resources",
        "description": "Academic resources cataloging, multi-versioning, metadata indexing, and secure file downloads.",
    },
    {
        "name": "Sharing",
        "description": "Granular user-to-user access grants (VIEW/EDIT) with instant revocation and audit logging.",
    },
    {
        "name": "Uploads",
        "description": "Two-phase atomic upload reservations, in-flight progress updates, and Server-Sent Events (SSE).",
    },
    {
        "name": "Storage",
        "description": "Quota inspection, presigned download link generation, and expired reservation garbage collection.",
    },
    {
        "name": "Webhooks",
        "description": "Supabase Storage event webhook receivers with HMAC secret verification and automated resource activation.",
    },
    {
        "name": "Search",
        "description": "Advanced multi-attribute search powered by PostgreSQL full-text search and trigram similarity indexing.",
    },
    {
        "name": "Admin",
        "description": "Privileged administration endpoints: user quota adjustments, system audit trails, and storage reporting.",
    },
    {
        "name": "Telemetry",
        "description": "Real-time server telemetry, latency metrics, traffic analysis, and server lifecycle monitoring.",
    },
]
