// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import NextLink from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import cytoscape, { type Core, type ElementDefinition, type EventObject } from 'cytoscape';
import euler from 'cytoscape-euler';

import { datasources } from '@/api/datasources';
import { termsApi } from '@/api/terms';
import { Icon, IconName } from '@/components/icons';
import { Modal } from '@/components/Modal';
import { SearchInput } from '@/components/SearchInput';
import { SqlBlock } from '@/components/SqlBlock';
import { Table } from '@/components/Table';
import SnowflakeSvg from '@/components/icons/svg/snowflake.svg';
import TermsSvg from '@/components/icons/svg/terms.svg';
import { TableType } from '@/enums/datasources';
import type {
	ExplorationDataNode,
	ExplorationGraph,
	ExplorationLayer,
	ExplorationLink,
	ExplorationNode,
	ExplorationTermNode,
	DataExplorationGraph,
	SemanticExplorationGraph,
	TableExplorationDetails,
} from '@/types/exploration';
import type { Column } from '@/types/datasources';
import type { ColumnAttribute, SqlAttribute } from '@/types/terms';
import type { TableColumn } from '@/types/table';
import { DataTablePills, ZoneChip, ZonesRow } from '@/common/SinglePageComposer';
import { TruncatedText } from '@/components/TruncatedText';
import { catalogPathFromFocusId } from '@/lib/data/data-catalog-path';

cytoscape.use(euler);

const EMPTY_GRAPH: ExplorationGraph = { nodes: [], links: [] };
const DATA_OBJECT_CONNECTOR_ICON = encodeURI(
	`data:image/svg+xml;utf-8,${renderToStaticMarkup(
		<SnowflakeSvg width={24} height={24} />,
	).replaceAll('currentColor', 'rgb(49, 185, 197)')}`,
);
const TERM_OBJECT_ICON = encodeURI(
	`data:image/svg+xml;utf-8,${renderToStaticMarkup(
		<TermsSvg width={24} height={24} />,
	).replaceAll('currentColor', 'rgb(71, 186, 197)')}`,
);
const EULER_LAYOUT: cytoscapeEuler.EulerLayoutOptions = {
	name: 'euler',
	springLength: () => 800,
	springCoeff: () => 0.000001,
	mass: () => 4,
	gravity: -10.2,
	pull: 0.001,
	theta: 0.666,
	dragCoeff: 0.02,
	movementThreshold: 1,
	timeStep: 20,
	refresh: 10,
	animate: false,
	maxIterations: 3000,
	maxSimulationTime: 1000,
	ungrabifyWhileSimulating: false,
	fit: false,
	padding: 30,
	randomize: true,
};

const buildSemanticGraph = (graph: SemanticExplorationGraph): ExplorationGraph => ({
	nodes: graph.nodes.map((node) => ({
		id: node.id,
		name: node.name,
		description: node.description,
		synonyms: node.synonyms,
		zones: node.zones,
		layer: 'semantic' as const,
		nodeType: 'term' as const,
		relationshipCount: node.relationship_count,
		columnAttributesCount: node.column_attributes_count,
		sqlAttributesCount: node.sql_attributes_count,
	})),
	links: graph.links.map((link) => ({
		source: link.source,
		target: link.target,
		queries: [],
	})),
});

const getDataNodeType = (tableType: TableType): ExplorationDataNode['nodeType'] => {
	if (tableType === TableType.VIEW) return 'view';
	if (tableType === TableType.MATERIALIZED_VIEW) return 'materialized-view';
	return 'table';
};

const buildDataGraph = (graph: DataExplorationGraph): ExplorationGraph => {
	const nodes: ExplorationDataNode[] = graph.nodes.map((table) => ({
		id: table.id,
		name: table.name,
		description: table.description ?? null,
		layer: 'data',
		nodeType: getDataNodeType(table.table_type as TableType),
		relationshipCount: 0,
		databaseId: table.database_id,
		databaseName: table.database_name,
		schemaId: table.schema_id,
		schemaName: table.schema_name,
		columnsCount: table.columns_count,
		sqlCount: table.sql_count ?? 0,
		termsCount: table.terms_count ?? 0,
		zones: table.zones ?? [],
	}));

	const tableIds = new Set(nodes.map((node) => node.id));
	const links = graph.links.filter(
		(link) => tableIds.has(link.source) && tableIds.has(link.target) && link.queries.length > 0,
	);

	const relationshipCountById = new Map<string, number>();
	links.forEach(({ source, target }) => {
		relationshipCountById.set(source, (relationshipCountById.get(source) ?? 0) + 1);
		relationshipCountById.set(target, (relationshipCountById.get(target) ?? 0) + 1);
	});

	return {
		nodes: nodes.map((node) => ({
			...node,
			relationshipCount: relationshipCountById.get(node.id) ?? 0,
		})),
		links,
	};
};

const getNodeSize = (relationshipCount: number) => {
	if (relationshipCount >= 8) return 140;
	if (relationshipCount >= 4) return 80;
	if (relationshipCount >= 1) return 60;
	return 40;
};

const createElements = (graph: ExplorationGraph): ElementDefinition[] => [
	...graph.nodes.map((node) => {
		const size = getNodeSize(node.relationshipCount);
		return {
			data: {
				id: node.id,
				label: node.layer === 'data' ? node.name.toUpperCase() : node.name,
				size,
				borderRadius: Math.max(10, Math.round(size * 0.2)),
				layer: node.layer,
				nodeType: node.nodeType,
				icon: node.layer === 'data' ? DATA_OBJECT_CONNECTOR_ICON : TERM_OBJECT_ICON,
			},
		};
	}),
	...graph.links.map((link) => ({
		data: {
			id: `${link.source}:${link.target}`,
			source: link.source,
			target: link.target,
		},
	})),
];

type GraphCanvasProps = {
	graph: ExplorationGraph;
	activeNodeId: string | null;
	onSelectNode: (nodeId: string | null) => void;
	onSelectEdge: (edgeId: string) => void;
	onHoverNode: (hoveredNode: HoveredNode | null) => void;
	onControllerChange: (controller: Core | null) => void;
};

type HoveredNode = {
	id: string;
	x: number;
	y: number;
};

