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

export type FinancialFact = {
  id: number;
  company_id: number;
  canonical_metric_key: string;
  taxonomy_tag: string;
  label: string;
  period_start: string | null;
  period_end: string;
  source_fiscal_year: number | null;
  fact_fiscal_year: number | null;
  fiscal_period: string | null;
  form_type: string | null;
  filed_date: string | null;
  unit: string;
  value: string;
  source_accession_number: string | null;
  source_filing_id: number | null;
  source_filing_url: string | null;
  source_fact_id: string;
  is_computed: boolean;
  calculation_notes: string | null;
  created_at: string;
  updated_at: string;
};

export type RetrievalPlan = {
  question_type: string;
  target_sections: string[];
  metric_keys: string[];
  time_scope: string;
  comparison_basis: string;
  comparison_candidates: string[];
  default_comparison_basis: string | null;
  ambiguities: string[];
  forms: string[];
  preferred_forms: string[];
  dense_queries: string[];
  dense_query_specs: Record<string, unknown>[];
  lexical_queries: string[];
  rule_confidence: number;
  matched_rules: string[];
  planner_source: string;
  confidence_breakdown: Record<string, number>;
  needs_financial_facts: boolean;
  needs_text_chunks: boolean;
  needs_metric_comparisons: boolean;
  evidence_roles: string[];
  requires_llm_fallback_reason: string | null;
};

export type RetrievalAnalysisChunk = {
  evidence_id: string;
  chunk_id: number;
  filing_id: number;
  score: number;
  fusion_score: number;
  source_ranks: Record<string, number>;
  rerank_boosts: Record<string, number>;
  form_type: string;
  filing_date: string;
  section_label: string;
  pages: string | null;
  snippet: string;
  sec_url: string;
};

export type RetrievalAnalysisSpan = {
  evidence_id: string;
  chunk_id: number;
  source_chunk_evidence_id: string;
  role: string;
  score: number;
  support_kind: string;
  text: string;
  start_char: number | null;
  end_char: number | null;
  reasons: string[];
  form_type: string;
  filing_date: string;
  section_label: string;
  pages: string | null;
  sec_url: string;
};

export type RetrievalAnalysisFact = {
  evidence_id: string;
  score: number;
  canonical_metric_key: string;
  label: string;
  period_start: string | null;
  period_end: string;
  duration_class: string | null;
  period_label: string | null;
  source_fiscal_year: number | null;
  fact_fiscal_year: number | null;
  fiscal_period: string | null;
  value: string;
  unit: string;
  source_filing_url: string | null;
};

export type RetrievalAnalysisComparison = {
  evidence_id: string;
  basis: string;
  canonical_metric_key: string;
  current_fact_id: number;
  prior_fact_id: number;
  current_period_end: string;
  prior_period_end: string;
  current_period_label: string | null;
  prior_period_label: string | null;
  current_source_fiscal_year: number | null;
  current_fact_fiscal_year: number | null;
  prior_source_fiscal_year: number | null;
  prior_fact_fiscal_year: number | null;
  current_value: string;
  prior_value: string;
  growth_rate: string | null;
};

export type RetrievalAnalysisResponse = {
  retrieval_plan: RetrievalPlan;
  source_coverage_summary: Record<string, unknown>;
  final_evidence_pack: {
    metric_comparisons: RetrievalAnalysisComparison[];
    primary_financial_statement_chunks: RetrievalAnalysisChunk[];
    mda_explanation_chunks: RetrievalAnalysisChunk[];
    segment_or_product_breakdown_chunks: RetrievalAnalysisChunk[];
    annual_context_chunks: RetrievalAnalysisChunk[];
    primary_financial_statement_spans: RetrievalAnalysisSpan[];
    mda_explanation_spans: RetrievalAnalysisSpan[];
    segment_or_product_breakdown_spans: RetrievalAnalysisSpan[];
    annual_context_spans: RetrievalAnalysisSpan[];
  };
  top_chunks: RetrievalAnalysisChunk[];
  top_facts: RetrievalAnalysisFact[];
  metric_comparisons: RetrievalAnalysisComparison[];
  analysis_trace: {
    candidate_counts: Record<string, number>;
    timing_ms: Record<string, number>;
    degraded: { stage: string; reason: string }[];
    retrieval_config: Record<string, unknown>;
    top_score_breakdown: Record<string, unknown>[];
  };
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

export function ingestCompany(ticker: string, refresh = true): Promise<Job> {
  const query = `?refresh=${refresh ? "true" : "false"}`;
  return requestJson<Job>(`/companies/${encodeURIComponent(ticker)}/ingest${query}`, {
    method: "POST",
  });
}

export function fetchCompanyFilings(ticker: string): Promise<Filing[]> {
  return requestJson<Filing[]>(`/companies/${encodeURIComponent(ticker)}/filings?limit=100`);
}

export function loadCompanyMetrics(ticker: string, refresh = false): Promise<Job> {
  const query = refresh ? "?refresh=true" : "";
  return requestJson<Job>(`/companies/${encodeURIComponent(ticker)}/metrics/load${query}`, {
    method: "POST",
  });
}

export function generateCompanyEmbeddings(ticker: string, refresh = false): Promise<Job> {
  const query = refresh ? "?refresh=true" : "";
  return requestJson<Job>(`/companies/${encodeURIComponent(ticker)}/embeddings/generate${query}`, {
    method: "POST",
  });
}

export function fetchCompanyMetrics(
  ticker: string,
  metricKey?: string,
): Promise<FinancialFact[]> {
  const params = new URLSearchParams({ limit: "1000" });
  if (metricKey) {
    params.set("metric_key", metricKey);
  }

  return requestJson<FinancialFact[]>(
    `/companies/${encodeURIComponent(ticker)}/metrics?${params.toString()}`,
  );
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

export function retrieveEvidence(request: {
  ticker: string;
  question: string;
  form_type?: string;
  section?: string;
}): Promise<RetrievalAnalysisResponse> {
  return requestJson<RetrievalAnalysisResponse>("/research/retrieve?view=analysis", {
    body: JSON.stringify(request),
    headers: { "Content-Type": "application/json" },
    method: "POST",
  });
}
