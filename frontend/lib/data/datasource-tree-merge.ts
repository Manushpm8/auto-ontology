import type { Column, Database, Schema, Table } from '@/types/datasources';

export function mergeSchemasIntoDatabase(
	databases: Database[],
	databaseId: string,
	schemas: Schema[],
): Database[] {
	return databases.map((database) =>
		database.id === databaseId ? { ...database, schemas } : database,
	);
}

export function mergeTablesIntoSchema(
	databases: Database[],
	schemaId: string,
	tables: Table[],
): Database[] {
	return databases.map((database) => ({
		...database,
		schemas: database.schemas.map((schema) =>
			schema.id === schemaId ? { ...schema, tables } : schema,
		),
	}));
}

/** Upsert columns for one table (merged from columns API). */
export function mergeColumnsIntoTable(
	databases: Database[],
	tableId: string,
	columns: Column[],
): Database[] {
	return databases.map((database) => ({
		...database,
		schemas: (database.schemas ?? []).map((schema) => ({
			...schema,
			tables: (schema.tables ?? []).map((table) =>
				table.id === tableId ? { ...table, columns, columns_count: columns.length } : table,
			),
		})),
	}));
}

function mergeTable(existing: Table, incoming: Table): Table {
	const existingColumns = existing.columns ?? [];
	const incomingColumns = incoming.columns ?? [];
	const richestColumns =
		incomingColumns.length > existingColumns.length
			? incomingColumns
			: existingColumns.length > 0
				? existingColumns
				: incomingColumns;
	return {
		...existing,
		...incoming,
		columns: richestColumns,
		columns_count: Math.max(
			existing.columns_count,
			incoming.columns_count,
			richestColumns.length,
		),
	};
}

function mergeSchema(existing: Schema, incoming: Schema): Schema {
	const existingTables = existing.tables ?? [];
	const incomingTables = incoming.tables ?? [];
	const tableById = new Map(existingTables.map((table) => [table.id, table]));
	for (const table of incomingTables) {
		const match = tableById.get(table.id);
		tableById.set(table.id, match ? mergeTable(match, table) : table);
	}
	const orderedIds = [...existingTables.map((table) => table.id)];
	for (const table of incomingTables) {
		if (!orderedIds.includes(table.id)) orderedIds.push(table.id);
	}
	const tables = orderedIds.map((id) => tableById.get(id)!);
	return {
		...existing,
		...incoming,
		tables,
		tables_count: Math.max(existing.tables_count, incoming.tables_count, tables.length),
	};
}

function mergeDatabase(existing: Database, incoming: Database): Database {
	const existingSchemas = existing.schemas ?? [];
	const incomingSchemas = incoming.schemas ?? [];
	const schemaById = new Map(existingSchemas.map((schema) => [schema.id, schema]));
	for (const schema of incomingSchemas) {
		const match = schemaById.get(schema.id);
		schemaById.set(schema.id, match ? mergeSchema(match, schema) : schema);
	}
	const orderedIds = [...existingSchemas.map((schema) => schema.id)];
	for (const schema of incomingSchemas) {
		if (!orderedIds.includes(schema.id)) orderedIds.push(schema.id);
	}
	const schemas = orderedIds.map((id) => schemaById.get(id)!);
	return {
		...existing,
		...incoming,
		schemas,
		num_of_schemas: Math.max(existing.num_of_schemas, incoming.num_of_schemas, schemas.length),
	};
}

/** Deep-merge catalog trees so prefetch + lazy expansion both keep richest node data. */
export function mergeDatabaseCatalog(previous: Database[], incoming: Database[]): Database[] {
	if (incoming.length === 0) return previous;
	const databaseById = new Map(previous.map((database) => [database.id, database]));
	for (const incomingDb of incoming) {
		const existing = databaseById.get(incomingDb.id);
		databaseById.set(
			incomingDb.id,
			existing ? mergeDatabase(existing, incomingDb) : incomingDb,
		);
	}
	const orderedIds = [...previous.map((database) => database.id)];
	for (const database of incoming) {
		if (!orderedIds.includes(database.id)) orderedIds.push(database.id);
	}
	return orderedIds.map((id) => databaseById.get(id)!);
}

/** Cheap fingerprint for syncing explorer state when the parent ref gains new API data. */
export function catalogStructureFingerprint(databases: Database[]): string {
	return databases
		.map((database) => {
			const schemasPart = (database.schemas ?? [])
				.map((schema) => {
					const tables = schema.tables ?? [];
					const tablesPart = tables
						.map((table) => `${table.id}:${(table.columns ?? []).length}`)
						.join(',');
					return `${schema.id}:${tables.length}:${tablesPart}`;
				})
				.join(';');
			return `${database.id}:${(database.schemas ?? []).length}:${schemasPart}`;
		})
		.join('|');
}
