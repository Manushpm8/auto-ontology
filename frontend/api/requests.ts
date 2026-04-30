import axios, { AxiosError, AxiosResponse } from 'axios';
import type { Params } from '@/types/params';
import { ApiError, ResponseWithError } from './types';

const isServer = typeof window === 'undefined';

const api = axios.create({
	baseURL: isServer ? `${process.env.PYTHON_API_URL ?? 'http://127.0.0.1:3001'}/api` : '/api',
	headers: {
		'Content-Type': 'application/json',
	},
});

const responseBody = <DataType>({ data }: AxiosResponse<DataType>): DataType => data;

const errorHandler = (err: AxiosError): ApiError => {
	return {
		message:
			typeof err.response !== 'undefined'
				? (err.response.data as { message: string }).message
				: err.message,
		error: true,
	};
};

const normalizeQueryParams = (params: Params): Params => {
	const newParams: Params = {};
	Object.entries(params).forEach(([paramKey, paramValue]) => {
		if (Array.isArray(paramValue)) {
			newParams[paramKey] = paramValue.toString();
		} else {
			newParams[paramKey] = paramValue;
		}
	});
	return newParams;
};

export const requests = {
	get: <OutputType>(url: string, params: Params = {}, abortController?: AbortController) => {
		return api
			.get<OutputType>(url, {
				params: normalizeQueryParams(params),
				signal: abortController?.signal,
			})
			.then(responseBody)
			.catch(errorHandler) as Promise<ResponseWithError<OutputType>>;
	},

	post: <OutputType>(url: string, data: unknown = {}, abortController?: AbortController) => {
		return api
			.post<OutputType>(url, data, {
				signal: abortController?.signal,
			})
			.then(responseBody)
			.catch(errorHandler) as Promise<ResponseWithError<OutputType>>;
	},

	patch: <OutputType>(url: string, body: Record<string, unknown> = {}) => {
		return api.patch<OutputType>(url, body).then(responseBody).catch(errorHandler) as Promise<
			ResponseWithError<OutputType>
		>;
	},
};