const GraphCanvas = ({
	graph,
	activeNodeId,
	onSelectNode,
	onSelectEdge,
	onHoverNode,
	onControllerChange,
}: GraphCanvasProps) => {
	const containerRef = useRef<HTMLDivElement>(null);
	const controllerRef = useRef<Core | null>(null);

	useEffect(() => {
		if (containerRef.current == null) return undefined;

		const controller = cytoscape({
			container: containerRef.current,
			elements: createElements(graph),
			boxSelectionEnabled: false,
			minZoom: 0.2,
			maxZoom: 3,
			style: [
				{
					selector: 'edge',
					style: {
						width: 1,
						'line-color': '#76b900',
						opacity: 0.24,
						'curve-style': 'bezier',
					},
				},
				{
					selector: 'node',
					style: {
						width: 'data(size)',
						height: 'data(size)',
						label: 'data(label)',
						'background-color': '#eef7df',
						'border-color': '#76b900',
						'border-width': 1,
						color: '#27272a',
						'font-size': 12,
						'font-weight': 500,
						'text-halign': 'center',
						'text-valign': 'bottom',
						'text-margin-y': 8,
						'text-max-width': '130px',
						'text-overflow-wrap': 'whitespace',
						'text-wrap': 'ellipsis',
					},
				},
				{
					selector: 'node[layer = "semantic"]',
					style: {
						shape: 'ellipse',
						'background-color': '#e3f4f5',
						'border-color': '#47bac5',
						'background-image': 'data(icon)',
						'background-width': '24px',
						'background-height': '24px',
						'background-fit': 'none',
					},
				},
				{
					selector: 'node[layer = "data"]',
					style: {
						shape: 'roundrectangle',
						'corner-radius': 'data(borderRadius)',
						'background-color': '#fceee8',
						'border-color': '#e4b9a9',
						'background-image': 'data(icon)',
						'background-width': '24px',
						'background-height': '24px',
						'background-fit': 'none',
					},
				},
				{
					selector: 'node.hovered',
					style: {
						'underlay-color': '#76b900',
						'underlay-opacity': 0.14,
						'underlay-padding': 10,
					},
				},
				{
					selector: 'node.selected',
					style: {
						'border-width': 3,
						'border-color': '#76b900',
						'background-color': '#e4f4ca',
						opacity: 1,
					},
				},
				{
					selector: 'node[layer = "semantic"].selected',
					style: {
						'border-color': '#47bac5',
						'background-color': '#d5eff1',
					},
				},
				{
					selector: 'node.dimmed',
					style: {
						opacity: 0.35,
					},
				},
				{
					selector: 'edge.dimmed',
					style: {
						opacity: 0.06,
					},
				},
				{
					selector: 'edge.connected',
					style: {
						width: 2,
						opacity: 0.9,
					},
				},
				{
					selector: 'edge.hovered',
					style: {
						width: 3,
						opacity: 1,
					},
				},
			],
			layout: { name: 'preset' },
		});

		const handleNodeTap = (event: EventObject) => {
			onSelectNode(event.target.id());
		};
		const handleEdgeTap = (event: EventObject) => {
			onSelectEdge(event.target.id());
		};
		const handleBackgroundTap = (event: EventObject) => {
			if (event.target === controller) onSelectNode(null);
		};

		controller.on('tap', 'node', handleNodeTap);
		controller.on('tap', 'edge', handleEdgeTap);
		controller.on('tap', handleBackgroundTap);
		controller.on('mouseover', 'node', (event) => {
			event.target.addClass('hovered');
			const position = event.target.renderedPosition();
			const offset = event.target.renderedWidth() / 2 + 8;
			onHoverNode({
				id: event.target.id(),
				x: position.x + offset,
				y: position.y + offset,
			});
		});
		controller.on('mouseout', 'node', (event) => {
			event.target.removeClass('hovered');
			onHoverNode(null);
		});
		controller.on('drag', 'node', () => onHoverNode(null));
		controller.on('pan zoom', () => onHoverNode(null));
		controller.on('mouseover', 'edge', (event) => event.target.addClass('hovered'));
		controller.on('mouseout', 'edge', (event) => event.target.removeClass('hovered'));
		controller.one('layoutstop', () => {
			controller.zoom(1);
			controller.center();
		});
		const layout = controller.layout(EULER_LAYOUT);
		layout.run();

		const resizeObserver = new ResizeObserver(() => {
			controller.resize();
		});
		resizeObserver.observe(containerRef.current);

		controllerRef.current = controller;
		onControllerChange(controller);

		return () => {
			resizeObserver.disconnect();
			onControllerChange(null);
			layout.stop();
			controller.destroy();
			controllerRef.current = null;
		};
	}, [graph, onControllerChange, onHoverNode, onSelectEdge, onSelectNode]);

	useEffect(() => {
		const controller = controllerRef.current;
		if (controller == null) return;

		controller.nodes().removeClass('selected dimmed');
		controller.edges().removeClass('connected dimmed');
		if (activeNodeId == null) return;

		const activeNode = controller.getElementById(activeNodeId);
		if (activeNode.empty()) return;

		controller.nodes().addClass('dimmed');
		controller.edges().addClass('dimmed');
		activeNode.removeClass('dimmed').addClass('selected');
		activeNode.neighborhood('node').removeClass('dimmed');
		activeNode.connectedEdges().removeClass('dimmed').addClass('connected');
	}, [activeNodeId]);

	return <div ref={containerRef} className="h-full w-full" aria-label="Exploration graph" />;
};

type ViewToggleProps = {
	layer: ExplorationLayer;
	onToggle: () => void;
};

const ViewToggle = ({ layer, onToggle }: ViewToggleProps) => {
	const isSemantic = layer === 'semantic';

	return (
		<button
			type="button"
			onClick={onToggle}
			className="flex h-10 cursor-pointer items-center gap-2 whitespace-nowrap rounded-lg border border-[#76b900] bg-[#76b900] px-3 text-sm font-medium text-white shadow-md transition-colors hover:bg-[#5e9400]"
		>
			<Icon name={isSemantic ? IconName.Database : IconName.Terms} className="h-4 w-4" />
			Switch to {isSemantic ? 'Data Objects' : 'Semantic Objects'}
		</button>
	);
};

type ZoomControlsProps = {
	controller: Core | null;
};

const ZoomControls = ({ controller }: ZoomControlsProps) => {
	const [zoom, setZoom] = useState(1);

	useEffect(() => {
		if (controller == null) return undefined;

		const updateZoom = () => setZoom(controller.zoom());
		const animationFrame = requestAnimationFrame(updateZoom);
		controller.on('zoom', updateZoom);
		return () => {
			cancelAnimationFrame(animationFrame);
			controller.off('zoom', updateZoom);
		};
	}, [controller]);

	const changeZoom = (delta: number) => {
		if (controller == null) return;
		const nextZoom = Math.min(3, Math.max(0.2, controller.zoom() + delta));
		controller.zoom({
			level: nextZoom,
			renderedPosition: {
				x: controller.width() / 2,
				y: controller.height() / 2,
			},
		});
	};

	const resetView = () => {
		if (controller == null) return;
		controller.zoom(1);
		controller.center();
	};

	return (
		<div className="flex items-center gap-2">
			<div className="flex h-10 items-center rounded-lg border border-zinc-200 bg-white shadow-md dark:border-zinc-700 dark:bg-zinc-900">
				<button
					type="button"
					onClick={() => changeZoom(-0.1)}
					className="h-full cursor-pointer px-3 text-lg text-zinc-600 hover:text-[#76b900] dark:text-zinc-300"
					aria-label="Zoom out"
				>
					−
				</button>
				<span className="w-12 text-center text-xs text-zinc-600 dark:text-zinc-300">
					{Math.round(zoom * 100)}%
				</span>
				<button
					type="button"
					onClick={() => changeZoom(0.1)}
					className="h-full cursor-pointer px-3 text-lg text-zinc-600 hover:text-[#76b900] dark:text-zinc-300"
					aria-label="Zoom in"
				>
					+
				</button>
			</div>
			<button
				type="button"
				onClick={resetView}
				className="flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg border border-zinc-200 bg-white text-zinc-600 shadow-md transition-colors hover:text-[#76b900] dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
				aria-label="Reset view to initial position"
				title="Reset view to initial position"
			>
				<svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor">
					<path
						d="M7 3H3v4M13 3h4v4M7 17H3v-4m10 4h4v-4"
						strokeWidth="1.5"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
				</svg>
			</button>
		</div>
	);
};

