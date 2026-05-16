export type Company = {
  id: number;
  ticker: string;
  cik: string;
  name: string;
  exchange: string | null;
  sic: string | null;
  sic_description: string | null;
  created_at: string;
  updated_at: string;
};

export type Filing = {
  id: number;
  company_id: number;
  accession_number: string;
  form_type: string;
  filing_date: string;
  report_date: string | null;
  primary_document: string | null;
  sec_filing_url: string;
  sec_primary_document_url: string | null;
  created_at: string;
  updated_at: string;
};

export type Job = {
  id: number;
  job_type: string;
  company_id: number | null;
  status: string;
  progress: number;
  retry_count: number;
  payload: Record<string, unknown>;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type FilingSectionSummary = {
  id: number;
  filing_id: number;
  section_key: string;
  part: string | null;
  item: string | null;
  title: string | null;
  section_order: number;
  start_page: number | null;
  end_page: number | null;
  start_display_page: number | null;
  end_display_page: number | null;
  token_count: number;
  created_at: string;
  updated_at: string;
};

export type FilingSection = FilingSectionSummary & {
  markdown_text: string;
};

export type DocumentChunk = {
  id: number;
  filing_id: number;
  section_id: number;
  chunk_index: number;
  chunk_text: string;
  token_count: number;
  accession_number: string;
  form_type: string;
  filing_date: string;
  section_label: string;
  sec_url: string;
  start_page: number | null;
  end_page: number | null;
  start_display_page: number | null;
  end_display_page: number | null;
  element_ids: string[];
  xbrl_tags: string[];
  source_start_offset: number | null;
  source_end_offset: number | null;
  has_table: boolean;
  created_at: string;
  updated_at: string;
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function fetchCompany(ticker: string): Promise<Company> {
  return requestJson<Company>(`/companies/${encodeURIComponent(ticker)}`);
}

export function ingestCompany(ticker: string, refresh = false): Promise<Job> {
  const query = refresh ? "?refresh=true" : "";
  return requestJson<Job>(`/companies/${encodeURIComponent(ticker)}/ingest${query}`, {
    method: "POST",
  });
}

export function fetchCompanyFilings(ticker: string): Promise<Filing[]> {
  return requestJson<Filing[]>(`/companies/${encodeURIComponent(ticker)}/filings?limit=100`);
}

export function parseFiling(filingId: number, refresh = false): Promise<Job> {
  const query = refresh ? "?refresh=true" : "";
  return requestJson<Job>(`/filings/${filingId}/parse${query}`, {
    method: "POST",
  });
}

export function fetchFilingSections(filingId: number): Promise<FilingSectionSummary[]> {
  return requestJson<FilingSectionSummary[]>(`/filings/${filingId}/sections`);
}

export function fetchFilingSection(
  filingId: number,
  sectionId: number,
): Promise<FilingSection> {
  return requestJson<FilingSection>(`/filings/${filingId}/sections/${sectionId}`);
}

export function fetchFilingChunks(
  filingId: number,
  sectionId?: number,
): Promise<DocumentChunk[]> {
  const query = sectionId === undefined ? "?limit=100" : `?section_id=${sectionId}&limit=100`;
  return requestJson<DocumentChunk[]>(`/filings/${filingId}/chunks${query}`);
}

export function fetchJob(jobId: number): Promise<Job> {
  return requestJson<Job>(`/jobs/${jobId}`);
}
