export type Column = {
	column_name: string;
	data_type: string;
	db_name: string;
	schema_name: string;
	table_name: string;
	ordinal_position: number;
};

export type Table = {
	id: string;
	name: string;
	db_name: string;
	schema_name: string;
	columns_count: number;
	columns: Column[];
};

export type Schema = {
	id: string;
	schema_name: string;
	tables_count: number;
	tables: Table[];
};

export type Database = {
	id: string;
	name: string;
	num_of_schemas: number;
	schemas: Schema[];
};