const HoverNodeCard = ({ node, x, y }: { node: ExplorationNode; x: number; y: number }) => {
	const isSemantic = node.layer === 'semantic';

	return (
		<aside
			// Cytoscape positions are runtime canvas coordinates and cannot be static Tailwind classes.
			// Read-only preview: pointer-events-none so it never intercepts hover/click
			// on the graph beneath it. Actionable links only live in the click card.
			style={{ left: x, top: y }}
			className="pointer-events-none absolute z-30 w-[min(25rem,calc(100%-2rem))] rounded-lg border border-zinc-200 bg-white p-3 shadow-xl dark:border-zinc-700 dark:bg-zinc-900"
		>
			<div className="flex items-start gap-2">
				<Icon
					name={
						node.layer === 'semantic' ? IconName.Terms : getDataNodeIcon(node.nodeType)
					}
					className={`mt-0.5 h-5 w-5 shrink-0 ${
						isSemantic ? 'text-[#47bac5]' : 'text-[#31b9c5]'
					}`}
				/>
				<div className="min-w-0 flex-1">
					<div className="flex items-baseline gap-2">
						<h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
							{node.name}
						</h2>
						<span className="shrink-0 text-xs capitalize text-zinc-400">
							{node.layer === 'semantic' ? 'Term' : node.nodeType.replace('-', ' ')}
						</span>
					</div>
					{node.layer === 'data' && (
						<p className="mt-0.5 truncate text-xs text-zinc-400 dark:text-zinc-500">
							{node.databaseName} • {node.schemaName}
						</p>
					)}
					{node.layer === 'semantic' && node.synonyms.length > 0 && (
						<p className="mt-0.5 truncate text-xs text-zinc-400 dark:text-zinc-500">
							Synonyms: {node.synonyms.join(', ')}
						</p>
					)}
					<p className="mt-1 line-clamp-2 text-xs leading-5 text-zinc-500 dark:text-zinc-400">
						{node.description || 'No Description'}
					</p>
				</div>
			</div>

			{node.layer === 'data' ? (
				<dl className="mt-3 grid grid-cols-2 overflow-hidden rounded-md border border-zinc-200 text-xs dark:border-zinc-700">
					{[
						{ label: 'Columns', value: node.columnsCount },
						{ label: 'SQL Queries', value: node.sqlCount },
						{ label: 'Terms', value: node.termsCount },
						{ label: 'Related Tables', value: node.relationshipCount },
					].map((item, index) => (
						<div
							key={item.label}
							className={`px-3 py-2 ${
								index % 2 === 0
									? 'border-r border-zinc-200 dark:border-zinc-700'
									: ''
							} ${index < 2 ? 'border-b border-zinc-200 dark:border-zinc-700' : ''}`}
						>
							<dt className="text-zinc-400">{item.label}</dt>
							<dd className="mt-0.5 font-medium text-zinc-700 dark:text-zinc-200">
								{item.value}
							</dd>
						</div>
					))}
				</dl>
			) : (
				<dl className="mt-3 grid grid-cols-3 overflow-hidden rounded-md border border-zinc-200 text-xs dark:border-zinc-700">
					{[
						{ label: 'Related Terms', value: node.relationshipCount },
						{ label: 'Attribute Columns', value: node.columnAttributesCount },
						{ label: 'SQL Attributes', value: node.sqlAttributesCount },
					].map((item, index) => (
						<div
							key={item.label}
							className={`px-3 py-2 ${
								index < 2 ? 'border-r border-zinc-200 dark:border-zinc-700' : ''
							}`}
						>
							<dt className="text-zinc-400">{item.label}</dt>
							<dd className="mt-0.5 font-medium text-zinc-700 dark:text-zinc-200">
								{item.value}
							</dd>
						</div>
					))}
				</dl>
			)}

			<ZonesRow zones={node.zones} />
		</aside>
	);
};

// Description text is width-constrained by the caller and truncated; hovering
// reveals the full text in a floating popover instead of relying on the
// native `title` tooltip.
const TruncatedDescription = ({ text }: { text: string | null }) => {
	const [hovered, setHovered] = useState(false);
	const value = text?.trim() ?? '';

	if (value === '') {
		return <span className="italic text-zinc-400 dark:text-zinc-500">No Description</span>;
	}

	return (
		<span
			className="relative inline-block max-w-full"
			onMouseEnter={() => setHovered(true)}
			onMouseLeave={() => setHovered(false)}
		>
			<span className="block truncate">{value}</span>
			{hovered && (
				<span className="absolute left-0 top-full z-40 mt-1 block w-72 max-w-[min(22rem,90vw)] whitespace-normal rounded-lg border border-zinc-200 bg-white p-2.5 text-xs leading-5 text-zinc-600 shadow-xl dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
					{value}
				</span>
			)}
		</span>
	);
};

const DetailLinkButton = ({
	count,
	onClick,
	label,
}: {
	count: number;
	onClick: () => void;
	label: string;
}) =>
	count > 0 ? (
		<button
			type="button"
			onClick={onClick}
			className="cursor-pointer rounded p-0.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-[#76b900] dark:hover:bg-zinc-700"
			aria-label={label}
			title={label}
		>
			<Icon name={IconName.Link} className="h-3.5 w-3.5" />
		</button>
	) : null;

type ActiveTermCardProps = {
	node: ExplorationTermNode;
	onClose: () => void;
	onView: () => void;
	onShowRelationships: () => void;
	onShowColumnAttributes: () => void;
	onShowSqlAttributes: () => void;
};

const ActiveTermCard = ({
	node,
	onClose,
	onView,
	onShowRelationships,
	onShowColumnAttributes,
	onShowSqlAttributes,
}: ActiveTermCardProps) => {
	const [minimized, setMinimized] = useState(false);

	return (
		<section className="absolute bottom-4 left-4 z-20 w-[min(26rem,calc(100%-2rem))] rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
			<header className="flex items-center justify-between border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-700">
				<p className="text-xs font-semibold text-zinc-600 dark:text-zinc-300">
					Showing info on this Semantic Object
				</p>
				<div className="flex items-center gap-1">
					<button
						type="button"
						onClick={() => setMinimized((value) => !value)}
						className="cursor-pointer rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
						aria-label={minimized ? 'Expand term details' : 'Minimize term details'}
					>
						{minimized ? '+' : '−'}
					</button>
					<button
						type="button"
						onClick={onClose}
						className="cursor-pointer rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
						aria-label="Close term details"
					>
						×
					</button>
				</div>
			</header>
			{!minimized && (
				<div className="space-y-3 p-4">
					<div className="flex items-start gap-3">
						<Icon
							name={IconName.Terms}
							className="mt-0.5 h-5 w-5 shrink-0 text-[#76b900]"
						/>
						<div className="min-w-0 flex-1">
							<h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
								{node.name}
							</h2>
							{node.synonyms.length > 0 && (
								<p className="mt-0.5 truncate text-xs text-zinc-400 dark:text-zinc-500">
									Synonyms: {node.synonyms.join(', ')}
								</p>
							)}
							<p className="mt-1 line-clamp-2 text-xs leading-5 text-zinc-500 dark:text-zinc-400">
								{node.description || 'No Description'}
							</p>
						</div>
						<button
							type="button"
							onClick={onView}
							className="shrink-0 cursor-pointer rounded-lg bg-[#76b900] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#5e9400]"
						>
							View Term
						</button>
					</div>
					<div className="flex flex-wrap items-center gap-2">
						<span className="flex items-center gap-1.5 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
							Related Terms: {node.relationshipCount}
							<DetailLinkButton
								count={node.relationshipCount}
								onClick={onShowRelationships}
								label="View related Terms"
							/>
						</span>
						<span className="flex items-center gap-1.5 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
							Attribute Columns: {node.columnAttributesCount}
							<DetailLinkButton
								count={node.columnAttributesCount}
								onClick={onShowColumnAttributes}
								label="View attribute columns"
							/>
						</span>
						<span className="flex items-center gap-1.5 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
							SQL Attributes: {node.sqlAttributesCount}
							<DetailLinkButton
								count={node.sqlAttributesCount}
								onClick={onShowSqlAttributes}
								label="View SQL attributes"
							/>
						</span>
						<span className="flex items-center gap-1.5 text-xs text-zinc-400">
							Zones:
							{node.zones.length > 0 ? (
								node.zones.map((zone) => (
									<ZoneChip
										key={zone.id}
										name={zone.name}
										color={zone.color}
										enabled={zone.enabled}
									/>
								))
							) : (
								<span className="text-zinc-500 dark:text-zinc-400">-</span>
							)}
						</span>
					</div>
				</div>
			)}
		</section>
	);
};

