import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  DocumentChunk,
  Filing,
  FilingSection,
  Job,
  fetchCompany,
  fetchCompanyFilings,
  fetchCompanyJobs,
  fetchFilingChunks,
  fetchFilingSections,
  fetchJob,
  processFiling,
  type Company,
} from "./api/filings";
import { fetchHealthStatus } from "./api/health";
import "./styles.css";

type LoadState = "idle" | "loading" | "ready" | "error";

type FilingJobSummary = {
  status: string;
  progress: number;
  updatedAt: string;
  errorMessage: string | null;
  sectionsCount: number | null;
  chunksCount: number | null;
  jobId: number;
};

const DEFAULT_TICKER = "AAPL";
const JOB_POLL_INTERVAL_MS = 1500;

export function App() {
  const [apiStatus, setApiStatus] = useState("checking");
  const [tickerInput, setTickerInput] = useState(DEFAULT_TICKER);
  const [activeTicker, setActiveTicker] = useState(DEFAULT_TICKER);
  const [company, setCompany] = useState<Company | null>(null);
  const [filings, setFilings] = useState<Filing[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedFilingId, setSelectedFilingId] = useState<number | null>(null);
  const [sections, setSections] = useState<FilingSection[]>([]);
  const [selectedSectionId, setSelectedSectionId] = useState<number | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [companyState, setCompanyState] = useState<LoadState>("idle");
  const [sectionsState, setSectionsState] = useState<LoadState>("idle");
  const [chunksState, setChunksState] = useState<LoadState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [processingFilingId, setProcessingFilingId] = useState<number | null>(null);
  const [filingDataVersion, setFilingDataVersion] = useState(0);
  const lastLoadedChunkedRevision = useRef<string | null>(null);

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
    };
  }, []);

  useEffect(() => {
    void loadCompanyWorkspace(activeTicker);
  }, [activeTicker]);

  useEffect(() => {
    if (selectedFilingId === null) {
      setSections([]);
      setSelectedSectionId(null);
      setChunks([]);
      return;
    }

    let isMounted = true;
    setSectionsState("loading");
    setChunks([]);
    setSelectedSectionId(null);

    fetchFilingSections(selectedFilingId)
      .then((nextSections) => {
        if (!isMounted) {
          return;
        }
        setSections(nextSections);
        setSelectedSectionId(nextSections[0]?.id ?? null);
        setSectionsState("ready");
      })
      .catch((error) => {
        if (!isMounted) {
          return;
        }
        setSections([]);
        setSectionsState("error");
        setErrorMessage(error instanceof Error ? error.message : "Unable to load sections.");
      });

    return () => {
      isMounted = false;
    };
  }, [selectedFilingId, filingDataVersion]);

  useEffect(() => {
    if (selectedFilingId === null || selectedSectionId === null) {
      setChunks([]);
      return;
    }

    let isMounted = true;
    setChunksState("loading");

    fetchFilingChunks(selectedFilingId, { sectionId: selectedSectionId, limit: 100 })
      .then((nextChunks) => {
        if (!isMounted) {
          return;
        }
        setChunks(nextChunks);
        setChunksState("ready");
      })
      .catch((error) => {
        if (!isMounted) {
          return;
        }
        setChunks([]);
        setChunksState("error");
        setErrorMessage(error instanceof Error ? error.message : "Unable to load chunks.");
      });

    return () => {
      isMounted = false;
    };
  }, [selectedFilingId, selectedSectionId]);

  const selectedFiling = filings.find((filing) => filing.id === selectedFilingId) ?? null;
  const selectedSection =
    sections.find((section) => section.id === selectedSectionId) ?? null;
  const jobByFilingId = useMemo(() => mapLatestJobsByFilingId(jobs), [jobs]);
  const selectedFilingJob =
    selectedFilingId === null ? undefined : jobByFilingId.get(selectedFilingId);
  const selectedChunkedRevision =
    selectedFilingJob !== undefined &&
    selectedFilingJob.status === "succeeded" &&
    selectedFilingJob.chunksCount !== null &&
    selectedFilingJob.chunksCount > 0
      ? `${selectedFilingId}:${selectedFilingJob.jobId}:${selectedFilingJob.updatedAt}:${selectedFilingJob.chunksCount}`
      : null;
  const hasActiveFilingJob = jobs.some((job) => job.job_type === "filing_processing" && isActiveJob(job));
  const parsedSectionsCount = sections.length;
  const parsedChunksCount = chunks.length;

  useEffect(() => {
    if (company === null || !hasActiveFilingJob) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void refreshJobs();
    }, JOB_POLL_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [company?.ticker, hasActiveFilingJob]);

  useEffect(() => {
    if (selectedChunkedRevision === null) {
      return;
    }

    if (lastLoadedChunkedRevision.current === selectedChunkedRevision) {
      return;
    }

    lastLoadedChunkedRevision.current = selectedChunkedRevision;
    setFilingDataVersion((version) => version + 1);
  }, [selectedChunkedRevision]);

  function handleTickerSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedTicker = tickerInput.trim().toUpperCase();
    if (!normalizedTicker) {
      setErrorMessage("Ticker must not be empty.");
      return;
    }

    setActiveTicker(normalizedTicker);
  }

  async function loadCompanyWorkspace(ticker: string) {
    setCompanyState("loading");
    setErrorMessage(null);
    setSelectedFilingId(null);
    setSelectedSectionId(null);
    setSections([]);
    setChunks([]);

    try {
      const [nextCompany, nextFilings, nextJobs] = await Promise.all([
        fetchCompany(ticker),
        fetchCompanyFilings(ticker),
        fetchCompanyJobs(ticker),
      ]);
      setCompany(nextCompany);
      setFilings(nextFilings);
      setJobs(nextJobs);
      setSelectedFilingId(nextFilings[0]?.id ?? null);
      lastLoadedChunkedRevision.current = null;
      setCompanyState("ready");
    } catch (error) {
      setCompany(null);
      setFilings([]);
      setJobs([]);
      setCompanyState("error");
      setErrorMessage(error instanceof Error ? error.message : "Unable to load ticker.");
    }
  }

  async function refreshJobs() {
    if (company === null) {
      return;
    }

    try {
      setJobs(await fetchCompanyJobs(company.ticker));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Unable to refresh jobs.");
    }
  }

  async function handleProcessFiling(filingId: number, refresh: boolean) {
    setProcessingFilingId(filingId);
    setErrorMessage(null);

    try {
      const job = await processFiling(filingId, refresh);
      setJobs((currentJobs) => [job, ...currentJobs.filter((item) => item.id !== job.id)]);
      await refreshJobs();
      void pollJobUntilFinished(job.id, filingId);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Unable to process filing.");
    } finally {
      setProcessingFilingId(null);
    }
  }

  async function pollJobUntilFinished(jobId: number, filingId: number) {
    try {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        await delay(JOB_POLL_INTERVAL_MS);
        const job = await fetchJob(jobId);
        setJobs((currentJobs) => [job, ...currentJobs.filter((item) => item.id !== job.id)]);

        if (!isActiveJob(job)) {
          await refreshJobs();
          if (job.status === "succeeded" && filingId === selectedFilingId) {
            setFilingDataVersion((version) => version + 1);
          }
          return;
        }
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Unable to refresh job status.");
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Equity Research Copilot</p>
          <h1>Filing Explorer</h1>
        </div>
        <div className="status-row" aria-live="polite">
          <span className={`status-dot status-dot--${apiStatus}`} />
          <span>Backend {apiStatus}</span>
        </div>
      </header>

      <section className="toolbar" aria-label="Company lookup">
        <form className="ticker-form" onSubmit={handleTickerSubmit}>
          <label htmlFor="ticker-input">Ticker</label>
          <input
            id="ticker-input"
            value={tickerInput}
            onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
            spellCheck={false}
            autoComplete="off"
          />
          <button type="submit" disabled={companyState === "loading"}>
            {companyState === "loading" ? "Loading" : "Load"}
          </button>
        </form>

        {company !== null ? (
          <div className="company-strip">
            <strong>{company.ticker}</strong>
            <span>{company.name}</span>
            <span>CIK {company.cik}</span>
            {company.exchange ? <span>{company.exchange}</span> : null}
          </div>
        ) : null}
      </section>

      {errorMessage !== null ? (
        <div className="alert" role="alert">
          {errorMessage}
        </div>
      ) : null}

      <section className="workspace-grid" aria-label="Filing explorer">
        <aside className="filings-pane">
          <div className="pane-header">
            <div>
              <p className="pane-kicker">Filings</p>
              <h2>{filings.length} stored</h2>
            </div>
            <button
              className="icon-button"
              type="button"
              onClick={refreshJobs}
              disabled={company === null}
              title="Refresh jobs"
              aria-label="Refresh jobs"
            >
              R
            </button>
          </div>

          <div className="filing-list">
            {filings.map((filing) => {
              const job = jobByFilingId.get(filing.id);
              const readiness = getFilingReadiness(job);
              const isSelected = filing.id === selectedFilingId;
              const isBusy =
                processingFilingId === filing.id ||
                (job !== undefined && (job.status === "pending" || job.status === "running"));

              return (
                <article
                  className={`filing-card${isSelected ? " filing-card--selected" : ""}`}
                  key={filing.id}
                >
                  <button
                    className="filing-select"
                    type="button"
                    onClick={() => setSelectedFilingId(filing.id)}
                    aria-pressed={isSelected}
                  >
                    <span className="filing-main">
                      <strong>{filing.form_type}</strong>
                      <span>{formatDate(filing.filing_date)}</span>
                    </span>
                    <span className="filing-sub">
                      Report {formatDate(filing.report_date)}
                    </span>
                    <span className={`job-pill job-pill--${readiness.kind}`}>
                      {readiness.label}
                    </span>
                  </button>

                  <div className="filing-actions">
                    <a
                      href={filing.sec_primary_document_url ?? filing.sec_filing_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      SEC
                    </a>
                    <button
                      type="button"
                      onClick={() => void handleProcessFiling(filing.id, false)}
                      disabled={isBusy}
                    >
                      Process
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleProcessFiling(filing.id, true)}
                      disabled={isBusy}
                    >
                      Refresh
                    </button>
                  </div>
                </article>
              );
            })}

            {companyState === "ready" && filings.length === 0 ? (
              <p className="empty-state">No stored filings.</p>
            ) : null}
          </div>
        </aside>

        <section className="sections-pane" aria-label="Sections">
          <div className="pane-header">
            <div>
              <p className="pane-kicker">Sections</p>
              <h2>{selectedFiling ? selectedFiling.form_type : "No filing"}</h2>
            </div>
            <div className="count-strip">
              <span>{parsedSectionsCount} sections</span>
              <span>{parsedChunksCount} visible chunks</span>
            </div>
          </div>

          <div className="section-tabs" role="tablist" aria-label="Filing sections">
            {sections.map((section) => (
              <button
                className={`section-tab${
                  section.id === selectedSectionId ? " section-tab--selected" : ""
                }`}
                key={section.id}
                type="button"
                onClick={() => setSelectedSectionId(section.id)}
                role="tab"
                aria-selected={section.id === selectedSectionId}
              >
                <span>{section.section_title}</span>
                <small>{section.extraction_confidence}%</small>
              </button>
            ))}
          </div>

          <article className="section-text">
            {sectionsState === "loading" ? <p className="empty-state">Loading sections.</p> : null}
            {sectionsState === "ready" && selectedSection !== null ? (
              <>
                <div className="section-meta">
                  <span>{selectedSection.section_key}</span>
                  <span>{selectedSection.extraction_method}</span>
                  <span>
                    {selectedSection.start_offset}..{selectedSection.end_offset}
                  </span>
                </div>
                <h3>{selectedSection.section_title}</h3>
                <pre>{selectedSection.normalized_text}</pre>
              </>
            ) : null}
            {sectionsState === "ready" && sections.length === 0 ? (
              <p className="empty-state">No parsed sections.</p>
            ) : null}
          </article>
        </section>

        <aside className="chunks-pane" aria-label="Chunks">
          <div className="pane-header">
            <div>
              <p className="pane-kicker">Chunks</p>
              <h2>{selectedSection ? selectedSection.section_key : "None"}</h2>
            </div>
          </div>

          <div className="chunk-list">
            {chunksState === "loading" ? <p className="empty-state">Loading chunks.</p> : null}
            {chunks.map((chunk) => (
              <article className="chunk-card" key={chunk.id}>
                <div className="chunk-card__top">
                  <strong>#{chunk.chunk_index}</strong>
                  <span>{chunk.token_count} tokens</span>
                </div>
                <p>{chunk.chunk_text}</p>
                <dl>
                  <div>
                    <dt>Offsets</dt>
                    <dd>
                      {chunk.start_offset}..{chunk.end_offset}
                    </dd>
                  </div>
                  <div>
                    <dt>Hash</dt>
                    <dd>{chunk.text_hash.slice(0, 12)}</dd>
                  </div>
                  <div>
                    <dt>Accession</dt>
                    <dd>{chunk.accession_number}</dd>
                  </div>
                </dl>
                <a href={chunk.sec_url} target="_blank" rel="noreferrer">
                  Source
                </a>
              </article>
            ))}
            {chunksState === "ready" && chunks.length === 0 ? (
              <p className="empty-state">No chunks for this section.</p>
            ) : null}
          </div>
        </aside>
      </section>
    </main>
  );
}

