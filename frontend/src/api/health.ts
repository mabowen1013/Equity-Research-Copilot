export type HealthStatus = {
  status: string;
};

export async function fetchHealthStatus(): Promise<HealthStatus> {
  const response = await fetch("/health");

  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }

  return response.json() as Promise<HealthStatus>;
}