const getDataNodeIcon = (nodeType: ExplorationDataNode['nodeType']) => {
	if (nodeType === 'view') return IconName.View;
	if (nodeType === 'materialized-view') return IconName.MaterializedView;
	return IconName.Table;
};

type ActiveDataCardProps = {
	node: ExplorationDataNode;
	onClose: () => void;
	onView: () => void;
	onShowRelationships: () => void;
	onShowColumns: () => void;
	onShowQueries: () => void;
	onShowTerms: () => void;
};

const ActiveDataCard = ({
	node,
	onClose,
	onView,
	onShowRelationships,
	onShowColumns,
	onShowQueries,
	onShowTerms,
}: ActiveDataCardProps) => {
	const [minimized, setMinimized] = useState(false);

	return (
		<section className="absolute bottom-4 left-4 z-20 w-[min(26rem,calc(100%-2rem))] rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
			<header className="flex items-center justify-between border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-700">
				<p className="text-xs font-semibold text-zinc-600 dark:text-zinc-300">
					Showing info on this Data Object
				</p>
				<div className="flex items-center gap-1">
					<button
						type="button"
						onClick={() => setMinimized((value) => !value)}
						className="cursor-pointer rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
						aria-label={
							minimized
								? 'Expand data object details'
								: 'Minimize data object details'
						}
					>
						{minimized ? '+' : '−'}
					</button>
					<button
						type="button"
						onClick={onClose}
						className="cursor-pointer rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
						aria-label="Close data object details"
					>
						×
					</button>
				</div>
			</header>
			{!minimized && (
				<div className="space-y-3 p-4">
					<div className="flex items-start gap-3">
						<Icon
							name={getDataNodeIcon(node.nodeType)}
							className="mt-0.5 h-5 w-5 shrink-0 text-[#3b82b6]"
						/>
						<div className="min-w-0 flex-1">
							<h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
								{node.name}
							</h2>
							<p className="mt-0.5 truncate text-xs text-zinc-400 dark:text-zinc-500">
								{node.databaseName} • {node.schemaName}
							</p>
							<p className="mt-1 line-clamp-2 text-xs leading-5 text-zinc-500 dark:text-zinc-400">
								{node.description || 'No Description'}
							</p>
						</div>
						<div className="flex shrink-0 items-center gap-1">
							<button
								type="button"
								onClick={onView}
								className="cursor-pointer rounded-lg bg-[#76b900] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#5e9400]"
							>
								View in Data
							</button>
						</div>
					</div>
					<div className="grid grid-cols-2 gap-2">
						<span className="flex items-center justify-between gap-1.5 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
							Columns: {node.columnsCount}
							<DetailLinkButton
								count={node.columnsCount}
								onClick={onShowColumns}
								label="View columns"
							/>
						</span>
						<span className="flex items-center justify-between gap-1.5 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
							SQL Queries: {node.sqlCount}
							<DetailLinkButton
								count={node.sqlCount}
								onClick={onShowQueries}
								label="View SQL queries"
							/>
						</span>
						<span className="flex items-center justify-between gap-1.5 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
							Terms: {node.termsCount}
							<DetailLinkButton
								count={node.termsCount}
								onClick={onShowTerms}
								label="View Terms"
							/>
						</span>
						<span className="flex items-center justify-between gap-1.5 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
							Related Tables: {node.relationshipCount}
							<DetailLinkButton
								count={node.relationshipCount}
								onClick={onShowRelationships}
								label="View related tables"
							/>
						</span>
					</div>
					<ZonesRow zones={node.zones} />
				</div>
			)}
		</section>
	);
};

type RelationshipsModalProps = {
	node: ExplorationNode | null;
	rows: ExplorationNode[];
	onClose: () => void;
	onFocus: (nodeId: string) => void;
};

const RelationshipsModal = ({ node, rows, onClose, onFocus }: RelationshipsModalProps) => {
	const columns: TableColumn<ExplorationNode>[] = [
		{
			key: 'type',
			header: 'Type',
			width: 'w-24',
			cell: (row) => (
				<div className="flex items-center gap-1.5 capitalize">
					<Icon
						name={
							row.layer === 'semantic'
								? IconName.Terms
								: getDataNodeIcon(row.nodeType)
						}
						className="h-4 w-4 text-[#76b900]"
					/>
					{row.layer === 'semantic' ? 'Term' : row.nodeType.replace('-', ' ')}
				</div>
			),
		},
		{
			key: 'name',
			header: 'Name',
			cell: (row) => row.name,
			title: (row) => row.name,
			truncate: true,
		},
		{
			key: 'relationships',
			header: 'Relationships',
			width: 'w-32',
			cell: (row) => row.relationshipCount,
		},
		{
			key: 'link',
			header: '',
			width: 'w-10',
			cell: (row) => (
				<NextLink
					href={
						row.layer === 'semantic'
							? `/terms?focus=${encodeURIComponent(row.id)}`
							: catalogPathFromFocusId(`${row.databaseId}|${row.schemaId}|${row.id}`)
					}
					className="flex h-6 w-6 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-[#76b900] dark:hover:bg-zinc-700"
					aria-label={`Open ${row.name}`}
					title={`Open ${row.name}`}
				>
					<Icon name={IconName.ExternalLink} className="h-3.5 w-3.5" />
				</NextLink>
			),
		},
		{
			key: 'focus',
			header: 'Focus',
			width: 'w-20',
			cell: (row) => (
				<button
					type="button"
					onClick={() => onFocus(row.id)}
					className="cursor-pointer rounded-md border border-zinc-200 px-2 py-1 text-xs font-medium text-zinc-600 transition-colors hover:border-[#76b900] hover:text-[#76b900] dark:border-zinc-700 dark:text-zinc-300"
				>
					Focus
				</button>
			),
		},
	];

	return (
		<Modal open={node != null} onClose={onClose} className="w-full max-w-3xl">
			<header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon name={IconName.Link} className="h-5 w-5 shrink-0 text-[#76b900]" />
					<h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{node?.name} — Related Entities ({rows.length})
					</h2>
				</div>
				<button
					type="button"
					onClick={onClose}
					className="cursor-pointer rounded p-1 text-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
					aria-label="Close related entities"
				>
					×
				</button>
			</header>
			<div className="max-h-[70dvh] overflow-y-auto p-5">
				<Table
					columns={columns}
					rows={rows}
					rowKey={(row) => row.id}
					containerClassName="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700"
					scrollClassName="max-h-[28rem] overflow-auto"
					emptyMessage="No related entities"
				/>
			</div>
		</Modal>
	);
};

type ColumnAttributesModalProps = {
	node: ExplorationTermNode | null;
	onClose: () => void;
};

