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

export type FilingSection = {
  id: number;
  filing_id: number;
  section_key: string;
  section_title: string;
  section_order: number;
  normalized_text: string;
  start_offset: number;
  end_offset: number;
  extraction_confidence: number;
  extraction_method: string;
  created_at: string;
  updated_at: string;
};

export type DocumentChunk = {
  id: number;
  filing_id: number;
  section_id: number;
  chunk_index: number;
  chunk_text: string;
  token_count: number;
  start_offset: number;
  end_offset: number;
  text_hash: string;
  accession_number: string;
  form_type: string;
  filing_date: string;
  section_key: string;
  sec_url: string;
  created_at: string;
  updated_at: string;
};

export async function fetchCompany(ticker: string): Promise<Company> {
  return requestJson<Company>(`/companies/${encodeURIComponent(ticker)}`);
}

export async function fetchCompanyFilings(ticker: string): Promise<Filing[]> {
  return requestJson<Filing[]>(`/companies/${encodeURIComponent(ticker)}/filings`);
}

export async function fetchCompanyJobs(ticker: string, limit = 100): Promise<Job[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  return requestJson<Job[]>(
    `/companies/${encodeURIComponent(ticker)}/jobs?${params.toString()}`,
  );
}

export async function fetchJob(jobId: number): Promise<Job> {
  return requestJson<Job>(`/jobs/${jobId}`);
}

export async function processFiling(filingId: number, refresh = false): Promise<Job> {
  const params = new URLSearchParams({ refresh: String(refresh) });
  return requestJson<Job>(`/filings/${filingId}/process?${params.toString()}`, {
    method: "POST",
  });
}

export async function fetchFilingSections(filingId: number): Promise<FilingSection[]> {
  return requestJson<FilingSection[]>(`/filings/${filingId}/sections`);
}

export async function fetchFilingChunks(
  filingId: number,
  options: {
    sectionId?: number;
    limit?: number;
  } = {},
): Promise<DocumentChunk[]> {
  const params = new URLSearchParams();
  if (options.sectionId !== undefined) {
    params.set("section_id", String(options.sectionId));
  }
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit));
  }

  const query = params.toString();
  return requestJson<DocumentChunk[]>(
    `/filings/${filingId}/chunks${query ? `?${query}` : ""}`,
  );
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);

  if (!response.ok) {
    const message = await getErrorMessage(response);
    throw new Error(message);
  }

  try {
    return (await response.json()) as T;
  } catch (error) {
    throw new Error(
      `Expected JSON response but received ${response.headers.get("content-type") ?? "unknown content type"}.`,
      { cause: error },
    );
  }
}

async function getErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    return `Request failed with status ${response.status}`;
  }

  return `Request failed with status ${response.status}`;
}
