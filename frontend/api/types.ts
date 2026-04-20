import type { Column, Schema } from '@/types/datasources';

export type ApiError = {
	message: string;
	error: boolean;
};

export type ResponseWithError<T> = T & Partial<ApiError>;

export type ResponseWithCount<T> = {
	data: T;
	count: number;
};

export type ApiResponse<T> = ResponseWithError<ResponseWithCount<T>>;

/** Schemas endpoint returns a non-standard envelope (not {data,count}). */
export type SchemasResponse = {
	schemas_count: number;
	schemas: Omit<Schema, 'tables'>[];
};

/** Columns endpoint returns a table-scoped envelope with nested column rows. */
export type ColumnsEnvelope = {
	table_name: string;
	schema_name: string;
	db_name: string;
	columns_count: number;
	columns: Pick<Column, 'ordinal_position' | 'column_name' | 'data_type'>[];
};