const ColumnAttributesModal = ({ node, onClose }: ColumnAttributesModalProps) => {
	const [attributes, setAttributes] = useState<ColumnAttribute[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (node == null) return undefined;
		let cancelled = false;

		const load = async () => {
			setLoading(true);
			setError(null);
			const response = await termsApi.getColumnAttributes(node.id);
			if (cancelled) return;
			if (response.error) {
				setError(response.message ?? 'Failed to load attribute columns');
			} else {
				setAttributes(response.data ?? []);
			}
			setLoading(false);
		};

		void load();
		return () => {
			cancelled = true;
		};
	}, [node]);

	const columns: TableColumn<ColumnAttribute>[] = [
		{
			key: 'name',
			header: 'Attribute Name',
			cell: (row) => row.name,
			title: (row) => row.name,
			truncate: true,
			width: 'w-40',
		},
		{
			key: 'description',
			header: 'Description',
			cell: (row) =>
				row.description ? (
					<TruncatedText text={row.description} maxWidthClass="max-w-none" />
				) : (
					'—'
				),
		},
		{
			key: 'sample_values',
			header: 'Sample Values',
			width: 'w-56',
			cell: (row) => <DataTablePills values={row.sample_values ?? []} />,
		},
	];

	return (
		<Modal open={node != null} onClose={onClose} className="w-full max-w-2xl">
			<header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon name={IconName.Column} className="h-5 w-5 shrink-0 text-[#76b900]" />
					<h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{node?.name} — Attribute Columns ({attributes.length})
					</h2>
				</div>
				<button
					type="button"
					onClick={onClose}
					className="cursor-pointer rounded p-1 text-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
					aria-label="Close attribute columns"
				>
					×
				</button>
			</header>
			<div className="max-h-[70dvh] overflow-y-auto p-5">
				{loading ? (
					<div className="flex h-32 items-center justify-center">
						<div
							className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-200 border-t-[#76b900]"
							role="status"
							aria-label="Loading attribute columns"
						/>
					</div>
				) : error != null ? (
					<p className="text-sm text-red-600 dark:text-red-300">{error}</p>
				) : (
					<Table
						columns={columns}
						rows={attributes}
						rowKey={(row) => row.id}
						containerClassName="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700"
						scrollClassName="max-h-[28rem] overflow-auto"
						emptyMessage="No attribute columns"
					/>
				)}
			</div>
		</Modal>
	);
};

type SqlAttributesModalProps = {
	node: ExplorationTermNode | null;
	onClose: () => void;
};

const SqlAttributesModal = ({ node, onClose }: SqlAttributesModalProps) => {
	const [attributes, setAttributes] = useState<SqlAttribute[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (node == null) return undefined;
		let cancelled = false;

		const load = async () => {
			setLoading(true);
			setError(null);
			const response = await termsApi.getSqlAttributes(node.id);
			if (cancelled) return;
			if (response.error) {
				setError(response.message ?? 'Failed to load SQL attributes');
			} else {
				setAttributes(response.data ?? []);
			}
			setLoading(false);
		};

		void load();
		return () => {
			cancelled = true;
		};
	}, [node]);

	return (
		<Modal open={node != null} onClose={onClose} className="w-full max-w-2xl">
			<header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon name={IconName.Link} className="h-5 w-5 shrink-0 text-[#76b900]" />
					<h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{node?.name} — SQL Attributes ({attributes.length})
					</h2>
				</div>
				<button
					type="button"
					onClick={onClose}
					className="cursor-pointer rounded p-1 text-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
					aria-label="Close SQL attributes"
				>
					×
				</button>
			</header>
			<div className="max-h-[70dvh] overflow-y-auto p-5">
				{loading ? (
					<div className="flex h-32 items-center justify-center">
						<div
							className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-200 border-t-[#76b900]"
							role="status"
							aria-label="Loading SQL attributes"
						/>
					</div>
				) : error != null ? (
					<p className="text-sm text-red-600 dark:text-red-300">{error}</p>
				) : attributes.length === 0 ? (
					<p className="text-sm italic text-zinc-500 dark:text-zinc-400">
						No SQL attributes
					</p>
				) : (
					<ul className="space-y-4">
						{attributes.map((attr) => (
							<li
								key={attr.id}
								className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700"
							>
								<h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
									{attr.name}
								</h3>
								<div className="mt-1 max-w-md text-xs text-zinc-500 dark:text-zinc-400">
									<TruncatedDescription text={attr.description} />
								</div>
								<SqlBlock
									className="mt-2"
									sql={attr.expression || attr.sql || ''}
									label="SQL"
								/>
							</li>
						))}
					</ul>
				)}
			</div>
		</Modal>
	);
};

type DataDetailsKind = 'columns' | 'queries' | 'terms';

type DataDetailsModalProps = {
	node: ExplorationDataNode | null;
	kind: DataDetailsKind | null;
	onClose: () => void;
};

const DataDetailsModal = ({ node, kind, onClose }: DataDetailsModalProps) => {
	const [columns, setColumns] = useState<Column[]>([]);
	const [details, setDetails] = useState<TableExplorationDetails>({
		queries: [],
		terms: [],
	});
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (node == null || kind == null) return undefined;
		let cancelled = false;

		const load = async () => {
			setLoading(true);
			setError(null);
			if (kind === 'columns') {
				const response = await datasources.getColumnsForTable(node.id);
				if (cancelled) return;
				if (response.error) {
					setError(response.message ?? 'Failed to load columns');
				} else {
					setColumns(response.data ?? []);
				}
			} else {
				const response = await datasources.getTableExplorationDetails(node.id);
				if (cancelled) return;
				if (response.error) {
					setError(response.message ?? 'Failed to load details');
				} else {
					setDetails(response.data);
				}
			}
			setLoading(false);
		};

		void load();
		return () => {
			cancelled = true;
		};
	}, [kind, node]);

	const title = kind === 'columns' ? 'Columns' : kind === 'queries' ? 'SQL Queries' : 'Terms';

	const renderTable = () => {
		if (kind === 'columns') {
			const tableColumns: TableColumn<Column>[] = [
				{
					key: 'column',
					header: 'Name',
					cell: (row) => row.column_name,
					title: (row) => row.column_name,
					truncate: true,
				},
				{
					key: 'link',
					header: '',
					width: 'w-10',
					cell: (row) =>
						node != null ? (
							<NextLink
								href={catalogPathFromFocusId(
									`${node.databaseId}|${node.schemaId}|${node.id}|${row.id}`,
								)}
								className="flex h-6 w-6 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-[#76b900] dark:hover:bg-zinc-700"
								aria-label={`Open ${row.column_name} in Data`}
								title={`Open ${row.column_name} in Data`}
							>
								<Icon name={IconName.ExternalLink} className="h-3.5 w-3.5" />
							</NextLink>
						) : null,
				},
			];
			return (
				<Table
					columns={tableColumns}
					rows={columns}
					rowKey={(row) => row.id}
					containerClassName="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700"
					scrollClassName="max-h-[28rem] overflow-auto"
					emptyMessage="No columns"
				/>
			);
		}

		if (kind === 'queries') {
			if (details.queries.length === 0) {
				return (
					<p className="text-sm italic text-zinc-500 dark:text-zinc-400">
						No SQL queries
					</p>
				);
			}
			return (
				<ul className="space-y-4">
					{details.queries.map((query, index) => (
						<li key={query.id || `${index}`}>
							<SqlBlock sql={query.sql} label={`Query ${index + 1}`} />
						</li>
					))}
				</ul>
			);
		}

		const termColumns: TableColumn<TableExplorationDetails['terms'][number]>[] = [
			{ key: 'name', header: 'Name', width: 'w-48', cell: (row) => row.name },
			{
				key: 'description',
				header: 'Description',
				cell: (row) => row.description || 'No Description',
				title: (row) => row.description ?? '',
				truncate: true,
			},
			{
				key: 'link',
				header: '',
				width: 'w-10',
				cell: (row) => (
					<NextLink
						href={`/terms?focus=${encodeURIComponent(row.id)}`}
						className="flex h-6 w-6 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-[#76b900] dark:hover:bg-zinc-700"
						aria-label={`Open ${row.name} term`}
						title={`Open ${row.name} term`}
					>
						<Icon name={IconName.ExternalLink} className="h-3.5 w-3.5" />
					</NextLink>
				),
			},
		];
		return (
			<Table
				columns={termColumns}
				rows={details.terms}
				rowKey={(row) => row.id}
				containerClassName="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700"
				scrollClassName="max-h-[28rem] overflow-auto"
				emptyMessage="No Terms"
			/>
		);
	};

	return (
		<Modal open={node != null && kind != null} onClose={onClose} className="w-full max-w-4xl">
			<header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon
						name={
							kind === 'columns'
								? IconName.Column
								: kind === 'terms'
									? IconName.Terms
									: IconName.Link
						}
						className="h-5 w-5 shrink-0 text-[#76b900]"
					/>
					<h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{node?.name} ({title})
					</h2>
				</div>
				<button
					type="button"
					onClick={onClose}
					className="cursor-pointer rounded p-1 text-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
					aria-label={`Close ${title}`}
				>
					×
				</button>
			</header>
			<div className="max-h-[70dvh] overflow-y-auto p-5">
				{loading ? (
					<div className="flex h-32 items-center justify-center">
						<div
							className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-200 border-t-[#76b900]"
							role="status"
							aria-label={`Loading ${title}`}
						/>
					</div>
				) : error != null ? (
					<p className="text-sm text-red-600 dark:text-red-300">{error}</p>
				) : (
					renderTable()
				)}
			</div>
		</Modal>
	);
};