function mapLatestJobsByFilingId(jobs: Job[]): Map<number, FilingJobSummary> {
  const summaries = new Map<number, FilingJobSummary>();
  const sortedJobs = [...jobs].sort(
    (left, right) =>
      new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
  );

  for (const job of sortedJobs) {
    if (job.job_type !== "filing_processing") {
      continue;
    }

    const filingId = Number(job.payload.filing_id);
    if (!Number.isFinite(filingId) || summaries.has(filingId)) {
      continue;
    }

    summaries.set(filingId, {
      jobId: job.id,
      status: job.status,
      progress: job.progress,
      updatedAt: job.updated_at,
      errorMessage: job.error_message,
      sectionsCount: numberFromPayload(job.payload.sections_count),
      chunksCount: numberFromPayload(job.payload.chunks_count),
    });
  }

  return summaries;
}

function getFilingReadiness(job: FilingJobSummary | undefined): {
  kind: string;
  label: string;
} {
  if (job === undefined) {
    return { kind: "unprocessed", label: "not processed" };
  }

  if (job.status === "pending" || job.status === "running") {
    return { kind: job.status, label: `${job.status} ${job.progress}%` };
  }

  if (job.status === "failed") {
    return { kind: "failed", label: "failed" };
  }

  if (job.status === "succeeded" && job.chunksCount !== null && job.chunksCount > 0) {
    return { kind: "chunked", label: `chunked ${job.chunksCount}` };
  }

  if (job.status === "succeeded" && job.chunksCount === 0) {
    return { kind: "stale", label: "no chunks" };
  }

  if (job.status === "succeeded" && job.sectionsCount !== null) {
    return { kind: "stale", label: "sections only" };
  }

  return { kind: "stale", label: "needs process" };
}

function numberFromPayload(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  return null;
}

function isActiveJob(job: Job): boolean {
  return job.status === "pending" || job.status === "running";
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

function formatDate(value: string | null): string {
  if (value === null) {
    return "n/a";
  }

  return value;
}
