document.addEventListener('DOMContentLoaded', () => {
  const projects = [
    {
      id: 'distributed-infrastructure',
      mark: 'DB',
      title: 'MongoDB High Availability & Distributed Infrastructure',
      shortDescription: 'A fault-tolerant MongoDB deployment that keeps data available through replica-set elections and controlled recovery.',
      application: 'Production services need to keep reading and writing when a database host fails. Replication and tested recovery reduce interruption without relying on a single server.',
      stack: ['MongoDB', 'Docker Compose', 'Kubernetes', 'Python', 'PyMongo', 'Prometheus'],
      capabilities: ['Three-member authenticated replica set', 'Primary election and driver reconnection tests', 'StatefulSet with persistent storage', 'Least-privilege metrics exporter'],
      status: { tested: false },
      docker: 'https://hub.docker.com/r/jovit206/lab-13',
      resources: [
        ['Technical guide', 'readme'],
        ['Kubernetes deployment guide', 'kubernetes-guide'],
        ['Failover test source', 'failover-script'],
      ],
      architecture: [
        ['Application', 'The client connects through a replica-set-aware MongoDB driver.'],
        ['MongoDB replica set', 'Three MongoDB members replicate writes and elect a primary.'],
        ['Primary', 'Accepts writes and replicates them to the other members.'],
        ['Two secondary nodes', 'Keep replicated data and can participate in a new election.'],
        ['Failover & monitoring', 'The driver reconnects after an election; a least-privilege exporter exposes metrics.'],
      ],
      architectureRows: [[0], [1], [2, 3], [4]],
      detail: {
        problem: 'A database that depends on one server can stop serving requests when that server becomes unavailable.',
        solution: 'Run three authenticated MongoDB members, verify replication, and test an election while a PyMongo client continues its read-and-write checks.',
        howItWorks: 'The application discovers the current primary from the replica set. MongoDB replicates committed writes to secondary members. If the primary steps down, eligible members elect a replacement and the client reconnects.',
        database: 'MongoDB replica set rs0 uses three members with separate persistent volumes. Majority write checks and member health are verified by the included scripts.',
        implementation: 'Docker Compose and Kubernetes configurations are provided. The Kubernetes deployment uses a headless Service, StatefulSet, and per-pod persistent volume claims. A dedicated exporter account is used for metrics.',
        testing: 'The README documents verification of one primary and two secondary members, majority writes, driver reconnection, election behavior, and pod recreation. No separate execution report is present in the repository.',
        deployment: 'Docker Compose and Kubernetes deployment definitions are present. The Docker Hub repository and version 1.0 tag were verified. The optional Atlas Terraform configuration is separate from local deployments.',
      },
    },
    {
      id: 'database-reliability',
      mark: 'OPS',
      title: 'Automated Database Monitoring, Backup & Recovery',
      shortDescription: 'A Python operations toolkit for MongoDB health metrics, scheduled backups, guarded restores and recovery verification.',
      application: 'Teams need early warning of database issues and a safe way to recover data. Monitoring and rehearsed restores help reduce uncertainty during an incident.',
      stack: ['Python', 'MongoDB', 'PyMongo', 'Docker', 'MongoDB Database Tools', 'Requests'],
      capabilities: ['Connection, cache and replication monitoring', 'Optional Slack and Discord alerts', 'Hourly timestamped backups with seven-day retention', 'Isolated restore verification and benchmarks'],
      status: { tested: true },
      docker: 'https://hub.docker.com/r/jovit206/lab-14',
      resources: [
        ['Technical guide', 'readme'],
        ['Execution report', 'execution-report'],
        ['Backup and restore benchmarks', 'benchmarks'],
        ['Recovery objectives report', 'recovery-report'],
        ['Monitoring source', 'monitoring-script'],
        ['Backup and restore source', 'backup-script'],
      ],
      architecture: [
        ['MongoDB', 'The monitoring and backup tools connect to the configured MongoDB deployment.'],
        ['Monitoring service', 'Reads server, storage-engine and replica-set health information.'],
        ['Backup service', 'Runs timestamped database dumps and records their results.'],
        ['Backup storage', 'Keeps marked backup directories and applies the configured retention policy.'],
        ['Restore verification', 'Restores into an isolated test database and checks recovered values.'],
      ],
      architectureRows: [[0], [1, 2], [3], [4]],
      detail: {
        problem: 'Without regular health checks and restore drills, a database issue or damaged backup can go unnoticed until recovery is needed.',
        solution: 'A Python service collects MongoDB health metrics, can send threshold alerts, and pairs scheduled backups with a guarded restore-verification workflow.',
        howItWorks: 'The monitoring process samples server status, WiredTiger cache information and replica-set lag. The backup process runs MongoDB Database Tools, records outcomes, prunes only its own expired backups, and can restore into a dedicated test database.',
        database: 'The tools inspect MongoDB server and replica-set status. Backup data is stored in timestamped directories with ownership markers; restore checks use an isolated verification database.',
        implementation: 'The repository includes monitoring and backup scripts, Docker packaging, a retention policy, restore verification, and optional webhook notifications. Secrets are supplied at runtime through environment variables.',
        testing: 'The execution report records 17 unit tests, live replica-set checks, backup and restore runs, and verification benchmarks at 1,000, 10,000 and 100,000 documents. Webhook delivery was not run because no endpoint credentials were supplied.',
        deployment: 'A Docker image with tag 1.0 is available. The deployment documentation describes runtime MongoDB and optional alert settings; no webhook credentials are embedded in the image.',
      },
    },
    {
      id: 'saas-backend',
      mark: 'API',
      title: 'Secure Multi-Tenant SaaS Platform',
      shortDescription: 'A FastAPI task service that scopes authenticated users to their organization and applies role-aware access controls.',
      application: 'Multiple organizations can share one application while each organization’s users access only the data allowed by their tenant and role.',
      stack: ['Python', 'FastAPI', 'MongoDB', 'PyMongo', 'JWT authentication', 'Docker'],
      capabilities: ['JWT-based sign-in and authenticated routes', 'Tenant-scoped task access', 'Admin and regular-user permissions', 'Rate limiting and validated tenant migration'],
      status: { tested: true },
      docker: 'https://hub.docker.com/r/jovit206/lab-15',
      resources: [
        ['Technical guide', 'readme'],
        ['Execution report', 'execution-report'],
        ['Architecture recommendation', 'architecture'],
        ['Migration validation report', 'migration-report'],
        ['Performance comparison', 'benchmarks'],
        ['API source', 'api-source'],
      ],
      architecture: [
        ['User', 'A person signs in and receives a token tied to their identity and tenant.'],
        ['FastAPI backend', 'The API validates requests and applies its authentication dependencies.'],
        ['JWT authentication', 'A signed token carries verified user and tenant identity.'],
        ['Tenant identification', 'The backend derives the tenant from the authenticated user, not request data.'],
        ['Role-based access', 'Tenant administrators and regular users have distinct permissions.'],
        ['MongoDB', 'Tenant-scoped queries keep records separated by organization.'],
      ],
      architectureRows: [[0], [1], [2], [3], [4], [5]],
      detail: {
        problem: 'A shared SaaS database must not allow one organization to read or change another organization’s records.',
        solution: 'The API verifies identity and derives tenant scope from the authenticated user record. Its task operations and permissions are tested against cross-tenant access.',
        howItWorks: 'Users authenticate and receive a signed token. API dependencies resolve the user and tenant, then task queries include that tenant. Administrators can manage tenant tasks; regular users are limited to their own tasks.',
        database: 'The source workflow uses a shared tasks collection with tenant-scoped queries. A migration tool can copy records into per-tenant collections, validates identifiers and boundaries, and leaves the original source collection intact.',
        implementation: 'The FastAPI application includes authentication, task routes, MongoDB-backed rate limiting, seed and migration scripts, and a Docker image. Tenant identity is not accepted as an override from task request fields.',
        testing: 'The execution report records seven passing integration tests for authentication, CRUD, tenant isolation, role checks and rate limiting. It also documents a 500-record migration validation and a measured strategy comparison.',
        deployment: 'A Docker image with tag 1.0 is available. The API requires a MongoDB connection, database name and JWT signing secret supplied at runtime.',
      },
    },
    {
      id: 'polyglot-intelligence',
      mark: 'DATA',
      title: 'Hybrid Database Architecture for Fraud Detection',
      shortDescription: 'A Python analysis service combines MongoDB transactions, Cassandra event history and Neo4j relationship paths.',
      application: 'Fraud review can require transaction details, a timeline of recent activity and links among accounts. Each data store handles a query pattern suited to its model.',
      stack: ['MongoDB', 'Apache Cassandra', 'Neo4j', 'Python', 'PyMongo', 'Cassandra Driver', 'Neo4j Python Driver', 'Docker'],
      capabilities: ['Transaction lookup and time-ordered user events', 'Bounded multi-hop account relationship analysis', 'Independent connection pools and circuit breakers', 'Partial results when a data source is unavailable'],
      status: { tested: true },
      docker: 'https://hub.docker.com/r/jovit206/lab-16',
      resources: [
        ['Technical guide', 'readme'],
        ['Execution report', 'execution-report'],
        ['Hybrid architecture document', 'architecture'],
        ['Latency comparison', 'benchmarks'],
        ['Fraud analysis source', 'analysis-source'],
        ['Circuit-breaker source', 'circuit-breaker-source'],
      ],
      architecture: [
        ['Fraud analysis request', 'The service receives a request to examine a user and transaction.'],
        ['MongoDB', 'Returns the operational transaction record.'],
        ['Apache Cassandra', 'Returns recent events ordered by user and event time.'],
        ['Neo4j', 'Traverses bounded transfer paths between connected accounts.'],
        ['Fraud analysis service', 'Combines available results and reports unavailable sources.'],
        ['Combined result', 'Returns the analysis with its completion or partial status.'],
      ],
      architectureRows: [[0], [1, 2, 3], [4], [5]],
      detail: {
        problem: 'A single data model is not ideal for transaction lookups, ordered activity histories and relationship-path queries at the same time.',
        solution: 'Use MongoDB for transaction documents, Cassandra for user event history, and Neo4j for connected-account paths; combine their results in a service that can report partial availability.',
        howItWorks: 'The analysis service queries each database through its own client. Cassandra reads a bounded recent-event partition, Neo4j traverses parameterized transfer paths with a validated hop limit, and MongoDB retrieves the transaction. The response includes failures rather than hiding them.',
        database: 'MongoDB stores transaction documents; Cassandra partitions user events by user and orders them by event time; Neo4j models users and transfers as a graph. The separate enrollment example also compares document, query-table and graph models.',
        implementation: 'The service uses pooled MongoDB, Cassandra and Neo4j connections, bounded graph queries, independent circuit breakers, and cleanup for every backend connection.',
        testing: 'The execution report records 20 passing tests, consistent query results across the three data stores, a live merged fraud-analysis run, and latency measurements. Docker image build and Compose workflow were also recorded as successful.',
        deployment: 'A Docker image with tag 1.0 is available. The deployment includes an isolated Compose configuration for the three database services and runtime configuration through environment variables.',
      },
    },
    {
      id: 'rag-pipeline',
      mark: 'RAG',
      title: 'Interactive Retrieval-Augmented Generation Pipeline',
      shortDescription: 'An admin-only document Q&A workspace that retrieves MongoDB Vector Search sources and reports each observed pipeline stage.',
      application: 'Knowledge assistants need to ground answers in approved documents, show their sources, and keep each administrator’s conversation history separate.',
      stack: ['Python', 'FastAPI', 'MongoDB Atlas Vector Search', 'Voyage AI embeddings', 'OpenAI-compatible LLM', 'PyMongo'],
      capabilities: ['100-word chunks with 20-word overlap', 'Server-side embedding and LLM providers', 'Source-backed answers and retrieved chunk previews', 'Administrator-scoped conversation memory', 'Correlated retrieval, generation and storage events'],
      status: { tested: false, label: 'Configuration required' },
      docker: null,
      interactive: 'rag',
      resources: [
        ['Technical guide', 'readme'],
        ['RAG API source', 'api-source'],
        ['RAG service source', 'service-source'],
      ],
      architecture: [
        ['Administrator', 'An authenticated administrator uploads documents or asks a question in the dashboard.'],
        ['FastAPI RAG service', 'The service validates the request and separates each administrator’s conversation history.'],
        ['Voyage AI', 'The backend requests embeddings using server-side credentials and validates vector dimensions.'],
        ['MongoDB Atlas Vector Search', 'The service retrieves relevant stored document chunks through a configured vector index.'],
        ['OpenAI-compatible LLM', 'The backend passes the question, prior conversation and retrieved source context to the configured model.'],
        ['Cited response', 'The dashboard displays the measured answer, retrieved sources, and correlated stage outcomes.'],
      ],
      architectureRows: [[0], [1], [2, 3], [4], [5]],
      detail: {
        problem: 'A question-answering interface without retrieval, source previews and isolated conversation memory cannot demonstrate where an answer came from or protect conversations from crossing administrator boundaries.',
        solution: 'The service chunks uploaded text, creates Voyage embeddings, stores document vectors in MongoDB, retrieves matching chunks with Vector Search, and asks a configured chat-completion provider to answer from that context.',
        howItWorks: 'Uploads are split into 100-word windows with 20-word overlap. The app persists chunks and vectors, and an administrator creates a MongoDB Vector Search index after the first successful embedding. A question runs through query embedding, vector retrieval, conversation-memory lookup and LLM generation; each completed stage is correlated in System Activity.',
        database: 'The rag_demo database stores document chunks and vectors in documents. Conversation messages and metadata are scoped by the authenticated administrator user_id and conversation_id. Embedding model and actual dimensions are stored with each embedded chunk.',
        implementation: 'Voyage AI and an OpenAI-compatible chat-completion endpoint are called by the backend only. Provider keys are read from runtime environment configuration, never returned to the browser or written into operational events. Source chunks are shown with the answer.',
        testing: 'RAG availability depends on an embedding key, an LLM key and a MongoDB deployment that supports Vector Search. The workspace reports missing configuration and unsupported database capabilities instead of showing invented answers or measurements.',
        deployment: 'Configure VOYAGE_API_KEY, RAG_LLM_API_KEY (or OPENAI_API_KEY), and an HTTPS RAG_LLM_API_URL on the backend. Create the Vector Search index from the workspace after adding documents. The local replica set does not automatically imply Atlas Vector Search support.',
      },
    },
    {
      id: 'vector-search-benchmark',
      mark: 'VEC',
      title: 'HNSW & IVF Vector Search Benchmark',
      shortDescription: 'A progress-reporting benchmark workspace for comparing measured MongoDB ANN, ENN and exact-search behavior on a real 5,000-record dataset.',
      application: 'Vector-search choices should be based on measured latency and recall for the target data and query workload—not on unverified configuration claims.',
      stack: ['MongoDB Atlas Vector Search', 'Python', 'Sentence Transformers', 'all-MiniLM-L6-v2', 'NumPy', 'PyMongo'],
      capabilities: ['Admin JSONL upload with exactly 5,000 records', '384-dimensional embeddings generated in 500-record batches', '100-query benchmark with top-10 recall and latency distributions', 'ANN compared with database ENN and local exact cosine search', 'Unsupported IVF and custom HNSW parameters reported explicitly'],
      status: { tested: false, label: 'Configuration required' },
      docker: null,
      interactive: 'benchmark',
      resources: [
        ['Technical guide', 'readme'],
        ['Benchmark API source', 'api-source'],
        ['Benchmark service source', 'service-source'],
        ['Optional benchmark dependencies', 'optional-requirements'],
      ],
      architecture: [
        ['Administrator JSONL upload', 'The dashboard validates a UTF-8 dataset of exactly 5,000 text and source records.'],
        ['Sentence Transformers', 'The backend generates and validates all-MiniLM-L6-v2 vectors at 384 dimensions.'],
        ['MongoDB batch storage', 'Each successful batch of up to 500 records is persisted under a new dataset ID.'],
        ['MongoDB Atlas index', 'The app requests its managed Vector Search index and observes readiness through the Atlas index listing.'],
        ['ANN, ENN and local exact', 'A deterministic sample of 100 queries measures ANN latency, ENN latency, exact cosine top-k and recall.'],
        ['Measured results', 'The dashboard reports only completed run data and identifies configurations the service cannot run.'],
      ],
      architectureRows: [[0], [1], [2], [3], [4], [5]],
      detail: {
        problem: 'Benchmark results are misleading when they use fabricated data, do not check exact neighbors, mix index algorithms, or report custom index settings that the target database does not expose.',
        solution: 'The admin workspace accepts a real JSONL dataset, embeds exactly 5,000 records with all-MiniLM-L6-v2, stores 384-dimensional vectors in 500-record batches, then compares managed MongoDB ANN with MongoDB ENN and a local exact cosine baseline.',
        howItWorks: 'After validating the uploaded data, a background job reports embedding and insertion progress. Once the dataset is complete, the administrator requests a vector index. A benchmark run samples 100 deterministic queries and measures top-10 recall and latency against local exact results. Results appear only after a run completes.',
        database: 'A new dataset ID scopes its records in rag_demo.vector_benchmark_documents. Earlier datasets are retained. The currently active dataset pointer changes only after all 5,000 vectors are stored and validated.',
        implementation: 'The implementation uses bounded JSONL uploads, optional Sentence Transformers dependencies, 500-record MongoDB insert batches, duplicate-job protection and progress polling. MongoDB-managed ANN is measured as ANN; IVF and user-controlled HNSW M / efConstruction variants are reported as unsupported.',
        testing: 'No benchmark numbers are present until the optional model dependencies are installed, a real dataset is uploaded, a MongoDB Atlas Vector Search index becomes queryable, and a run finishes. Index build duration is reported as the first-observed readiness interval, not an exact server-side build metric.',
        deployment: 'Install the vector-search requirements file in the backend image to enable the embedding job. MongoDB Atlas Vector Search is required for ANN and ENN database measurements. The application preserves prior dataset records and does not fabricate unsupported IVF results.',
      },
    },
    {
      id: 'comparative-threat-intelligence',
      mark: 'CTI',
      title: 'Multi-Database Threat Intelligence Comparison',
      shortDescription: 'Compare MongoDB, Cassandra and Neo4j using one normalized threat dataset, with analyst review and measured query benchmarks.',
      application: 'Analysts can review source-provided threat data, inspect explicit relationships and compare database query behavior without treating shared indicators as proof.',
      stack: ['MongoDB', 'Apache Cassandra', 'Neo4j', 'FastAPI', 'Pydantic', 'Docker'],
      capabilities: ['Idempotent normalized imports', 'Four measured query patterns with ten repetitions', 'Explicit-link investigations and analyst decisions', 'Availability-aware database comparison'],
      status: { tested: false, label: 'Configuration required' },
      docker: null,
      interactive: 'chapter18',
      resources: [
        ['Setup guide and report', 'readme'],
        ['Cassandra schema', 'cassandra-schema'],
        ['Neo4j schema', 'neo4j-schema'],
        ['Optional database dependencies', 'optional-requirements'],
      ],
      architecture: [
        ['Normalized threat records', 'Validated records preserve source provenance, UTC time and explicit relationship identifiers.'],
        ['MongoDB', 'Stores the isolated threat intelligence document collection, import history, reviews and benchmark history.'],
        ['Apache Cassandra', 'Stores query tables partitioned by source, type, target, indicator and day.'],
        ['Neo4j', 'Stores threats and their explicit source-provided relationship edges.'],
        ['FastAPI comparison service', 'Runs bounded queries, reports unavailable adapters and records only observed timings.'],
        ['Analyst workspace', 'Presents records, evidence and review decisions for authenticated administrators.'],
      ],
      architectureRows: [[0], [1, 2, 3], [4], [5]],
      detail: {
        problem: 'Threat records need document retrieval, time-series access and relationship exploration; comparisons are only useful when they use the same data and honest measurements.',
        solution: 'Imported records are normalized once and written to isolated threat intelligence structures in each configured database.',
        howItWorks: 'Administrators import validated JSON records, inspect adapter readiness, query records, build bounded investigations from explicit related-threat identifiers, and run four database operations ten times each.',
        database: 'MongoDB and Cassandra use isolated threat-intelligence namespaces; Neo4j stores threat labels and relationships. Existing application records remain separate.',
        implementation: 'The API is administrator-only. Adapter failures are returned as unavailable or not tested. Similar indicators and transaction IDs are evidence for review, never automatic false-positive classifications.',
        testing: 'Live timings are generated only by database operations. A missing service or schema is reported without fabricated latency. Integration requires the matching database service and credentials.',
        deployment: 'See the deployment guide for optional database configuration and container build arguments. Cassandra and Neo4j drivers must be available in the backend image.',
      },
    },
  ];

  const technologies = [
    { name: 'MongoDB', mark: 'M', category: 'database', description: 'Document database for operational records and flexible application data.', projects: ['distributed-infrastructure', 'database-reliability', 'saas-backend', 'polyglot-intelligence', 'rag-pipeline', 'vector-search-benchmark', 'comparative-threat-intelligence'] },
    { name: 'MongoDB Atlas Vector Search', mark: 'V', category: 'database', description: 'Managed vector retrieval and exact-neighbor comparison for embedding search.', projects: ['rag-pipeline', 'vector-search-benchmark'] },
    { name: 'Apache Cassandra', mark: 'C*', category: 'database', description: 'Distributed wide-column store for partitioned, time-ordered event histories.', projects: ['polyglot-intelligence', 'comparative-threat-intelligence'] },
    { name: 'Neo4j', mark: 'N', category: 'database', description: 'Graph database for connected accounts and relationship paths.', projects: ['polyglot-intelligence', 'comparative-threat-intelligence'] },
    { name: 'Python', mark: 'Py', description: 'Backend language for automation, APIs, monitoring and data services.', projects: ['distributed-infrastructure', 'database-reliability', 'saas-backend', 'polyglot-intelligence', 'rag-pipeline', 'vector-search-benchmark'] },
    { name: 'FastAPI', mark: 'F', description: 'Python web framework for authenticated REST APIs.', projects: ['saas-backend'] },
    { name: 'Docker', mark: 'D', description: 'Container packaging and local orchestration for services and databases.', projects: ['distributed-infrastructure', 'database-reliability', 'saas-backend', 'polyglot-intelligence'] },
    { name: 'Kubernetes', mark: 'K8s', description: 'Orchestration for stateful database members and persistent storage.', projects: ['distributed-infrastructure'] },
    { name: 'JWT', mark: 'JWT', description: 'Signed tokens for user authentication and identity claims.', projects: ['saas-backend'] },
    { name: 'PyMongo', mark: 'PyM', description: 'MongoDB driver used by Python services and verification tools.', projects: ['distributed-infrastructure', 'database-reliability', 'saas-backend', 'polyglot-intelligence', 'rag-pipeline', 'vector-search-benchmark'] },
    { name: 'Prometheus', mark: 'P', description: 'Metrics collection and monitoring integration for MongoDB infrastructure.', projects: ['distributed-infrastructure'] },
    { name: 'Voyage AI', mark: 'VAI', description: 'Server-side embedding provider used by the RAG document and query flow.', projects: ['rag-pipeline'] },
    { name: 'Sentence Transformers', mark: 'ST', description: 'Local sentence embedding model used by the vector benchmark dataset job.', projects: ['vector-search-benchmark'] },
    { name: 'NumPy', mark: 'np', description: 'Exact cosine-neighbor baseline and benchmark calculations.', projects: ['vector-search-benchmark'] },
  ];

  const journey = [
    ['Distributed Infrastructure', 'Building reliable database systems with replication, failover and orchestration.'],
    ['Database Reliability', 'Developing monitoring, automated backups and recovery mechanisms.'],
    ['Backend & SaaS Architecture', 'Building secure APIs, multi-tenant applications and access-control systems.'],
    ['Multi-Database Intelligence', 'Combining database technologies to support complex data analysis and fraud detection.'],
    ['Retrieval-Augmented Generation', 'Grounding assistant responses in retrieved documents with traceable sources.'],
    ['Vector Search Evaluation', 'Comparing measured approximate retrieval against exact search baselines.'],
  ];

  const byId = id => document.getElementById(id);
  const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
  const projectFor = id => projects.find(project => project.id === id);
  const documentUrl = (project, resource) => `/portfolio/docs/${encodeURIComponent(project.id)}/${encodeURIComponent(resource)}`;

  function renderStats() {
    const databaseCount = technologies.filter(technology => technology.category === 'database').length;
    byId('portfolio-stat-grid').innerHTML = [
      ['Capabilities', projects.length, 'Documented and interactive capabilities'],
      ['Technology stack', technologies.length, 'Across platform capabilities'],
      ['Database technologies', databaseCount, 'Document, wide-column and graph'],
      ['Engineering domains', journey.length, 'Reliability through data intelligence'],
    ].map(([label, value, note]) => `
      <article class="portfolio-stat">
        <span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small>
      </article>
    `).join('');
  }

  function renderProjectCards() {
    byId('portfolio-project-grid').innerHTML = projects.map(project => `
      <article class="portfolio-project-card" data-portfolio-card="${escapeHtml(project.id)}" tabindex="0" aria-label="Open ${escapeHtml(project.title)}">
        <div class="portfolio-card-topline"><span class="portfolio-project-mark">${escapeHtml(project.mark)}</span><span class="portfolio-status${project.status.label ? ' portfolio-status-config' : ''}">${escapeHtml(project.status.label || (project.status.tested ? 'Tested' : 'Implemented'))}</span></div>
        <h3>${escapeHtml(project.title)}</h3>
        <p class="portfolio-project-summary">${escapeHtml(project.shortDescription)}</p>
        <div class="portfolio-stack" aria-label="Technology stack">${project.stack.map(name => `<span>${escapeHtml(name)}</span>`).join('')}</div>
        <div class="portfolio-card-section"><h4>Key capabilities</h4><ul>${project.capabilities.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>
        <p class="portfolio-application"><strong>Why it matters</strong>${escapeHtml(project.application)}</p>
        <div class="portfolio-badges">
          <span class="portfolio-verified">Documentation available</span>
          ${project.status.tested ? '<span class="portfolio-verified">Tested</span>' : ''}
          ${project.docker ? `<a class="portfolio-verified portfolio-badge-link" href="${escapeHtml(project.docker)}" target="_blank" rel="noopener noreferrer">Docker image 1.0 available <span aria-hidden="true">↗</span></a>` : ''}
        </div>
        <div class="portfolio-card-actions">
          <button type="button" class="portfolio-primary-button" data-portfolio-project="${escapeHtml(project.id)}">View details <span aria-hidden="true">→</span></button>
          <a href="${documentUrl(project, 'readme')}" class="portfolio-text-link">Technical documentation</a>
        </div>
      </article>
    `).join('');
  }

  function renderTechnologies() {
    byId('portfolio-tech-grid').innerHTML = technologies.map(technology => {
      const relatedProjects = technology.projects.map(projectFor).filter(Boolean);
      return `
        <button type="button" class="portfolio-tech-card" data-portfolio-project="${escapeHtml(relatedProjects[0].id)}" aria-label="Explore ${escapeHtml(technology.name)} in ${escapeHtml(relatedProjects[0].title)}">
          <span class="portfolio-tech-mark" aria-hidden="true">${escapeHtml(technology.mark)}</span>
          <span class="portfolio-tech-name">${escapeHtml(technology.name)}</span>
          <span class="portfolio-tech-description">${escapeHtml(technology.description)}</span>
          <span class="portfolio-tech-projects"><strong>Used in</strong>${relatedProjects.map(project => escapeHtml(project.title)).join(' · ')}</span>
          <span class="portfolio-tech-open">View a related capability <span aria-hidden="true">→</span></span>
        </button>
      `;
    }).join('');
  }

  function renderJourney() {
    byId('portfolio-timeline').innerHTML = journey.map(([title, description]) => `
      <li class="portfolio-timeline-item">
        <span class="portfolio-timeline-dot" aria-hidden="true"></span>
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(description)}</p>
      </li>
    `).join('');
  }

  function renderResources() {
    byId('portfolio-resource-grid').innerHTML = projects.map(project => `
      <article class="portfolio-resource-card">
        <div><span class="portfolio-kicker">TECHNICAL RESOURCES</span><h3>${escapeHtml(project.title)}</h3></div>
        <div class="portfolio-resource-links">
          ${project.resources.map(([label, resource]) => `<a href="${documentUrl(project, resource)}">${escapeHtml(label)} <span aria-hidden="true">↗</span></a>`).join('')}
          ${project.docker ? `<a href="${escapeHtml(project.docker)}" target="_blank" rel="noopener noreferrer">Docker Hub image 1.0 <span aria-hidden="true">↗</span></a>` : ''}
        </div>
      </article>
    `).join('');
  }

  function renderArchitecture(project) {
    return `
      <div class="portfolio-architecture" aria-label="${escapeHtml(project.title)} system architecture">
        <div class="portfolio-architecture-flow">
          ${project.architectureRows.map((row, rowIndex) => `
            ${rowIndex ? '<span class="portfolio-architecture-arrow" aria-hidden="true">↓</span>' : ''}
            <div class="portfolio-architecture-row">
              ${row.map(index => `
                <button type="button" class="portfolio-architecture-node${index === 0 ? ' is-selected' : ''}" data-architecture-node="${index}" aria-pressed="${index === 0}">
                  <span class="portfolio-node-mark" aria-hidden="true">◇</span>
                  <strong>${escapeHtml(project.architecture[index][0])}</strong>
                  <span>View component details</span>
                </button>
              `).join('')}
            </div>
          `).join('')}
        </div>
        <p class="portfolio-architecture-description" aria-live="polite">${escapeHtml(project.architecture[0][1])}</p>
      </div>
    `;
  }

  function renderProject(project) {
    const detail = byId('portfolio-project');
    detail.dataset.projectId = project.id;
    const simpleSections = [
      ['Overview', escapeHtml(project.shortDescription)],
      ['Operational Challenge', escapeHtml(project.detail.problem)],
      ['Solution', escapeHtml(project.detail.solution)],
      ['How It Works', escapeHtml(project.detail.howItWorks)],
      ['Key Features', `<ul>${project.capabilities.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`],
      ['Real-World Applications', escapeHtml(project.application)],
    ];
    const advancedSections = [
      ['Database Design', project.detail.database],
      ['Implementation Details', project.detail.implementation],
      ['Testing & Performance', project.detail.testing],
      ['Deployment Information', project.detail.deployment],
    ];

    detail.innerHTML = `
      <button type="button" class="portfolio-back-button" data-portfolio-back>← All capabilities</button>
      <header class="portfolio-detail-header">
        <span class="portfolio-kicker">PLATFORM CAPABILITY</span>
        <h1 tabindex="-1">${escapeHtml(project.title)}</h1>
        <p>${escapeHtml(project.shortDescription)}</p>
        <div class="portfolio-badges"><span class="portfolio-verified${project.status.label ? ' portfolio-status-config' : ''}">${escapeHtml(project.status.label || (project.status.tested ? 'Tested' : 'Implemented'))}</span><span class="portfolio-verified">Documentation available</span>${project.status.tested ? '<span class="portfolio-verified">Tested</span>' : ''}</div>
      </header>
      <section class="portfolio-detail-section"><div class="portfolio-section-heading"><div><span class="portfolio-kicker">SYSTEM OVERVIEW</span><h2>Architecture</h2></div></div>${renderArchitecture(project)}</section>
      <div class="portfolio-detail-grid">${simpleSections.map(([title, content]) => `
        <article class="portfolio-detail-card"><h2>${escapeHtml(title)}</h2><div>${content}</div></article>
      `).join('')}</div>
      <section class="portfolio-detail-section">
        <div class="portfolio-section-heading"><div><span class="portfolio-kicker">IMPLEMENTATION DETAILS</span><h2>Technical details</h2></div><span class="portfolio-section-note">Expand a section for implementation context</span></div>
        <div class="portfolio-advanced-list">${advancedSections.map(([title, content]) => `
          <details class="portfolio-advanced"><summary>${escapeHtml(title)}</summary><div>${escapeHtml(content)}</div></details>
        `).join('')}</div>
      </section>
      <section class="portfolio-detail-section">
        <div class="portfolio-section-heading"><div><span class="portfolio-kicker">TOOLS USED</span><h2>Technologies</h2></div></div>
        <div class="portfolio-stack">${project.stack.map(name => `<span>${escapeHtml(name)}</span>`).join('')}</div>
      </section>
      <section class="portfolio-detail-section">
        <div class="portfolio-section-heading"><div><span class="portfolio-kicker">SOURCE MATERIAL</span><h2>Documentation &amp; Deployment</h2></div></div>
        <div class="portfolio-detail-resources">
          ${project.resources.map(([label, resource]) => `<a href="${documentUrl(project, resource)}">${escapeHtml(label)} <span aria-hidden="true">↗</span></a>`).join('')}
          ${project.docker ? `<a href="${escapeHtml(project.docker)}" target="_blank" rel="noopener noreferrer">Docker Hub image 1.0 <span aria-hidden="true">↗</span></a>` : ''}
        </div>
      </section>
      ${project.interactive ? `<section class="portfolio-detail-section vector-project-section" aria-label="Interactive ${escapeHtml(project.title)} workspace"><div id="vector-project-workspace" data-vector-project="${escapeHtml(project.interactive)}"><p class="ops-empty">Loading capability configuration and live capabilities…</p></div></section>` : ''}
    `;
    byId('portfolio-dashboard').hidden = true;
    detail.hidden = false;
    detail.querySelector('.portfolio-detail-header h1').focus();
    detail.scrollIntoView({ block: 'start' });
    if (project.interactive) {
      document.dispatchEvent(new CustomEvent('portfolio:project-opened', {
        detail: { id: project.interactive },
      }));
    }
  }

  function showDashboard() {
    byId('portfolio-project').hidden = true;
    byId('portfolio-dashboard').hidden = false;
    document.dispatchEvent(new CustomEvent('portfolio:project-closed'));
    byId('portfolio-title').focus();
  }

  byId('portfolio-title').setAttribute('tabindex', '-1');
  renderStats();
  renderProjectCards();
  renderTechnologies();
  renderJourney();
  renderResources();

  byId('view-portfolio').addEventListener('click', event => {
    const card = event.target.closest('[data-portfolio-card]');
    if (card && !event.target.closest('a, button')) {
      const project = projectFor(card.dataset.portfolioCard);
      if (project) renderProject(project);
      return;
    }

    const projectButton = event.target.closest('[data-portfolio-project]');
    if (projectButton) {
      const project = projectFor(projectButton.dataset.portfolioProject);
      if (project) renderProject(project);
      return;
    }

    if (event.target.closest('[data-portfolio-back]')) {
      showDashboard();
      return;
    }

    const architectureNode = event.target.closest('[data-architecture-node]');
    if (architectureNode) {
      const project = projectFor(byId('portfolio-project').dataset.projectId);
      const index = Number(architectureNode.dataset.architectureNode);
      if (!project || !project.architecture[index]) return;
      byId('portfolio-project').querySelectorAll('[data-architecture-node]').forEach(node => {
        const selected = node === architectureNode;
        node.classList.toggle('is-selected', selected);
        node.setAttribute('aria-pressed', String(selected));
      });
      byId('portfolio-project').querySelector('.portfolio-architecture-description').textContent = project.architecture[index][1];
    }
  });

  byId('view-portfolio').addEventListener('keydown', event => {
    const card = event.target.closest('[data-portfolio-card]');
    if (!card || event.target !== card || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    const project = projectFor(card.dataset.portfolioCard);
    if (project) renderProject(project);
  });
});