type SemanticRelationshipModalProps = {
	sourceNode: ExplorationTermNode | null;
	targetNode: ExplorationTermNode | null;
	onClose: () => void;
	onView: (nodeId: string) => void;
};

const SemanticRelationshipModal = ({
	sourceNode,
	targetNode,
	onClose,
	onView,
}: SemanticRelationshipModalProps) => {
	const open = sourceNode != null && targetNode != null;

	return (
		<Modal open={open} onClose={onClose} className="w-full max-w-2xl">
			<header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon name={IconName.Connection} className="h-5 w-5 shrink-0 text-[#76b900]" />
					<h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
						{sourceNode?.name} (Term) &lt;&gt; {targetNode?.name} (Term)
					</h2>
				</div>
				<button
					type="button"
					onClick={onClose}
					className="cursor-pointer rounded p-1 text-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
					aria-label="Close term relationship"
				>
					×
				</button>
			</header>
			<div className="p-5">
				<div className="grid grid-cols-2 overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-700">
					{[sourceNode, targetNode].map((node, index) => (
						<div
							key={node?.id ?? index}
							className={
								index === 0 ? 'border-r border-zinc-200 dark:border-zinc-700' : ''
							}
						>
							<div className="border-b border-zinc-200 bg-zinc-50 px-4 py-2.5 dark:border-zinc-700 dark:bg-zinc-800/60">
								<span className="truncate text-xs font-semibold text-zinc-500 dark:text-zinc-400">
									{node?.name}
								</span>
							</div>
							<button
								type="button"
								onClick={() => node != null && onView(node.id)}
								className="flex w-full cursor-pointer items-center justify-between gap-2 px-4 py-3 text-left text-sm text-zinc-700 transition-colors hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800/40"
							>
								<span className="flex min-w-0 items-center gap-1.5">
									<Icon
										name={IconName.Terms}
										className="h-3.5 w-3.5 shrink-0 text-[#76b900]"
									/>
									<span className="truncate">{node?.name}</span>
								</span>
								<Icon
									name={IconName.ExternalLink}
									className="h-4 w-4 shrink-0 text-zinc-400"
								/>
							</button>
						</div>
					))}
				</div>
			</div>
		</Modal>
	);
};

type QueryCarouselModalProps = {
	link: ExplorationLink | null;
	sourceName: string;
	targetName: string;
	onClose: () => void;
};

const QueryCarouselModal = ({ link, sourceName, targetName, onClose }: QueryCarouselModalProps) => {
	const [queryIndex, setQueryIndex] = useState(0);
	const queries = link?.queries ?? [];
	const query = queries[queryIndex] ?? '';

	return (
		<Modal open={link != null} onClose={onClose} className="w-full max-w-3xl">
			<header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-700">
				<div className="flex min-w-0 items-center gap-2">
					<Icon name={IconName.Terms} className="h-5 w-5 shrink-0 text-[#76b900]" />
					<div className="min-w-0">
						<h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
							Query
						</h2>
						<p className="truncate text-xs text-zinc-500 dark:text-zinc-400">
							{sourceName} ↔ {targetName}
						</p>
					</div>
				</div>
				<div className="flex items-center gap-3">
					{queries.length > 1 && (
						<div className="flex items-center gap-2">
							<button
								type="button"
								onClick={() => setQueryIndex((index) => Math.max(0, index - 1))}
								disabled={queryIndex === 0}
								className="cursor-pointer rounded p-1 text-zinc-500 hover:bg-zinc-100 disabled:cursor-default disabled:opacity-30 dark:hover:bg-zinc-700"
								aria-label="Previous query"
							>
								←
							</button>
							<span className="text-xs text-zinc-500">
								{queryIndex + 1}/{queries.length}
							</span>
							<button
								type="button"
								onClick={() =>
									setQueryIndex((index) =>
										Math.min(queries.length - 1, index + 1),
									)
								}
								disabled={queryIndex === queries.length - 1}
								className="cursor-pointer rounded p-1 text-zinc-500 hover:bg-zinc-100 disabled:cursor-default disabled:opacity-30 dark:hover:bg-zinc-700"
								aria-label="Next query"
							>
								→
							</button>
						</div>
					)}
					<button
						type="button"
						onClick={onClose}
						className="cursor-pointer rounded p-1 text-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
						aria-label="Close query"
					>
						×
					</button>
				</div>
			</header>
			<div className="max-h-[70dvh] overflow-y-auto p-5">
				<SqlBlock sql={query} label="SQL Query" />
			</div>
		</Modal>
	);
};

