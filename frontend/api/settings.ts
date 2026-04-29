export type CustomPrompt = {
	id: string;
	content: string;
};

async function json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
	const res = await fetch(input, init);
	if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
	return res.json() as Promise<T>;
}

export const customPromptsApi = {
	get: () => json<CustomPrompt[]>('/api/custom-prompts'),

	create: (data: { content?: string } = {}) =>
		json<CustomPrompt>('/api/custom-prompts', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(data),
		}),

	update: (id: string, data: { content?: string }) =>
		json<CustomPrompt>(`/api/custom-prompts/${id}`, {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(data),
		}),

	delete: (id: string) => fetch(`/api/custom-prompts/${id}`, { method: 'DELETE' }),
};
