import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { fetchHealthStatus } from "./api/health";
import {
  AnswerCitation,
  Company,
  DocumentChunk,
  Filing,
  FilingSection,
  FilingSectionSummary,
  FinancialFact,
  Job,
  ResearchRunEvidence,
  ResearchRunResponse,
  ResearchRunStep,
  fetchCompany,
  fetchCompanyFilings,
  fetchCompanyMetrics,
  fetchFilingChunks,
  fetchFilingSection,
  fetchFilingSections,
  fetchJob,
  generateCompanyEmbeddings,
  ingestCompany,
  loadCompanyMetrics,
  parseFiling,
  runResearch,
} from "./api/sec";
import "./styles.css";

const CORE_METRIC_LABELS: Record<string, string> = {
  revenue: "Revenue",
  gross_profit: "Gross profit",
  operating_income: "Operating income",
  net_income: "Net income",
  operating_cash_flow: "Operating cash flow",
  capital_expenditures: "Capital expenditures",
  free_cash_flow: "Free cash flow",
  gross_margin: "Gross margin",
  operating_margin: "Operating margin",
  net_margin: "Net margin",
};

const CORE_METRIC_ORDER = Object.keys(CORE_METRIC_LABELS);
const DEFAULT_METRIC_KEY = CORE_METRIC_ORDER[0];
const DEFAULT_DETAIL_LIMIT = 12;
const EVIDENCE_MARKER_PATTERN =
  /\[((?:chunk|span|financial_fact|metric_observation|metric_comparison):[^\]\s]+)\]/g;

type AppView = "filings" | "metrics" | "research";

type MetricGroup = {
  key: string;
  label: string;
  facts: FinancialFact[];
};

type MetricSummary = {
  fact: FinancialFact | null;
  key: string;
  label: string;
  total: number;
};

function getViewTitle(view: AppView): string {
  if (view === "metrics") {
    return "XBRL Metrics";
  }
  if (view === "research") {
    return "Research";
  }
  return "Filing Explorer";
}

function isActiveJob(job: Job | null): boolean {
  return job !== null && (job.status === "pending" || job.status === "running");
}

function formatPageRange(start: number | null, end: number | null): string {
  if (start === null || end === null) {
    return "n/a";
  }

  return start === end ? `${start}` : `${start}-${end}`;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected error";
}

function getHighlightedSourceUrl(filingId: number, chunkId: number): string {
  return `/filings/${filingId}/chunks/${chunkId}/source`;
}

function formatMetricValue(fact: FinancialFact): string {
  const value = Number(fact.value);
  if (!Number.isFinite(value)) {
    return fact.value;
  }

  if (fact.unit === "ratio") {
    return `${(value * 100).toFixed(2)}%`;
  }

  if (fact.unit === "USD") {
    return new Intl.NumberFormat("en-US", {
      currency: "USD",
      maximumFractionDigits: 2,
      notation: "compact",
      style: "currency",
    }).format(value);
  }

  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
    notation: "compact",
  }).format(value);
}

function formatDecimalValue(value: string): string {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return value;
  }

  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
    notation: "compact",
  }).format(numericValue);
}

function formatGrowthRate(value: string | null): string {
  if (value === null) {
    return "n/a";
  }

  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return value;
  }

  return `${(numericValue * 100).toFixed(1)}%`;
}

function formatMetricPeriod(fact: FinancialFact): string {
  return fact.period_start === null
    ? `Period ended ${fact.period_end}`
    : `${fact.period_start} to ${fact.period_end}`;
}

function formatMetricFilingContext(fact: FinancialFact): string | null {
  const fiscalLabel = [fact.fact_fiscal_year ? `FY${fact.fact_fiscal_year}` : null, fact.fiscal_period]
    .filter(Boolean)
    .join(" ");
  const sourceFiscalLabel =
    fact.source_fiscal_year && fact.source_fiscal_year !== fact.fact_fiscal_year
      ? `source FY${fact.source_fiscal_year}`
      : null;
  const filedLabel = fact.filed_date ? `filed ${fact.filed_date}` : null;
  const context = [fiscalLabel ? `period ${fiscalLabel}` : null, sourceFiscalLabel, filedLabel]
    .filter(Boolean)
    .join(", ");

  return context || null;
}

function getSelectedStep(
  steps: ResearchRunStep[],
  selectedStepId: string | null,
): ResearchRunStep | null {
  return (
    steps.find((step) => step.step_id === selectedStepId) ??
    steps[0] ??
    null
  );
}

function getStepEvidence(
  evidence: ResearchRunEvidence[],
  step: ResearchRunStep | null,
): ResearchRunEvidence[] {
  if (!step) {
    return [];
  }
  const ids = new Set(step.evidence_ids);
  return evidence.filter((item) => ids.has(item.evidence_id));
}

function formatRunDuration(durationMs: number | null): string {
  if (durationMs === null) {
    return "n/a";
  }
  if (durationMs < 1000) {
    return `${Math.round(durationMs)} ms`;
  }
  return `${(durationMs / 1000).toFixed(2)} s`;
}

function compareFactsByRecentPeriod(first: FinancialFact, second: FinancialFact): number {
  const periodEndCompare = second.period_end.localeCompare(first.period_end);
  if (periodEndCompare !== 0) {
    return periodEndCompare;
  }

  const periodStartCompare = (second.period_start ?? "").localeCompare(first.period_start ?? "");
  if (periodStartCompare !== 0) {
    return periodStartCompare;
  }

  const filedDateCompare = (second.filed_date ?? "").localeCompare(first.filed_date ?? "");
  if (filedDateCompare !== 0) {
    return filedDateCompare;
  }

  return second.id - first.id;
}