export const ExplorationView = () => {
	const router = useRouter();
	const searchParams = useSearchParams();
	const semanticId = searchParams.get('semanticId');
	const dataId = searchParams.get('dataId');
	const layer: ExplorationLayer =
		dataId != null || searchParams.get('view') === 'data' ? 'data' : 'semantic';
	const activeNodeIdFromUrl = layer === 'semantic' ? semanticId : dataId;
	const [semanticGraph, setSemanticGraph] = useState<ExplorationGraph>(EMPTY_GRAPH);
	const [dataGraph, setDataGraph] = useState<ExplorationGraph>(EMPTY_GRAPH);
	const [semanticLoading, setSemanticLoading] = useState(true);
	const [dataLoading, setDataLoading] = useState(false);
	const [semanticError, setSemanticError] = useState<string | null>(null);
	const [dataError, setDataError] = useState<string | null>(null);
	const [dataLoaded, setDataLoaded] = useState(false);
	const [search, setSearch] = useState('');
	const [activeNodeId, setActiveNodeId] = useState<string | null>(activeNodeIdFromUrl);
	const [hoveredNodePosition, setHoveredNodePosition] = useState<HoveredNode | null>(null);
	const [selectedLinkId, setSelectedLinkId] = useState<string | null>(null);
	const [selectedSemanticEdgeId, setSelectedSemanticEdgeId] = useState<string | null>(null);
	const [relationshipsNodeId, setRelationshipsNodeId] = useState<string | null>(null);
	const [dataDetailsKind, setDataDetailsKind] = useState<DataDetailsKind | null>(null);
	const [columnAttributesNodeId, setColumnAttributesNodeId] = useState<string | null>(null);
	const [sqlAttributesNodeId, setSqlAttributesNodeId] = useState<string | null>(null);
	const [controller, setController] = useState<Core | null>(null);

	useEffect(() => {
		let cancelled = false;

		const loadGraph = async () => {
			setSemanticLoading(true);
			const response = await termsApi.getSemanticExplorationGraph();
			if (cancelled) return;

			if (response.error) {
				setSemanticError(response.message ?? 'Failed to load terms');
				setSemanticLoading(false);
				return;
			}

			const nextGraph = buildSemanticGraph(response.data ?? { nodes: [], links: [] });
			if (cancelled) return;
			setSemanticGraph(nextGraph);
			setSemanticError(null);
			setSemanticLoading(false);
		};

		void loadGraph();
		return () => {
			cancelled = true;
		};
	}, []);

	useEffect(() => {
		if (layer !== 'data' || dataLoaded) return undefined;
		let cancelled = false;

		const loadGraph = async () => {
			setDataLoading(true);
			try {
				const response = await datasources.getDataExplorationGraph();
				if (cancelled) return;
				if (response.error) {
					throw new Error(response.message ?? 'Failed to load data objects');
				}
				const nextGraph = buildDataGraph(response.data ?? { nodes: [], links: [] });
				if (cancelled) return;
				setDataGraph(nextGraph);
				setDataError(null);
				setDataLoaded(true);
			} catch (loadError) {
				if (cancelled) return;
				setDataError(
					loadError instanceof Error ? loadError.message : 'Failed to load data objects',
				);
			} finally {
				if (!cancelled) setDataLoading(false);
			}
		};

		void loadGraph();
		return () => {
			cancelled = true;
		};
	}, [dataLoaded, layer]);

	useEffect(() => {
		setActiveNodeId(activeNodeIdFromUrl);
	}, [activeNodeIdFromUrl]);

	const handleSelectNode = useCallback(
		(nodeId: string | null) => {
			setHoveredNodePosition(null);
			setSelectedLinkId(null);
			setSelectedSemanticEdgeId(null);
			setRelationshipsNodeId(null);
			setDataDetailsKind(null);
			setColumnAttributesNodeId(null);
			setSqlAttributesNodeId(null);
			setActiveNodeId(nodeId);
			let nextUrl = '/exploration';
			if (layer === 'data') {
				nextUrl = nodeId
					? `/exploration?view=data&dataId=${encodeURIComponent(nodeId)}`
					: '/exploration?view=data';
			} else if (nodeId) {
				nextUrl = `/exploration?semanticId=${encodeURIComponent(nodeId)}`;
			}
			router.replace(nextUrl, { scroll: false });
		},
		[layer, router],
	);

	const handleSelectEdge = useCallback(
		(edgeId: string) => {
			if (layer === 'data') setSelectedLinkId(edgeId);
			else setSelectedSemanticEdgeId(edgeId);
		},
		[layer],
	);

	const handleToggleLayer = useCallback(() => {
		setSearch('');
		setActiveNodeId(null);
		setHoveredNodePosition(null);
		setSelectedLinkId(null);
		setSelectedSemanticEdgeId(null);
		setRelationshipsNodeId(null);
		setDataDetailsKind(null);
		setColumnAttributesNodeId(null);
		setSqlAttributesNodeId(null);
		router.replace(layer === 'semantic' ? '/exploration?view=data' : '/exploration', {
			scroll: false,
		});
	}, [layer, router]);

	const handleControllerChange = useCallback((nextController: Core | null) => {
		setController(nextController);
	}, []);
	const handleHoverNode = useCallback((hoveredNode: HoveredNode | null) => {
		setHoveredNodePosition(hoveredNode);
	}, []);

	const graph = layer === 'semantic' ? semanticGraph : dataGraph;
	const loading = layer === 'semantic' ? semanticLoading : dataLoading;
	const error = layer === 'semantic' ? semanticError : dataError;

	const filteredNodes = useMemo(() => {
		const query = search.trim().toLowerCase();
		if (query === '') return [];
		return graph.nodes.filter((node) => node.name.toLowerCase().includes(query));
	}, [graph.nodes, search]);

	const activeNode = useMemo(
		() => graph.nodes.find((node) => node.id === activeNodeId) ?? null,
		[activeNodeId, graph.nodes],
	);
	const relationshipsNode = useMemo(
		() => graph.nodes.find((node) => node.id === relationshipsNodeId) ?? null,
		[graph.nodes, relationshipsNodeId],
	);
	const relatedNodes = useMemo(() => {
		if (relationshipsNodeId == null) return [];
		const relatedIds = new Set<string>();
		graph.links.forEach((link) => {
			if (link.source === relationshipsNodeId) relatedIds.add(link.target);
			if (link.target === relationshipsNodeId) relatedIds.add(link.source);
		});
		return graph.nodes.filter((node) => relatedIds.has(node.id));
	}, [graph.links, graph.nodes, relationshipsNodeId]);
	const columnAttributesNode = useMemo(() => {
		const found = graph.nodes.find((node) => node.id === columnAttributesNodeId) ?? null;
		return found?.layer === 'semantic' ? found : null;
	}, [columnAttributesNodeId, graph.nodes]);
	const sqlAttributesNode = useMemo(() => {
		const found = graph.nodes.find((node) => node.id === sqlAttributesNodeId) ?? null;
		return found?.layer === 'semantic' ? found : null;
	}, [graph.nodes, sqlAttributesNodeId]);
	const hoveredNode = useMemo(
		() => graph.nodes.find((node) => node.id === hoveredNodePosition?.id) ?? null,
		[graph.nodes, hoveredNodePosition?.id],
	);
	const selectedLink = useMemo(
		() =>
			graph.links.find((link) => `${link.source}:${link.target}` === selectedLinkId) ?? null,
		[graph.links, selectedLinkId],
	);
	const selectedLinkSource =
		graph.nodes.find((node) => node.id === selectedLink?.source)?.name ?? '';
	const selectedLinkTarget =
		graph.nodes.find((node) => node.id === selectedLink?.target)?.name ?? '';

	const selectedSemanticEdge = useMemo(
		() =>
			graph.links.find(
				(link) => `${link.source}:${link.target}` === selectedSemanticEdgeId,
			) ?? null,
		[graph.links, selectedSemanticEdgeId],
	);
	const semanticEdgeSourceNode = useMemo(() => {
		const found = graph.nodes.find((node) => node.id === selectedSemanticEdge?.source) ?? null;
		return found?.layer === 'semantic' ? found : null;
	}, [graph.nodes, selectedSemanticEdge?.source]);
	const semanticEdgeTargetNode = useMemo(() => {
		const found = graph.nodes.find((node) => node.id === selectedSemanticEdge?.target) ?? null;
		return found?.layer === 'semantic' ? found : null;
	}, [graph.nodes, selectedSemanticEdge?.target]);

	const graphBackground =
		'bg-[radial-gradient(circle,#e4e4e7_1px,transparent_1px)] bg-[size:8px_8px] dark:bg-[radial-gradient(circle,#3f3f46_1px,transparent_1px)]';

	return (
		<main
			className={`relative h-full min-h-0 w-full overflow-hidden bg-zinc-50 dark:bg-zinc-950 ${graphBackground}`}
		>
			<div className="absolute left-4 right-4 top-4 z-20 flex items-start justify-between gap-4">
				<div className="flex items-start gap-2">
					<div className="relative w-72">
						<SearchInput
							value={search}
							onChange={setSearch}
							placeholder={
								layer === 'semantic' ? 'Search terms…' : 'Search data objects…'
							}
							aria-label={
								layer === 'semantic'
									? 'Search terms in exploration'
									: 'Search data objects in exploration'
							}
							className="h-10 bg-white shadow-md dark:bg-zinc-900"
						/>
						{filteredNodes.length > 0 && (
							<ul className="absolute top-12 max-h-[calc(100dvh-8.5rem)] w-full overflow-y-auto rounded-lg border border-zinc-200 bg-white py-1 shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
								{filteredNodes.map((node) => (
									<li key={node.id}>
										<button
											type="button"
											onClick={() => {
												setSearch('');
												handleSelectNode(node.id);
											}}
											className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-sm text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
										>
											<Icon
												name={
													node.layer === 'semantic'
														? IconName.Terms
														: getDataNodeIcon(node.nodeType)
												}
												className={`h-4 w-4 shrink-0 ${
													node.layer === 'semantic'
														? 'text-[#76b900]'
														: 'text-[#3b82b6]'
												}`}
											/>
											<span className="truncate">{node.name}</span>
										</button>
									</li>
								))}
							</ul>
						)}
					</div>
					<ViewToggle layer={layer} onToggle={handleToggleLayer} />
				</div>
				<ZoomControls controller={controller} />
			</div>

			{loading && (
				<div className="flex h-full items-center justify-center">
					<div
						className="h-10 w-10 animate-spin rounded-full border-2 border-zinc-200 border-t-[#76b900] dark:border-zinc-700"
						role="status"
						aria-label="Loading exploration"
					/>
				</div>
			)}

			{!loading && error != null && (
				<div className="flex h-full items-center justify-center px-6 text-center">
					<div className="rounded-xl border border-red-200 bg-white px-8 py-6 text-sm text-red-700 shadow-lg dark:border-red-900/50 dark:bg-zinc-900 dark:text-red-300">
						Couldn&apos;t load exploration: {error}
					</div>
				</div>
			)}

			{!loading && error == null && graph.nodes.length === 0 && (
				<div className="flex h-full items-center justify-center text-sm text-zinc-500">
					{layer === 'semantic' ? 'No Terms Created Yet' : 'No Data Objects Found'}
				</div>
			)}

			{!loading && error == null && graph.nodes.length > 0 && (
				<GraphCanvas
					graph={graph}
					activeNodeId={activeNodeId}
					onSelectNode={handleSelectNode}
					onSelectEdge={handleSelectEdge}
					onHoverNode={handleHoverNode}
					onControllerChange={handleControllerChange}
				/>
			)}

			{hoveredNode != null && hoveredNodePosition != null && (
				<HoverNodeCard
					node={hoveredNode}
					x={hoveredNodePosition.x}
					y={hoveredNodePosition.y}
				/>
			)}

			{activeNode?.layer === 'semantic' && (
				<ActiveTermCard
					key={activeNode.id}
					node={activeNode}
					onClose={() => handleSelectNode(null)}
					onView={() => router.push(`/terms?focus=${encodeURIComponent(activeNode.id)}`)}
					onShowRelationships={() => setRelationshipsNodeId(activeNode.id)}
					onShowColumnAttributes={() => setColumnAttributesNodeId(activeNode.id)}
					onShowSqlAttributes={() => setSqlAttributesNodeId(activeNode.id)}
				/>
			)}

			{activeNode?.layer === 'data' && (
				<ActiveDataCard
					key={activeNode.id}
					node={activeNode}
					onClose={() => handleSelectNode(null)}
					onView={() =>
						router.push(
							`/data?focus=${encodeURIComponent(
								`${activeNode.databaseId}|${activeNode.schemaId}|${activeNode.id}`,
							)}`,
						)
					}
					onShowRelationships={() => setRelationshipsNodeId(activeNode.id)}
					onShowColumns={() => setDataDetailsKind('columns')}
					onShowQueries={() => setDataDetailsKind('queries')}
					onShowTerms={() => setDataDetailsKind('terms')}
				/>
			)}

			<div className="absolute bottom-4 right-4 z-20 rounded-lg border border-zinc-200 bg-white px-3 py-2 shadow-lg dark:border-zinc-700 dark:bg-zinc-900">
				<p className="text-xs text-zinc-500 dark:text-zinc-400">
					Viewing: {layer === 'semantic' ? 'Semantic Objects' : 'Data Objects'}
				</p>
				{layer === 'semantic' ? (
					<div className="mt-2 flex items-center gap-2">
						<span className="h-3 w-3 rounded-full border border-[#76b900] bg-[#eef7df]" />
						<Icon name={IconName.Terms} className="h-4 w-4 text-[#76b900]" />
						<span className="text-xs font-medium text-zinc-700 dark:text-zinc-200">
							Terms {graph.nodes.length}
						</span>
					</div>
				) : (
					<div className="mt-2 flex items-center gap-3">
						{[
							{
								type: 'table',
								label: 'Tables',
								icon: IconName.Table,
							},
							{
								type: 'view',
								label: 'Views',
								icon: IconName.View,
							},
							{
								type: 'materialized-view',
								label: 'Materialized',
								icon: IconName.MaterializedView,
							},
						].map((item) => (
							<span
								key={item.type}
								className="flex items-center gap-1 text-xs font-medium text-zinc-700 dark:text-zinc-200"
							>
								<Icon name={item.icon} className="h-4 w-4 text-[#3b82b6]" />
								{item.label}{' '}
								{
									graph.nodes.filter(
										(node) =>
											node.layer === 'data' && node.nodeType === item.type,
									).length
								}
							</span>
						))}
					</div>
				)}
			</div>

			<QueryCarouselModal
				key={selectedLinkId ?? 'closed-query'}
				link={selectedLink}
				sourceName={selectedLinkSource}
				targetName={selectedLinkTarget}
				onClose={() => setSelectedLinkId(null)}
			/>
			<RelationshipsModal
				node={relationshipsNode}
				rows={relatedNodes}
				onClose={() => setRelationshipsNodeId(null)}
				onFocus={handleSelectNode}
			/>
			<DataDetailsModal
				node={activeNode?.layer === 'data' ? activeNode : null}
				kind={dataDetailsKind}
				onClose={() => setDataDetailsKind(null)}
			/>
			<ColumnAttributesModal
				node={columnAttributesNode}
				onClose={() => setColumnAttributesNodeId(null)}
			/>
			<SqlAttributesModal
				node={sqlAttributesNode}
				onClose={() => setSqlAttributesNodeId(null)}
			/>
			<SemanticRelationshipModal
				sourceNode={semanticEdgeSourceNode}
				targetNode={semanticEdgeTargetNode}
				onClose={() => setSelectedSemanticEdgeId(null)}
				onView={(nodeId) => router.push(`/terms?focus=${encodeURIComponent(nodeId)}`)}
			/>
		</main>
	);
};
