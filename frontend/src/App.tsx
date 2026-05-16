import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { fetchHealthStatus } from "./api/health";
import {
  Company,
  DocumentChunk,
  Filing,
  FilingSection,
  FilingSectionSummary,
  Job,
  fetchCompany,
  fetchCompanyFilings,
  fetchFilingChunks,
  fetchFilingSection,
  fetchFilingSections,
  fetchJob,
  ingestCompany,
  parseFiling,
} from "./api/sec";
import "./styles.css";

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

export function App() {
  const [apiStatus, setApiStatus] = useState("checking");
  const [ticker, setTicker] = useState("AAPL");
  const [company, setCompany] = useState<Company | null>(null);
  const [filings, setFilings] = useState<Filing[]>([]);
  const [selectedFilingId, setSelectedFilingId] = useState<number | null>(null);
  const [sections, setSections] = useState<FilingSectionSummary[]>([]);
  const [selectedSectionId, setSelectedSectionId] = useState<number | null>(null);
  const [sectionDetail, setSectionDetail] = useState<FilingSection | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [ingestJob, setIngestJob] = useState<Job | null>(null);
  const [parseJob, setParseJob] = useState<Job | null>(null);
  const [isLoadingCompany, setIsLoadingCompany] = useState(false);
  const [isLoadingParsedData, setIsLoadingParsedData] = useState(false);
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
      const loadedFilings = await fetchCompanyFilings(loadedCompany.ticker);
      setTicker(loadedCompany.ticker);
      setCompany(loadedCompany);
      setFilings(loadedFilings);
      setSelectedFilingId(loadedFilings[0]?.id ?? null);
      setSections([]);
      setSelectedSectionId(null);
      setSectionDetail(null);
      setChunks([]);
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
      setError(getErrorMessage(loadError));
    } finally {
      setIsLoadingCompany(false);
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
      const job = await ingestCompany(normalizedTicker);
      setIngestJob(job);
      pollJob(job.id, setIngestJob, async () => {
        setMessage(`SEC metadata loaded for ${normalizedTicker}.`);
        await loadCompany(normalizedTicker);
      });
    } catch (ingestError) {
      setError(getErrorMessage(ingestError));
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
          <h1>Filing Explorer</h1>
        </div>
        <div className="status-row" aria-live="polite">
          <span className={`status-dot status-dot--${apiStatus}`} />
          <span>Backend: {apiStatus}</span>
        </div>
      </header>

      <section className="workspace-grid">
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
          {message && <p className="notice notice--success">{message}</p>}
          {error && <p className="notice notice--error">{error}</p>}
        </aside>

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
                <a href={selectedFiling.sec_primary_document_url} target="_blank" rel="noreferrer">
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
      </section>
    </main>
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
