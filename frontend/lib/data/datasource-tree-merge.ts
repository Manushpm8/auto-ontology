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
	let found = false;
	const next = databases.map((db) => {
		if (found) return db;
		let dbDirty = false;
		const schemas = db.schemas.map((sch) => {
			if (sch.id === schemaId) {
				found = true;
				dbDirty = true;
				return { ...sch, tables };
			}
			return sch;
		});
		return dbDirty ? { ...db, schemas } : db;
	});
	return found ? next : databases;
}

/** Upsert columns for one table (merged from columns API). */
export function mergeColumnsIntoTable(
	databases: Database[],
	tableId: string,
	columns: Column[],
): Database[] {
	let found = false;
	const next = databases.map((db) => {
		if (found) return db;
		let dbDirty = false;
		const schemas = (db.schemas ?? []).map((sch) => {
			if (found) return sch;
			let schDirty = false;
			const tables = (sch.tables ?? []).map((tbl) => {
				if (tbl.id === tableId) {
					found = true;
					schDirty = true;
					return { ...tbl, columns, columns_count: columns.length };
				}
				return tbl;
			});
			if (schDirty) dbDirty = true;
			return schDirty ? { ...sch, tables } : sch;
		});
		return dbDirty ? { ...db, schemas } : db;
	});
	return found ? next : databases;
}

function mergeTable(existing: Table, incoming: Table): Table {
	const existingColumns = existing.columns ?? [];
	const incomingColumns = incoming.columns ?? [];

	let columns: Column[];
	if (existingColumns.length === 0) {
		columns = incomingColumns;
	} else if (incomingColumns.length === 0) {
		columns = existingColumns;
	} else {
		const colById = new Map(existingColumns.map((c) => [c.id, c]));
		for (const col of incomingColumns) {
			const match = colById.get(col.id);
			colById.set(col.id, match ? { ...match, ...col } : col);
		}
		const orderedIds = existingColumns.map((c) => c.id);
		const seen = new Set(orderedIds);
		for (const col of incomingColumns) {
			if (!seen.has(col.id)) orderedIds.push(col.id);
		}
		columns = orderedIds.map((id) => colById.get(id)!);
	}

	return {
		...existing,
		...incoming,
		columns,
		columns_count: Math.max(existing.columns_count, incoming.columns_count, columns.length),
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
	const orderedIds = existingTables.map((table) => table.id);
	const seen = new Set(orderedIds);
	for (const table of incomingTables) {
		if (!seen.has(table.id)) orderedIds.push(table.id);
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
	const orderedIds = existingSchemas.map((schema) => schema.id);
	const seen = new Set(orderedIds);
	for (const schema of incomingSchemas) {
		if (!seen.has(schema.id)) orderedIds.push(schema.id);
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
	const orderedIds = previous.map((database) => database.id);
	const seen = new Set(orderedIds);
	for (const database of incoming) {
		if (!seen.has(database.id)) orderedIds.push(database.id);
	}
	return orderedIds.map((id) => databaseById.get(id)!);
}

/**
 * Patch a single node in the catalog tree by its ID, creating new objects
 * only along the path to the target node (not the entire tree).
 * Works for Database, Schema, Table, and Column nodes.
 * Returns [updatedDatabases, wasFound].
 */
export function patchNodeInTree(
	databases: Database[],
	nodeId: string,
	patch: Partial<Database> & Partial<Schema> & Partial<Table> & Partial<Column>,
): [Database[], boolean] {
	let found = false;
	const next = databases.map((db) => {
		if (found) return db;
		if (db.id === nodeId) {
			found = true;
			return { ...db, ...patch, schemas: db.schemas };
		}
		let dbDirty = false;
		const schemas = db.schemas.map((sch) => {
			if (found) return sch;
			if (sch.id === nodeId) {
				found = true;
				dbDirty = true;
				return { ...sch, ...patch, tables: sch.tables };
			}
			let schDirty = false;
			const tables = sch.tables.map((tbl) => {
				if (found) return tbl;
				if (tbl.id === nodeId) {
					found = true;
					schDirty = true;
					return { ...tbl, ...patch, columns: tbl.columns };
				}
				let tblDirty = false;
				const columns = tbl.columns.map((col) => {
					if (found) return col;
					if (col.id === nodeId) {
						found = true;
						tblDirty = true;
						return { ...col, ...patch };
					}
					return col;
				});
				if (tblDirty) schDirty = true;
				return tblDirty ? { ...tbl, columns } : tbl;
			});
			if (schDirty) dbDirty = true;
			return schDirty ? { ...sch, tables } : sch;
		});
		return dbDirty ? { ...db, schemas } : db;
	});
	return [found ? next : databases, found];
}

/** Cheap fingerprint for syncing explorer state when the parent ref gains new API data. */
export function catalogStructureFingerprint(databases: Database[]): string {
	return databases
		.map((database) => {
			const schemasPart = (database.schemas ?? [])
				.map((schema) => {
					const tables = schema.tables ?? [];
					const tablesPart = tables
						.map((table) => {
							const colDescs = (table.columns ?? [])
								.map((c) => `${c.description ?? ''}~${c.sample_values ?? ''}`)
								.join('/');
							return `${table.id}:${(table.columns ?? []).length}:${table.description ?? ''}:${colDescs}`;
						})
						.join(',');
					return `${schema.id}:${tables.length}:${schema.description ?? ''}:${tablesPart}`;
				})
				.join(';');
			return `${database.id}:${(database.schemas ?? []).length}:${database.description ?? ''}:${schemasPart}`;
		})
		.join('|');
}