function groupMetrics(metrics: FinancialFact[]): MetricGroup[] {
  const metricMap = new Map<string, FinancialFact[]>();
  for (const metric of metrics) {
    const group = metricMap.get(metric.canonical_metric_key) ?? [];
    group.push(metric);
    metricMap.set(metric.canonical_metric_key, group);
  }

  const knownGroups = CORE_METRIC_ORDER.map((metricKey) => ({
    key: metricKey,
    label: CORE_METRIC_LABELS[metricKey],
    facts: [...(metricMap.get(metricKey) ?? [])].sort(compareFactsByRecentPeriod),
  }));
  const extraGroups = [...metricMap.entries()]
    .filter(([metricKey]) => !(metricKey in CORE_METRIC_LABELS))
    .sort(([firstKey], [secondKey]) => firstKey.localeCompare(secondKey))
    .map(([metricKey, facts]) => ({
      key: metricKey,
      label: metricKey,
      facts: [...facts].sort(compareFactsByRecentPeriod),
    }));

  return [...knownGroups, ...extraGroups];
}

export function App() {
  const [apiStatus, setApiStatus] = useState("checking");
  const [activeView, setActiveView] = useState<AppView>("filings");
  const [selectedMetricKey, setSelectedMetricKey] = useState(DEFAULT_METRIC_KEY);
  const [showAllMetricFacts, setShowAllMetricFacts] = useState(false);
  const [ticker, setTicker] = useState("AAPL");
  const [company, setCompany] = useState<Company | null>(null);
  const [filings, setFilings] = useState<Filing[]>([]);
  const [selectedFilingId, setSelectedFilingId] = useState<number | null>(null);
  const [sections, setSections] = useState<FilingSectionSummary[]>([]);
  const [selectedSectionId, setSelectedSectionId] = useState<number | null>(null);
  const [sectionDetail, setSectionDetail] = useState<FilingSection | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [metrics, setMetrics] = useState<FinancialFact[]>([]);
  const [ingestJob, setIngestJob] = useState<Job | null>(null);
  const [parseJob, setParseJob] = useState<Job | null>(null);
  const [xbrlJob, setXbrlJob] = useState<Job | null>(null);
  const [embeddingJob, setEmbeddingJob] = useState<Job | null>(null);
  const [researchQuestion, setResearchQuestion] = useState(
    "Why did revenue grow last quarter?",
  );
  const [researchRun, setResearchRun] = useState<ResearchRunResponse | null>(
    null,
  );
  const [selectedRunStepId, setSelectedRunStepId] = useState<string | null>(null);
  const [isLoadingCompany, setIsLoadingCompany] = useState(false);
  const [isLoadingMetrics, setIsLoadingMetrics] = useState(false);
  const [isLoadingParsedData, setIsLoadingParsedData] = useState(false);
  const [isAsking, setIsAsking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollingTimers = useRef<number[]>([]);

  const selectedFiling = useMemo(
    () => filings.find((filing) => filing.id === selectedFilingId) ?? null,
    [filings, selectedFilingId],
  );

  useEffect(() => {
    let isMounted = true;

    fetchHealthStatus()
      .then((health) => {
        if (isMounted) {
          setApiStatus(health.status);
        }
      })
      .catch(() => {
        if (isMounted) {
          setApiStatus("unavailable");
        }
      });

    return () => {
      isMounted = false;
      pollingTimers.current.forEach((timerId) => window.clearTimeout(timerId));
    };
  }, []);

  async function loadCompany(nextTicker = ticker) {
    const normalizedTicker = nextTicker.trim().toUpperCase();
    if (!normalizedTicker) {
      setError("Ticker must not be empty.");
      return;
    }

    setIsLoadingCompany(true);
    setError(null);
    setMessage(null);

    try {
      const loadedCompany = await fetchCompany(normalizedTicker);
      const [loadedFilings, loadedMetrics] = await Promise.all([
        fetchCompanyFilings(loadedCompany.ticker),
        fetchCompanyMetrics(loadedCompany.ticker),
      ]);
      setTicker(loadedCompany.ticker);
      setCompany(loadedCompany);
      setFilings(loadedFilings);
      setMetrics(loadedMetrics);
      setSelectedFilingId(loadedFilings[0]?.id ?? null);
      setSections([]);
      setSelectedSectionId(null);
      setSectionDetail(null);
      setChunks([]);
      setResearchRun(null);
      setSelectedRunStepId(null);
      if (loadedFilings[0]) {
        await loadParsedData(loadedFilings[0].id);
      }
    } catch (loadError) {
      setCompany(null);
      setFilings([]);
      setSelectedFilingId(null);
      setSections([]);
      setSelectedSectionId(null);
      setSectionDetail(null);
      setChunks([]);
      setMetrics([]);
      setResearchRun(null);
      setSelectedRunStepId(null);
      setError(getErrorMessage(loadError));
    } finally {
      setIsLoadingCompany(false);
    }
  }

  async function loadMetrics(nextTicker = ticker) {
    const normalizedTicker = nextTicker.trim().toUpperCase();
    if (!normalizedTicker) {
      setError("Ticker must not be empty.");
      return;
    }

    setIsLoadingMetrics(true);
    setError(null);

    try {
      setMetrics(await fetchCompanyMetrics(normalizedTicker));
    } catch (metricsError) {
      setMetrics([]);
      setError(getErrorMessage(metricsError));
    } finally {
      setIsLoadingMetrics(false);
    }
  }

  async function loadParsedData(filingId: number, preferredSectionId?: number) {
    setIsLoadingParsedData(true);
    setError(null);

    try {
      const loadedSections = await fetchFilingSections(filingId);
      setSections(loadedSections);
      const nextSectionId = preferredSectionId ?? loadedSections[0]?.id ?? null;
      setSelectedSectionId(nextSectionId);

      if (nextSectionId === null) {
        setSectionDetail(null);
        setChunks([]);
        return;
      }

      const [loadedSection, loadedChunks] = await Promise.all([
        fetchFilingSection(filingId, nextSectionId),
        fetchFilingChunks(filingId, nextSectionId),
      ]);
      setSectionDetail(loadedSection);
      setChunks(loadedChunks);
    } catch (parsedDataError) {
      setSections([]);
      setSelectedSectionId(null);
      setSectionDetail(null);
      setChunks([]);
      setError(getErrorMessage(parsedDataError));
    } finally {
      setIsLoadingParsedData(false);
    }
  }

  function pollJob(
    jobId: number,
    setJob: (job: Job) => void,
    onSucceeded: (job: Job) => Promise<void>,
  ) {
    const tick = async () => {
      try {
        const nextJob = await fetchJob(jobId);
        setJob(nextJob);

        if (nextJob.status === "succeeded") {
          await onSucceeded(nextJob);
          return;
        }

        if (nextJob.status === "failed") {
          setError(nextJob.error_message ?? "Job failed.");
          return;
        }

        const timerId = window.setTimeout(tick, 1200);
        pollingTimers.current.push(timerId);
      } catch (pollError) {
        setError(getErrorMessage(pollError));
      }
    };

    const timerId = window.setTimeout(tick, 800);
    pollingTimers.current.push(timerId);
  }

  async function handleCompanySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await loadCompany();
  }

  async function handleIngest() {
    const normalizedTicker = ticker.trim().toUpperCase();
    if (!normalizedTicker) {
      setError("Ticker must not be empty.");
      return;
    }

    setError(null);
    setMessage(null);

    try {
      const job = await ingestCompany(normalizedTicker, true);
      setIngestJob(job);
      pollJob(job.id, setIngestJob, async () => {
        setMessage(`SEC metadata loaded for ${normalizedTicker}.`);
        await loadCompany(normalizedTicker);
      });
    } catch (ingestError) {
      setError(getErrorMessage(ingestError));
    }
  }

  async function handleLoadMetrics() {
    if (!company) {
      setError("Load a stored company first.");
      return;
    }

    setError(null);
    setMessage(null);

    try {
      const job = await loadCompanyMetrics(company.ticker);
      setXbrlJob(job);
      pollJob(job.id, setXbrlJob, async () => {
        setMessage(`XBRL metrics loaded for ${company.ticker}.`);
        await loadMetrics(company.ticker);
      });
    } catch (metricsError) {
      setError(getErrorMessage(metricsError));
    }
  }

  async function handleGenerateEmbeddings() {
    if (!company) {
      setError("Load a stored company first.");
      return;
    }

    setError(null);
    setMessage(null);

    try {
      const job = await generateCompanyEmbeddings(company.ticker);
      setEmbeddingJob(job);
      pollJob(job.id, setEmbeddingJob, async (completedJob) => {
        const totalChunks = Number(completedJob.payload.total_chunks ?? 0);
        const embeddedCount = Number(completedJob.payload.embedded_count ?? 0);
        const skippedCount = Number(completedJob.payload.skipped_count ?? 0);
        const staleUpdatedCount = Number(completedJob.payload.stale_updated_count ?? 0);
        setMessage(
          `Chunk embeddings ready for ${company.ticker}: ${embeddedCount} new, ${staleUpdatedCount} refreshed, ${skippedCount} unchanged across ${totalChunks} chunks.`,
        );
      });
    } catch (embeddingError) {
      setError(getErrorMessage(embeddingError));
    }
  }

  async function handleRetrieveEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!company) {
      setError("Load a stored company first.");
      return;
    }

    const question = researchQuestion.trim();
    if (!question) {
      setError("Research question must not be empty.");
      return;
    }

    setIsAsking(true);
    setError(null);
    setMessage(null);

    try {
      const nextRun = await runResearch({
        ticker: company.ticker,
        question,
      });
      setResearchRun(nextRun);
      setSelectedRunStepId(nextRun.steps[0]?.step_id ?? null);
    } catch (retrievalError) {
      setResearchRun(null);
      setSelectedRunStepId(null);
      setError(getErrorMessage(retrievalError));
    } finally {
      setIsAsking(false);
    }
  }

  async function handleParse(refresh = false) {
    if (!selectedFiling) {
      setError("Select a filing first.");
      return;
    }

    setError(null);
    setMessage(null);

    try {
      const job = await parseFiling(selectedFiling.id, refresh);
      setParseJob(job);
      pollJob(job.id, setParseJob, async () => {
        setMessage(`${selectedFiling.form_type} parsed with sec2md.`);
        await loadParsedData(selectedFiling.id);
      });
    } catch (parseError) {
      setError(getErrorMessage(parseError));
    }
  }

  async function handleSelectFiling(filing: Filing) {
    setSelectedFilingId(filing.id);
    setSections([]);
    setSelectedSectionId(null);
    setSectionDetail(null);
    setChunks([]);
    await loadParsedData(filing.id);
  }

  async function handleSelectSection(section: FilingSectionSummary) {
    if (!selectedFiling) {
      return;
    }

    setSelectedSectionId(section.id);
    setError(null);
    setIsLoadingParsedData(true);

    try {
      const [loadedSection, loadedChunks] = await Promise.all([
        fetchFilingSection(selectedFiling.id, section.id),
        fetchFilingChunks(selectedFiling.id, section.id),
      ]);
      setSectionDetail(loadedSection);
      setChunks(loadedChunks);
    } catch (sectionError) {
      setError(getErrorMessage(sectionError));
    } finally {
      setIsLoadingParsedData(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Equity Research Copilot</p>
          <h1>{getViewTitle(activeView)}</h1>
        </div>
        <nav className="view-switcher" aria-label="Workspace views">
          <button
            className={activeView === "filings" ? "view-switcher__item--active" : ""}
            type="button"
            onClick={() => setActiveView("filings")}
          >
            Filings
          </button>
          <button
            className={activeView === "metrics" ? "view-switcher__item--active" : ""}
            type="button"
            onClick={() => setActiveView("metrics")}
          >
            Metrics
          </button>
          <button
            className={activeView === "research" ? "view-switcher__item--active" : ""}
            type="button"
            onClick={() => setActiveView("research")}
          >
            Research
          </button>
        </nav>
        <div className="status-row" aria-live="polite">
          <span className={`status-dot status-dot--${apiStatus}`} />
          <span>Backend: {apiStatus}</span>
        </div>
      </header>

      <section
        className={`workspace-grid ${
          activeView === "metrics" || activeView === "research"
            ? "workspace-grid--metrics"
            : ""
        }`}
      >
        <aside className="sidebar">
          <form className="ticker-form" onSubmit={handleCompanySubmit}>
            <label htmlFor="ticker-input">Ticker</label>
            <div className="ticker-row">
              <input
                id="ticker-input"
                value={ticker}
                onChange={(event) => setTicker(event.target.value)}
                maxLength={16}
                autoCapitalize="characters"
              />
              <button type="submit" disabled={isLoadingCompany}>
                {isLoadingCompany ? "Loading" : "Load Stored"}
              </button>
            </div>
          </form>

          <button
            className="full-button"
            type="button"
            onClick={handleIngest}
            disabled={isActiveJob(ingestJob)}
          >
            {isActiveJob(ingestJob) ? "Fetching" : "Fetch SEC Metadata"}
          </button>

          <button
            className="full-button full-button--secondary"
            type="button"
            onClick={handleLoadMetrics}
            disabled={!company || isActiveJob(xbrlJob)}
          >
            {isActiveJob(xbrlJob) ? "Loading Metrics" : "Load XBRL Metrics"}
          </button>

          <button
            className="full-button full-button--secondary"
            type="button"
            onClick={handleGenerateEmbeddings}
            disabled={!company || isActiveJob(embeddingJob)}
          >
            {isActiveJob(embeddingJob) ? "Embedding Chunks" : "Generate Embeddings"}
          </button>

          {company && (
            <dl className="company-facts">
              <div>
                <dt>Company</dt>
                <dd>{company.name}</dd>
              </div>
              <div>
                <dt>CIK</dt>
                <dd>{company.cik}</dd>
              </div>
              <div>
                <dt>Exchange</dt>
                <dd>{company.exchange ?? "n/a"}</dd>
              </div>
            </dl>
          )}

          {ingestJob && <JobStatus job={ingestJob} />}
          {parseJob && <JobStatus job={parseJob} />}
          {xbrlJob && <JobStatus job={xbrlJob} />}
          {embeddingJob && <JobStatus job={embeddingJob} />}
          {message && <p className="notice notice--success">{message}</p>}
          {error && <p className="notice notice--error">{error}</p>}
        </aside>

        {activeView === "filings" ? (
          <>
            <section className="filings-panel" aria-labelledby="filings-heading">
              <div className="panel-header">
                <h2 id="filings-heading">Filings</h2>
                <span>{filings.length}</span>
              </div>
              <div className="filing-list">
                {filings.map((filing) => (
                  <button
                    className={`filing-item ${
                      filing.id === selectedFilingId ? "filing-item--active" : ""
                    }`}
                    key={filing.id}
                    type="button"
                    onClick={() => handleSelectFiling(filing)}
                  >
                    <span className="filing-form">{filing.form_type}</span>
                    <span>{filing.filing_date}</span>
                    <span>{filing.accession_number}</span>
                  </button>
                ))}
                {filings.length === 0 && <p className="empty-state">No filings loaded.</p>}
              </div>
            </section>

            <section className="reader-panel" aria-labelledby="reader-heading">
              <div className="panel-header">
                <div>
                  <h2 id="reader-heading">
                    {selectedFiling
                      ? `${selectedFiling.form_type} filed ${selectedFiling.filing_date}`
                      : "Selected Filing"}
                  </h2>
                  {selectedFiling && (
                    <p className="muted">{selectedFiling.accession_number}</p>
                  )}
                </div>
                <div className="action-row">
                  {selectedFiling?.sec_primary_document_url && (
                    <a
                      href={selectedFiling.sec_primary_document_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Source
                    </a>
                  )}
                  <button
                    type="button"
                    onClick={() => handleParse(false)}
                    disabled={!selectedFiling || isActiveJob(parseJob)}
                  >
                    Parse
                  </button>
                  <button
                    type="button"
                    onClick={() => handleParse(true)}
                    disabled={!selectedFiling || isActiveJob(parseJob)}
                  >
                    Refresh Parse
                  </button>
                </div>
              </div>

              <div className="section-layout">
                <nav className="section-list" aria-label="Filing sections">
                  {sections.map((section) => (
                    <button
                      className={`section-item ${
                        section.id === selectedSectionId ? "section-item--active" : ""
                      }`}
                      key={section.id}
                      type="button"
                      onClick={() => handleSelectSection(section)}
                    >
                      <span>{section.item ?? section.section_key}</span>
                      <small>{section.title ?? "Untitled"}</small>
                      <small>Pages {formatPageRange(section.start_page, section.end_page)}</small>
                    </button>
                  ))}
                  {sections.length === 0 && (
                    <p className="empty-state">
                      {isLoadingParsedData ? "Loading sections." : "No parsed sections."}
                    </p>
                  )}
                </nav>

                <article className="section-reader">
                  <div className="section-reader-header">
                    <div>
                      <h3>{sectionDetail?.title ?? sectionDetail?.section_key ?? "Section"}</h3>
                      {sectionDetail && (
                        <p className="muted">
                          {sectionDetail.item ?? sectionDetail.section_key} | Pages{" "}
                          {formatPageRange(sectionDetail.start_page, sectionDetail.end_page)} |{" "}
                          {sectionDetail.token_count} tokens
                        </p>
                      )}
                    </div>
                  </div>

                  <pre className="markdown-view">
                    {sectionDetail?.markdown_text ?? "Select or parse a filing to view section text."}
                  </pre>

                  <div className="chunk-panel">
                    <div className="panel-header panel-header--compact">
                      <h3>Chunks</h3>
                      <span>{chunks.length}</span>
                    </div>
                    <div className="chunk-list">
                      {chunks.map((chunk) => (
                        <div className="chunk-item" key={chunk.id}>
                          <div className="chunk-meta">
                            <span>#{chunk.chunk_index}</span>
                            <span>{chunk.token_count} tokens</span>
                            <span>Pages {formatPageRange(chunk.start_page, chunk.end_page)}</span>
                            {chunk.has_table && <span>Table</span>}
                          </div>
                          <p>{chunk.chunk_text.slice(0, 420)}</p>
                          <div className="chunk-actions">
                            <a
                              href={getHighlightedSourceUrl(chunk.filing_id, chunk.id)}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Highlighted Source
                            </a>
                          </div>
                          <div className="chunk-meta chunk-meta--subtle">
                            <span>{chunk.element_ids.length} elements</span>
                            <span>{chunk.xbrl_tags.length} tags</span>
                            <span>
                              Offsets {chunk.source_start_offset ?? "n/a"}-
                              {chunk.source_end_offset ?? "n/a"}
                            </span>
                          </div>
                        </div>
                      ))}
                      {chunks.length === 0 && <p className="empty-state">No chunks loaded.</p>}
                    </div>
                  </div>
                </article>
              </div>
            </section>
          </>
        ) : activeView === "metrics" ? (
          <FinancialMetricsPage
            metrics={metrics}
            isLoading={isLoadingMetrics}
            selectedMetricKey={selectedMetricKey}
            showAllMetricFacts={showAllMetricFacts}
            onSelectMetric={(metricKey) => {
              setSelectedMetricKey(metricKey);
              setShowAllMetricFacts(false);
            }}
            onToggleShowAll={() => setShowAllMetricFacts((current) => !current)}
          />
        ) : (
          <ResearchPage
            ticker={company?.ticker ?? ticker.trim().toUpperCase()}
            hasCompany={company !== null}
            question={researchQuestion}
            run={researchRun}
            selectedStepId={selectedRunStepId}
            isAsking={isAsking}
            onQuestionChange={setResearchQuestion}
            onSelectStep={setSelectedRunStepId}
            onSubmit={handleRetrieveEvidence}
          />
        )}
      </section>
    </main>
  );
}

function ResearchPage({
  ticker,
  hasCompany,
  question,
  run,
  selectedStepId,
  isAsking,
  onQuestionChange,
  onSelectStep,
  onSubmit,
}: {
  ticker: string;
  hasCompany: boolean;
  question: string;
  run: ResearchRunResponse | null;
  selectedStepId: string | null;
  isAsking: boolean;
  onQuestionChange: (question: string) => void;
  onSelectStep: (stepId: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
}) {
  const plan = run?.plan;
  const citationNumberById = useMemo(
    () => buildCitationNumberMap(run?.citations ?? []),
    [run?.citations],
  );
  const showLimitations = run !== null && run.limitations.length > 0;
  const keepLimitationsOpen = run?.validation_status !== "passed";
  const selectedStep = getSelectedStep(run?.steps ?? [], selectedStepId);
  const selectedEvidence = getStepEvidence(run?.evidence ?? [], selectedStep);

  return (
    <section className="research-page" aria-labelledby="research-heading">
      <div className="research-page__header">
        <div>
          <h2 id="research-heading">Cited Q&A</h2>
          <p className="muted">
            {hasCompany ? `Grounded answers for ${ticker}` : "Load a stored company first"}
          </p>
        </div>
        {run && (
          <span className={`validation-pill validation-pill--${run.validation_status}`}>
            {run.validation_status}
          </span>
        )}
      </div>

      <form className="research-form" onSubmit={onSubmit}>
        <label htmlFor="research-question">Question</label>
        <textarea
          id="research-question"
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          rows={3}
          disabled={!hasCompany || isAsking}
        />
        <button type="submit" disabled={!hasCompany || isAsking}>
          {isAsking ? "Asking" : "Ask Question"}
        </button>
      </form>

      {run === null ? (
        <p className="empty-state">
          {hasCompany
            ? "Ask a question to generate a cited, validator-checked answer."
            : "Load a company, parse filings, load metrics, and generate embeddings before retrieval."}
        </p>
      ) : (
        <div className="research-results">
          <div className="research-audit-grid">
            <section className="answer-panel" aria-labelledby="answer-heading">
              <div className="panel-header panel-header--compact">
                <h3 id="answer-heading">Answer</h3>
                <span className={`validation-pill validation-pill--${run.validation_status}`}>
                  {run.validation_status}
                </span>
              </div>
              <CitedAnswer
                answer={run.answer}
                citationNumberById={citationNumberById}
                citations={run.citations}
              />
              {showLimitations && (
                <details className="limitation-details" open={keepLimitationsOpen}>
                  <summary>Limitations ({run.limitations.length})</summary>
                  <div className="limitation-list">
                    {run.limitations.map((limitation) => (
                      <span key={limitation}>{limitation}</span>
                    ))}
                  </div>
                </details>
              )}
              {run.validation.errors.length > 0 && (
                <details className="validation-debug">
                  <summary>Validation details</summary>
                  <div className="trace-warning-list">
                    {run.validation.errors.map((issue) => (
                      <span key={`${issue.code}:${issue.evidence_id ?? issue.sentence ?? ""}`}>
                        {issue.code}: {issue.message}
                      </span>
                    ))}
                  </div>
                </details>
              )}
            </section>

            <section className="trace-panel" aria-labelledby="trace-heading">
              <div className="panel-header panel-header--compact">
                <h3 id="trace-heading">Agent Trace</h3>
                <span>{run.steps.length} steps</span>
              </div>
              <div className="trace-list">
                {run.steps.map((step) => (
                  <button
                    className={`trace-step ${
                      step.step_id === selectedStep?.step_id ? "trace-step--active" : ""
                    }`}
                    key={step.step_id}
                    type="button"
                    onClick={() => onSelectStep(step.step_id)}
                  >
                    <span>
                      {step.phase} | {step.status} | {formatRunDuration(step.duration_ms)}
                    </span>
                    <strong>{step.name}</strong>
                    <small>{step.summary}</small>
                    {step.degraded_reason && <small>{step.degraded_reason}</small>}
                  </button>
                ))}
                {run.steps.length === 0 && <p className="empty-state">No trace steps returned.</p>}
              </div>
            </section>

            <section className="evidence-panel" aria-labelledby="run-evidence-heading">
              <div className="panel-header panel-header--compact">
                <h3 id="run-evidence-heading">
                  Step Evidence
                </h3>
                <span>{selectedEvidence.length}</span>
              </div>
              <EvidenceCards evidence={selectedEvidence} />
            </section>
          </div>

          <section className="research-card" aria-labelledby="citations-heading">
            <div className="panel-header panel-header--compact">
              <h3 id="citations-heading">Citations</h3>
              <span>{run.citations.length}</span>
            </div>
            {run.citations.length > 0 ? (
              <div className="citation-list">
                {run.citations.map((citation) => (
                  <CitationCard
                    citation={citation}
                    key={citation.evidence_id}
                    number={citationNumberById.get(citation.evidence_id) ?? 0}
                  />
                ))}
              </div>
            ) : (
              <p className="metric-empty">No validated citations</p>
            )}
          </section>

          {plan && (
            <section className="research-card" aria-labelledby="plan-heading">
              <div className="panel-header panel-header--compact">
                <h3 id="plan-heading">Planner Output</h3>
                <span>{plan.planner_source}</span>
              </div>
              <div className="research-summary-grid">
                <SummaryTile label="Type" value={plan.question_type} />
                <SummaryTile label="Time" value={plan.time_scope} />
                <SummaryTile label="Basis" value={plan.comparison_basis} />
                <SummaryTile label="Source" value={plan.planner_source} />
              </div>
              <PillRow label="Metrics" values={plan.metric_keys} emptyValue="No metrics" />
              <PillRow label="Sections" values={plan.target_sections} emptyValue="No section filter" />
              <PillRow label="Forms" values={plan.forms} emptyValue="All forms" />
              {plan.ambiguities.length > 0 && (
                <p className="research-warning">{plan.ambiguities.join(" ")}</p>
              )}
            </section>
          )}

          <details className="diagnostics-panel">
            <summary>Diagnostics</summary>
            <pre>
              {JSON.stringify(run.diagnostics, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
}

function CitedAnswer({
  answer,
  citationNumberById,
  citations,
}: {
  answer: string;
  citationNumberById: Map<string, number>;
  citations: AnswerCitation[];
}) {
  const citationById = useMemo(
    () => new Map(citations.map((citation) => [citation.evidence_id, citation])),
    [citations],
  );
  const paragraphs = answer.split(/\n+/).map((paragraph) => paragraph.trim()).filter(Boolean);

  return (
    <div className="answer-text">
      {paragraphs.map((paragraph, index) => (
        <p key={`${paragraph.slice(0, 32)}:${index}`}>
          {renderAnswerWithCitationRefs(paragraph, citationNumberById, citationById)}
        </p>
      ))}
    </div>
  );
}

function EvidenceCards({ evidence }: { evidence: ResearchRunEvidence[] }) {
  if (evidence.length === 0) {
    return <p className="empty-state">No evidence attached to this step.</p>;
  }

  return (
    <div className="run-evidence-list">
      {evidence.map((item) => (
        <article className="run-evidence-card" key={item.evidence_id}>
          <div className="chunk-meta">
            <span>{item.evidence_type}</span>
            <span>{item.role}</span>
            {item.form_type && <span>{item.form_type}</span>}
          </div>
          <h4>{item.title}</h4>
          {item.text && <p>{item.text}</p>}
          <div className="chunk-meta chunk-meta--subtle">
            {item.period && <span>{item.period}</span>}
            {item.section && <span>{item.section}</span>}
            {item.filing_date && <span>{item.filing_date}</span>}
          </div>
          {item.sec_url && (
            <a href={item.sec_url} target="_blank" rel="noreferrer">
              SEC Source
            </a>
          )}
        </article>
      ))}
    </div>
  );
}

function renderAnswerWithCitationRefs(
  text: string,
  citationNumberById: Map<string, number>,
  citationById: Map<string, AnswerCitation>,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;

  for (const match of text.matchAll(EVIDENCE_MARKER_PATTERN)) {
    const marker = match[0];
    const evidenceId = match[1];
    const start = match.index ?? 0;
    if (start > cursor) {
      nodes.push(text.slice(cursor, start));
    }

    const number = citationNumberById.get(evidenceId);
    const citation = citationById.get(evidenceId);
    if (number !== undefined && citation !== undefined) {
      nodes.push(
        <button
          aria-label={`Source ${number}: ${citation.source_label ?? citation.evidence_type}`}
          className="citation-ref"
          key={`${evidenceId}:${start}`}
          onClick={() => scrollToCitation(evidenceId)}
          title={`${citation.source_label ?? citation.evidence_type} | ${citation.form_type ?? "source"}`}
          type="button"
        >
          [{number}]
        </button>,
      );
    } else {
      nodes.push(
        <span className="citation-ref citation-ref--unknown" key={`${marker}:${start}`} title={evidenceId}>
          [?]
        </span>,
      );
    }
    cursor = start + marker.length;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

function buildCitationNumberMap(citations: AnswerCitation[]): Map<string, number> {
  return new Map(citations.map((citation, index) => [citation.evidence_id, index + 1]));
}

function scrollToCitation(evidenceId: string): void {
  document.getElementById(citationDomId(evidenceId))?.scrollIntoView({
    behavior: "smooth",
    block: "center",
  });
}

function citationDomId(evidenceId: string): string {
  return `citation-${evidenceId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function SummaryTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="summary-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PillRow({
  label,
  values,
  emptyValue,
}: {
  label: string;
  values: string[];
  emptyValue: string;
}) {
  return (
    <div className="pill-row">
      <span>{label}</span>
      <div>
        {(values.length > 0 ? values : [emptyValue]).map((value) => (
          <small key={value}>{value}</small>
        ))}
      </div>
    </div>
  );
}

function CitationCard({ citation, number }: { citation: AnswerCitation; number: number }) {
  const chunkId = getNumericSourceId(citation, "chunk_id");
  const filingId = getNumericSourceId(citation, "filing_id");

  return (
    <article className="citation-card" id={citationDomId(citation.evidence_id)}>
      <div className="evidence-card__top">
        <span>Source {number || "n/a"}</span>
        <strong>{formatCitationType(citation.evidence_type)}</strong>
      </div>
      <h4>{citation.source_label ?? citation.section ?? "Citation"}</h4>
      {citation.text && <p>{citation.text}</p>}
      <div className="evidence-meta">
        {citation.form_type && <span>{citation.form_type}</span>}
        {citation.filing_date && <span>{citation.filing_date}</span>}
        {citation.section && <span>{citation.section}</span>}
        <span>Pages {citation.pages ?? "n/a"}</span>
      </div>
      {citation.sec_url && (
        <a href={citation.sec_url} target="_blank" rel="noreferrer">
          SEC Source
        </a>
      )}
      {filingId !== null && chunkId !== null && (
        <a href={getHighlightedSourceUrl(filingId, chunkId)} target="_blank" rel="noreferrer">
          Highlighted Source
        </a>
      )}
    </article>
  );
}

function formatCitationType(type: string): string {
  return type
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function getNumericSourceId(citation: AnswerCitation, key: string): number | null {
  const value = citation.source_ids[key];
  return typeof value === "number" ? value : null;
}

function FinancialMetricsPage({
  metrics,
  isLoading,
  selectedMetricKey,
  showAllMetricFacts,
  onSelectMetric,
  onToggleShowAll,
}: {
  metrics: FinancialFact[];
  isLoading: boolean;
  selectedMetricKey: string;
  showAllMetricFacts: boolean;
  onSelectMetric: (metricKey: string) => void;
  onToggleShowAll: () => void;
}) {
  const metricGroups = groupMetrics(metrics);
  const availableCount = metricGroups.filter((group) => group.facts.length > 0).length;
  const selectedGroup =
    metricGroups.find((group) => group.key === selectedMetricKey) ?? metricGroups[0];
  const latestFacts: MetricSummary[] = metricGroups.map((group) => ({
    fact: group.facts[0] ?? null,
    key: group.key,
    label: group.label,
    total: group.facts.length,
  }));
  const visibleFacts = showAllMetricFacts
    ? selectedGroup.facts
    : selectedGroup.facts.slice(0, DEFAULT_DETAIL_LIMIT);
  const hiddenFactsCount = Math.max(0, selectedGroup.facts.length - visibleFacts.length);

  return (
    <section className="metrics-page" aria-labelledby="metrics-heading">
      <div className="metrics-page__header">
        <div>
          <h2 id="metrics-heading">Financial Metrics</h2>
          <p className="muted">
            {availableCount} metrics available | {metrics.length} facts
          </p>
        </div>
      </div>

      {metrics.length === 0 ? (
        <p className="empty-state">
          {isLoading ? "Loading metrics." : "No XBRL metrics loaded."}
        </p>
      ) : (
        <>
          <div className="metric-summary-grid" aria-label="Latest metric values">
            {latestFacts.map(({ fact, key, label, total }) => (
              <button
                className={`metric-summary-card ${
                  key === selectedGroup.key ? "metric-summary-card--active" : ""
                }`}
                key={key}
                type="button"
                onClick={() => onSelectMetric(key)}
              >
                <span>{label}</span>
                <strong>{fact ? formatMetricValue(fact) : "Unavailable"}</strong>
                <small>
                  {fact ? `${fact.period_end} | ${total} facts` : "No facts loaded"}
                </small>
              </button>
            ))}
          </div>

          <div className="metric-browser">
            <nav className="metric-selector" aria-label="Metric selector">
              {metricGroups.map((group) => (
                <button
                  className={`metric-selector__item ${
                    group.key === selectedGroup.key ? "metric-selector__item--active" : ""
                  }`}
                  key={group.key}
                  type="button"
                  onClick={() => onSelectMetric(group.key)}
                >
                  <span>{group.label}</span>
                  <small>{group.facts.length || "Unavailable"}</small>
                </button>
              ))}
            </nav>

            <section className="metric-detail" aria-labelledby={`metric-detail-${selectedGroup.key}`}>
              <div className="metric-detail__header">
                <div>
                  <h3 id={`metric-detail-${selectedGroup.key}`}>{selectedGroup.label}</h3>
                  <p className="muted">
                    {selectedGroup.facts.length
                      ? `${selectedGroup.facts.length} facts sorted newest first`
                      : "Unavailable"}
                  </p>
                </div>
                {selectedGroup.facts.length > DEFAULT_DETAIL_LIMIT && (
                  <button type="button" onClick={onToggleShowAll}>
                    {showAllMetricFacts ? "Show Recent" : `Show All ${selectedGroup.facts.length}`}
                  </button>
                )}
              </div>

              {selectedGroup.facts.length > 0 ? (
                <>
                  <div className="metric-fact-table">
                    <div className="metric-fact-row metric-fact-row--header">
                      <span>Period</span>
                      <span>Value</span>
                      <span>Reported In</span>
                      <span>Source</span>
                    </div>
                    {visibleFacts.map((fact) => (
                      <details className="metric-fact-row" key={fact.id}>
                        <summary>
                          <span>{formatMetricPeriod(fact)}</span>
                          <strong>
                            {formatMetricValue(fact)}
                            {fact.is_computed && <small>Computed</small>}
                          </strong>
                          <span>{formatMetricFilingContext(fact) ?? "n/a"}</span>
                          <span>
                            {fact.source_filing_url ? (
                              <a href={fact.source_filing_url} target="_blank" rel="noreferrer">
                                {fact.source_accession_number ?? "Source"}
                              </a>
                            ) : (
                              (fact.source_accession_number ?? "n/a")
                            )}
                          </span>
                        </summary>
                        <div className="metric-fact-detail">
                          <span>{fact.taxonomy_tag}</span>
                          {fact.calculation_notes && <span>{fact.calculation_notes}</span>}
                        </div>
                      </details>
                    ))}
                  </div>
                  {hiddenFactsCount > 0 && (
                    <p className="metric-detail__footer">
                      Showing latest {visibleFacts.length}; {hiddenFactsCount} older facts hidden.
                    </p>
                  )}
                </>
              ) : (
                <p className="metric-empty">Unavailable</p>
              )}
            </section>
          </div>
        </>
      )}
    </section>
  );
}

function JobStatus({ job }: { job: Job }) {
  return (
    <div className="job-status">
      <div className="job-status__top">
        <span>{job.job_type}</span>
        <span className={`job-pill job-pill--${job.status}`}>{job.status}</span>
      </div>
      <div className="progress-track" aria-label={`${job.job_type} progress`}>
        <div className="progress-fill" style={{ width: `${job.progress}%` }} />
      </div>
      <p>{String(job.payload.stage ?? "queued")}</p>
      {job.error_message && <p className="job-error">{job.error_message}</p>}
    </div>
  );
}
