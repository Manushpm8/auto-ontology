// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useRef } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { UndirectedGraph } from 'graphology';
import Sigma from 'sigma';
import type { EdgeDisplayData, MouseCoords, NodeDisplayData } from 'sigma/types';
import { forceSimulation, forceManyBody, forceLink, forceCollide, forceX, forceY } from 'd3-force';
import type { SimulationNodeDatum, SimulationLinkDatum } from 'd3-force';
import { createNodeImageProgram } from '@sigma/node-image';
import { drawDiscNodeLabel } from 'sigma/rendering';
import type { NodeHoverDrawingFunction, NodeProgramType } from 'sigma/rendering';

import SnowflakeSvg from '@/common/icons/svg/snowflake.svg';
import TermsSvg from '@/common/icons/svg/terms.svg';
import { ExplorationLayer } from '@/enums/exploration';
import type { ExplorationGraph } from '@/types/exploration';

export type HoveredNode = {
	id: string;
	x: number;
	y: number;
};

// Rendered well above the on-screen icon size (see NODE_ICON_PADDING below) so
// the underlying vector art stays crisp once the WebGL texture atlas scales it
// up to fill the largest node radii.
const ICON_RASTER_SIZE = 128;

const DATA_OBJECT_ICON_COLOR = '#31b9c5';
const TERM_OBJECT_ICON_COLOR = '#47bac5';

const buildIcon = (svgMarkup: string, color: string) =>
	encodeURI(`data:image/svg+xml;utf-8,${svgMarkup.replaceAll('currentColor', color)}`);

// Leaves the node's `color` visible as a ring around the icon, mirroring the
// previous Cytoscape look (small fixed icon centered on a larger colored shape).
const NODE_ICON_PADDING = 0.58;

// Built once at module scope: @sigma/node-image caches its texture atlas on
// the returned class itself, so re-creating it on every mount/remount (e.g.
// when toggling between the semantic and data layers) would otherwise
// re-register and re-rasterize the same icons each time.
const NodeIconProgram = createNodeImageProgram({ padding: NODE_ICON_PADDING });

// The canvas is plain WebGL/Canvas2D, not DOM, so it can't pick up Tailwind's
// `dark:` variants — node/label colors have to be swapped by hand based on
// the OS-level color scheme instead. Light values match the previous
// Cytoscape pastel backgrounds; dark values are muted tints of the same hues
// (rather than that same near-white pastel) so nodes read as colored shapes
// instead of glowing white blobs against a black canvas.
const LAYER_COLOR_LIGHT: Record<ExplorationLayer, string> = {
	[ExplorationLayer.Semantic]: '#bfe8ec',
	[ExplorationLayer.Data]: '#fbd9bd',
};
const LAYER_COLOR_DARK: Record<ExplorationLayer, string> = {
	[ExplorationLayer.Semantic]: '#1f3336',
	[ExplorationLayer.Data]: '#332821',
};
const LABEL_COLOR_LIGHT = '#3f3f46';
const LABEL_COLOR_DARK = '#e4e4e7';

// Matches the `bg-zinc-50`/`dark:bg-zinc-950` canvas backdrop set by
// `ExplorationData.tsx`, so dimmed nodes below can be pre-mixed toward it.
const CANVAS_BACKGROUND_LIGHT = '#fafafa';
const CANVAS_BACKGROUND_DARK = '#09090b';

// Matches the light/dark surface colors used by HoverNodeCard and other
// panels (`bg-white` / `dark:bg-zinc-900`), so Sigma's own hover label
// background reads as an intentional themed surface instead of the
// library's hardcoded white pill (which swallowed the light label text
// used in dark mode above).
const HOVER_LABEL_BACKGROUND_LIGHT = '#ffffff';
const HOVER_LABEL_BACKGROUND_DARK = '#18181b';

const prefersDarkMode = () =>
	typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches;

const EDGE_BRAND_COLOR = '#76b900';

const hexToRgb = (hex: string) => {
	const value = hex.replace('#', '');
	return [
		parseInt(value.substring(0, 2), 16),
		parseInt(value.substring(2, 4), 16),
		parseInt(value.substring(4, 6), 16),
	] as const;
};

/** Converts a `#rrggbb` color into an `rgba(...)` string at the given alpha. */
const withAlpha = (hex: string, alpha: number) => {
	const [r, g, b] = hexToRgb(hex);
	return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

// `@sigma/node-image` derives a dimmed node's final on-screen alpha as
// `max(iconAlpha, backgroundAlpha)` rather than actually multiplying them
// together (see its fragment shader), so passing a translucent `rgba(...)`
// for the background `color` — the approach that works fine for edges below
// — doesn't reliably fade the disc, and never fades the (always
// fully-opaque) icon pixels at all. Pre-mixing toward the canvas background
// as a *flat, fully-opaque* color instead sidesteps that shader entirely:
// nothing has to blend at draw time, so dimmed nodes end up looking like an
// honest faded copy of the active ones rather than blending toward white.
const mixTowardColor = (hex: string, target: string, opacity: number) => {
	const [r1, g1, b1] = hexToRgb(hex);
	const [r2, g2, b2] = hexToRgb(target);
	const mix = (a: number, b: number) =>
		Math.round(a * opacity + b * (1 - opacity))
			.toString(16)
			.padStart(2, '0');
	return `#${mix(r1, r2)}${mix(g1, g2)}${mix(b1, b2)}`;
};

// How much of the original color survives in a dimmed node — applied
// uniformly to both the background disc and the icon fill so a dimmed node
// reads as the exact same node, just faded, instead of the icon staying
// fully saturated while only the background around it lightens. Kept low so
// active/connected nodes (drawn at full color) stand out clearly against the
// dimmed ones rather than reading as a similar, only-slightly-lighter hue.
const NODE_DIMMED_OPACITY = 0.22;

const LAYER_COLOR_DIMMED_LIGHT: Record<ExplorationLayer, string> = {
	[ExplorationLayer.Semantic]: mixTowardColor(
		LAYER_COLOR_LIGHT[ExplorationLayer.Semantic],
		CANVAS_BACKGROUND_LIGHT,
		NODE_DIMMED_OPACITY,
	),
	[ExplorationLayer.Data]: mixTowardColor(
		LAYER_COLOR_LIGHT[ExplorationLayer.Data],
		CANVAS_BACKGROUND_LIGHT,
		NODE_DIMMED_OPACITY,
	),
};
const LAYER_COLOR_DIMMED_DARK: Record<ExplorationLayer, string> = {
	[ExplorationLayer.Semantic]: mixTowardColor(
		LAYER_COLOR_DARK[ExplorationLayer.Semantic],
		CANVAS_BACKGROUND_DARK,
		NODE_DIMMED_OPACITY,
	),
	[ExplorationLayer.Data]: mixTowardColor(
		LAYER_COLOR_DARK[ExplorationLayer.Data],
		CANVAS_BACKGROUND_DARK,
		NODE_DIMMED_OPACITY,
	),
};

const LAYER_ICON: Record<ExplorationLayer, string> = {
	[ExplorationLayer.Semantic]: buildIcon(
		renderToStaticMarkup(<TermsSvg width={ICON_RASTER_SIZE} height={ICON_RASTER_SIZE} />),
		TERM_OBJECT_ICON_COLOR,
	),
	[ExplorationLayer.Data]: buildIcon(
		renderToStaticMarkup(<SnowflakeSvg width={ICON_RASTER_SIZE} height={ICON_RASTER_SIZE} />),
		DATA_OBJECT_ICON_COLOR,
	),
};
const LAYER_ICON_DIMMED_LIGHT: Record<ExplorationLayer, string> = {
	[ExplorationLayer.Semantic]: buildIcon(
		renderToStaticMarkup(<TermsSvg width={ICON_RASTER_SIZE} height={ICON_RASTER_SIZE} />),
		mixTowardColor(TERM_OBJECT_ICON_COLOR, CANVAS_BACKGROUND_LIGHT, NODE_DIMMED_OPACITY),
	),
	[ExplorationLayer.Data]: buildIcon(
		renderToStaticMarkup(<SnowflakeSvg width={ICON_RASTER_SIZE} height={ICON_RASTER_SIZE} />),
		mixTowardColor(DATA_OBJECT_ICON_COLOR, CANVAS_BACKGROUND_LIGHT, NODE_DIMMED_OPACITY),
	),
};
const LAYER_ICON_DIMMED_DARK: Record<ExplorationLayer, string> = {
	[ExplorationLayer.Semantic]: buildIcon(
		renderToStaticMarkup(<TermsSvg width={ICON_RASTER_SIZE} height={ICON_RASTER_SIZE} />),
		mixTowardColor(TERM_OBJECT_ICON_COLOR, CANVAS_BACKGROUND_DARK, NODE_DIMMED_OPACITY),
	),
	[ExplorationLayer.Data]: buildIcon(
		renderToStaticMarkup(<SnowflakeSvg width={ICON_RASTER_SIZE} height={ICON_RASTER_SIZE} />),
		mixTowardColor(DATA_OBJECT_ICON_COLOR, CANVAS_BACKGROUND_DARK, NODE_DIMMED_OPACITY),
	),
};

// A translucent edge that reads fine against a near-black dark canvas turns
// almost invisible against a near-white light one at the same alpha (green
// desaturated toward gray blends into `bg-zinc-50`) — so each theme gets its
// own alpha rather than sharing one. Edges are thin strokes rather than
// filled shapes, so (unlike nodes above) letting them fade via real alpha
// reads fine and doesn't hit the node-image shader's `max(...)` quirk,
// which is specific to the image node program.
//
// Every edge — active/connected/hovered or not, and on both the Terms and
// Tables layers, since they share this same code — uses this *one* alpha,
// with only line thickness (see `edgeReducer` below) signalling emphasis.
// A separate, darker/near-opaque alpha for connected/hovered edges used to
// make the brand green look like two different shades of green depending
// on state; keeping a single alpha avoids that "some edges are darker
// green than others" inconsistency.
const EDGE_ALPHA_LIGHT = 0.85;
const EDGE_ALPHA_DARK = 0.7;

// Dimmed edges (unrelated to the active node) used real alpha too, but at a
// low enough value that the brand green desaturated almost entirely into
// the light-mode canvas background — some edges read green, others read
// plain white depending on what happened to be layered underneath. Mixing
// toward the canvas background as a flat, opaque color (same trick used for
// dimmed nodes above) guarantees every edge keeps a visibly green tint.
const EDGE_DIMMED_OPACITY_LIGHT = 0.6;
const EDGE_DIMMED_OPACITY_DARK = 0.5;

const getEdgeColors = (isDark: boolean) => {
	const alpha = isDark ? EDGE_ALPHA_DARK : EDGE_ALPHA_LIGHT;
	const canvasBackground = isDark ? CANVAS_BACKGROUND_DARK : CANVAS_BACKGROUND_LIGHT;
	const dimmedOpacity = isDark ? EDGE_DIMMED_OPACITY_DARK : EDGE_DIMMED_OPACITY_LIGHT;
	return {
		base: withAlpha(EDGE_BRAND_COLOR, alpha),
		dimmed: mixTowardColor(EDGE_BRAND_COLOR, canvasBackground, dimmedOpacity),
	};
};

// Node radii (half of the previous Cytoscape diameters, since Sigma sizes are radii).
const getNodeRadius = (relationshipCount: number) => {
	if (relationshipCount >= 8) return 70;
	if (relationshipCount >= 4) return 40;
	if (relationshipCount >= 1) return 30;
	return 20;
};

/** Attributes stored on every Sigma/graphology node for the Exploration graph. */
type GraphNodeAttributes = {
	x: number;
	y: number;
	size: number;
	label: string;
	color: string;
	image: string;
	type: 'image';
	layer: ExplorationLayer;
};

/** Attributes stored on every Sigma/graphology edge for the Exploration graph. */
type GraphEdgeAttributes = {
	color: string;
	size: number;
};

/** A d3-force particle mirroring one graphology node, kept in sync by ID. */
type SimNode = SimulationNodeDatum & { id: string; size: number };
type SimLink = SimulationLinkDatum<SimNode>;

/** The Sigma renderer instance shared with `ZoomControls`/`ExplorationView`. */
export type GraphController = Sigma<GraphNodeAttributes, GraphEdgeAttributes>;

const buildGraphologyGraph = (
	graph: ExplorationGraph,
	layerColor: Record<ExplorationLayer, string>,
	edgeColor: string,
	containerAspectRatio: number,
) => {
	const graphology = new UndirectedGraph<GraphNodeAttributes, GraphEdgeAttributes>();

	graph.nodes.forEach((node) => {
		graphology.addNode(node.id, {
			x: 0,
			y: 0,
			size: getNodeRadius(node.relationshipCount),
			label: node.layer === ExplorationLayer.Data ? node.name.toUpperCase() : node.name,
			color: layerColor[node.layer],
			image: LAYER_ICON[node.layer],
			type: 'image',
			layer: node.layer,
		});
	});

	graph.links.forEach((link) => {
		if (!graphology.hasNode(link.source) || !graphology.hasNode(link.target)) return;
		if (link.source === link.target || graphology.hasEdge(link.source, link.target)) return;
		graphology.addEdgeWithKey(`${link.source}:${link.target}`, link.source, link.target, {
			color: edgeColor,
			size: 1.5,
		});
	});

	// Nodes just need *some* non-zero, non-overlapping starting position
	// before the force simulation below can take over; a plain random
	// scatter (rather than a perfect circle) is enough, since d3-force
	// doesn't need — and shouldn't get — a pre-arranged shape to relax away
	// from. Sigma auto-fits whatever bounding box the nodes occupy to the
	// container on every frame, but it preserves aspect ratio rather than
	// stretching — so a square-ish scatter inside a wide container would
	// only fill the height, leaving big empty margins on the sides instead
	// of the "spread across the whole page" look from before the Sigma
	// migration. Shaping the scatter after the container's own aspect ratio
	// avoids that from the very first frame, before physics has even had a
	// chance to run.
	const area = Math.max(200 * 200, graphology.order * 6000);
	const scatterHeight = Math.sqrt(area / containerAspectRatio);
	const scatterWidth = scatterHeight * containerAspectRatio;
	graphology.forEachNode((node) => {
		graphology.mergeNodeAttributes(node, {
			x: (Math.random() - 0.5) * scatterWidth,
			y: (Math.random() - 0.5) * scatterHeight,
		});
	});

	return graphology;
};

type GraphCanvasProps = {
	graph: ExplorationGraph;
	activeNodeId: string | null;
	onSelectNode: (nodeId: string | null) => void;
	onSelectEdge: (edgeId: string) => void;
	onHoverNode: (hoveredNode: HoveredNode | null) => void;
	onControllerChange: (controller: GraphController | null) => void;
};

/** Sigma.js-backed graph canvas shared by both the semantic and data Exploration layers. */
export const GraphCanvas = ({
	graph,
	activeNodeId,
	onSelectNode,
	onSelectEdge,
	onHoverNode,
	onControllerChange,
}: GraphCanvasProps) => {
	const containerRef = useRef<HTMLDivElement>(null);
	const activeNodeIdRef = useRef<string | null>(activeNodeId);

	useEffect(() => {
		activeNodeIdRef.current = activeNodeId;
	}, [activeNodeId]);

	useEffect(() => {
		if (containerRef.current == null) return undefined;

		let isDark = prefersDarkMode();
		let edgeColors = getEdgeColors(isDark);
		const containerRect = containerRef.current.getBoundingClientRect();
		const containerAspectRatio =
			containerRect.height > 0 ? containerRect.width / containerRect.height : 1;
		const graphology = buildGraphologyGraph(
			graph,
			isDark ? LAYER_COLOR_DARK : LAYER_COLOR_LIGHT,
			edgeColors.base,
			containerAspectRatio,
		);
		const hoveredNodeIdRef = { current: null as string | null };
		const hoveredEdgeIdRef = { current: null as string | null };

		// Reimplementation of Sigma's own `drawDiscNodeHover`, swapping its
		// hardcoded white label-background pill for a theme-aware one — it
		// reads `isDark` live, so flipping the OS theme (see
		// `handleColorSchemeChange` below) updates it without re-registering.
		const drawNodeHover: NodeHoverDrawingFunction<GraphNodeAttributes, GraphEdgeAttributes> = (
			context,
			data,
			settings,
		) => {
			const { labelSize: size, labelFont: font, labelWeight: weight } = settings;
			context.font = `${weight} ${size}px ${font}`;

			context.fillStyle = isDark ? HOVER_LABEL_BACKGROUND_DARK : HOVER_LABEL_BACKGROUND_LIGHT;
			context.shadowOffsetX = 0;
			context.shadowOffsetY = 0;
			context.shadowBlur = 8;
			context.shadowColor = '#000';
			const PADDING = 2;
			if (typeof data.label === 'string') {
				const textWidth = context.measureText(data.label).width;
				const boxWidth = Math.round(textWidth + 5);
				const boxHeight = Math.round(size + 2 * PADDING);
				const radius = Math.max(data.size, size / 2) + PADDING;
				const angleRadian = Math.asin(boxHeight / 2 / radius);
				const xDeltaCoord = Math.sqrt(Math.abs(radius ** 2 - (boxHeight / 2) ** 2));
				context.beginPath();
				context.moveTo(data.x + xDeltaCoord, data.y + boxHeight / 2);
				context.lineTo(data.x + radius + boxWidth, data.y + boxHeight / 2);
				context.lineTo(data.x + radius + boxWidth, data.y - boxHeight / 2);
				context.lineTo(data.x + xDeltaCoord, data.y - boxHeight / 2);
				context.arc(data.x, data.y, radius, angleRadian, -angleRadian);
				context.closePath();
				context.fill();
			} else {
				context.beginPath();
				context.arc(data.x, data.y, data.size + PADDING, 0, Math.PI * 2);
				context.closePath();
				context.fill();
			}
			context.shadowOffsetX = 0;
			context.shadowOffsetY = 0;
			context.shadowBlur = 0;

			drawDiscNodeLabel(context, data, settings);
		};

		// Re-evaluated every frame by the continuously running physics layout
		// below, so selection/hover state changes surface within one frame
		// without needing to force a manual re-render.
		// Sigma's own `NodeDisplayData` type doesn't know about the `image`
		// attribute `@sigma/node-image` reads off the merged node data at
		// render time, so the reducer's return type has to be widened past
		// what `Sigma.Settings['nodeReducer']` declares in order to swap it
		// per-node below.
		const nodeReducer = (
			node: string,
			data: GraphNodeAttributes,
		): Partial<NodeDisplayData> & Pick<GraphNodeAttributes, 'image'> => {
			const currentActiveNodeId = activeNodeIdRef.current;
			const isActive = currentActiveNodeId === node;
			const isHovered = hoveredNodeIdRef.current === node;
			const isDimmed =
				currentActiveNodeId != null &&
				!isActive &&
				!graphology.areNeighbors(currentActiveNodeId, node);

			const layerColorDimmed = isDark ? LAYER_COLOR_DIMMED_DARK : LAYER_COLOR_DIMMED_LIGHT;
			const layerIconDimmed = isDark ? LAYER_ICON_DIMMED_DARK : LAYER_ICON_DIMMED_LIGHT;

			return {
				...data,
				color: isDimmed ? layerColorDimmed[data.layer] : data.color,
				image: isDimmed ? layerIconDimmed[data.layer] : LAYER_ICON[data.layer],
				zIndex: isActive || isHovered ? 1 : 0,
				highlighted: isActive || isHovered,
			};
		};

		const edgeReducer = (edge: string, data: GraphEdgeAttributes): Partial<EdgeDisplayData> => {
			const currentActiveNodeId = activeNodeIdRef.current;
			const isHovered = hoveredEdgeIdRef.current === edge;
			const isConnected =
				currentActiveNodeId != null &&
				graphology.extremities(edge).includes(currentActiveNodeId);
			const isDimmed = currentActiveNodeId != null && !isConnected;

			// Emphasis for connected/hovered edges comes only from thickness now —
			// they stay the exact same green as every other edge, just drawn
			// wider, instead of also switching to a darker/more-opaque shade.
			let size = data.size;
			if (isHovered) size = 3.5;
			else if (isConnected) size = 2.5;

			return { ...data, color: isDimmed ? edgeColors.dimmed : edgeColors.base, size };
		};

		const renderer = new Sigma<GraphNodeAttributes, GraphEdgeAttributes>(
			graphology,
			containerRef.current,
			{
				// "screen" (the default) still makes nodes grow when zooming in
				// (scaled by camera ratio), but keeps their pixel size decoupled
				// from the graph's raw coordinate spread. With "positions" the
				// on-screen size is scaled by how spread out the *whole* graph
				// currently is, which constantly shifts while the physics runs
				// forever — nodes would balloon or shrink to invisible dots as
				// the simulation's bounding box changes, independently of zoom.
				itemSizesReference: 'screen',
				minCameraRatio: 0.3,
				maxCameraRatio: 5,
				// The container can briefly report a zero size during layer
				// switches/route transitions before the surrounding flex layout
				// settles; falling back to a 1px size for that one frame avoids a
				// hard crash instead of forcing every consumer to guard against it.
				allowInvalidContainer: true,
				enableEdgeEvents: true,
				zIndex: true,
				labelColor: { color: isDark ? LABEL_COLOR_DARK : LABEL_COLOR_LIGHT },
				defaultDrawNodeHover: drawNodeHover,
				// @sigma/node-image's program class is typed generically over the
				// default `Attributes` type; our stricter node attributes are a
				// compatible subtype at runtime, so this cast is safe.
				nodeProgramClasses: {
					image: NodeIconProgram as unknown as NodeProgramType<
						GraphNodeAttributes,
						GraphEdgeAttributes
					>,
				},
				nodeReducer,
				edgeReducer,
			},
		);

		// Mirror the graphology graph as a d3-force simulation. This replaces
		// graphology-layout-force, whose repulsion never decays with distance
		// (it's a constant push per node pair, regardless of how far apart
		// they already are). That makes any group of nodes with similar
		// connectivity — e.g. dozens of terms all linked only to one shared
		// "User"/"Zone" node — settle at the *same* radius from their hub no
		// matter how the force constants are tuned, since nothing but the
		// (identical, quadratic-in-distance) attraction differs between them:
		// the whole graph reliably collapses onto one big ring.
		//
		// d3-force's `forceManyBody` charge decays with distance (Barnes-Hut
		// approximated), so only nodes that are *actually* pulled together by
		// real edges end up close, while unrelated nodes/clusters can settle
		// at very different distances — producing the same kind of organic,
		// clustered layout the previous Cytoscape/euler setup had.
		// `forceLink` also weakens its own pull automatically for high-degree
		// hub nodes, so one heavily-connected node doesn't flatten all its
		// neighbours onto a uniform circle either.
		const simNodesById = new Map<string, SimNode>();
		const simNodes: SimNode[] = graphology.mapNodes((node, attributes) => {
			const simNode: SimNode = {
				id: node,
				size: attributes.size,
				x: attributes.x,
				y: attributes.y,
			};
			simNodesById.set(node, simNode);
			return simNode;
		});
		const simLinks: SimLink[] = graphology.mapEdges((_edge, _attributes, source, target) => ({
			source,
			target,
		}));

		const simulation = forceSimulation<SimNode>(simNodes)
			.force('charge', forceManyBody<SimNode>().strength(-450).distanceMax(1000))
			.force(
				'link',
				forceLink<SimNode, SimLink>(simLinks)
					.id((node) => node.id)
					.distance(140),
			)
			.force(
				'collide',
				forceCollide<SimNode>((node) => node.size + 6),
			)
			// A very weak pull toward the origin — just enough to stop the
			// whole graph drifting off-center over time, far too weak to
			// override the clustering forces above (that imbalance, gravity
			// dominating attraction, was what caused the ring artifact).
			//
			// Crucially, the x/y strengths are *not* equal: repulsion and
			// collision are radially symmetric, so an equal pull in both
			// axes always relaxes toward a roughly circular/square blob,
			// regardless of how wide the initial scatter was — leaving big
			// empty margins on the sides of a wide container. Weakening the
			// pull along the container's long axis (and strengthening it
			// along the short one) biases the settled shape itself into an
			// ellipse matching the container, so the layout actually uses
			// the full width instead of shrinking back to a centered blob.
			.force('x', forceX<SimNode>(0).strength(0.02 / containerAspectRatio))
			.force('y', forceY<SimNode>(0).strength(0.02 * containerAspectRatio))
			.on('tick', () => {
				graphology.updateEachNodeAttributes(
					(node, attributes) => {
						const simNode = simNodesById.get(node);
						if (simNode?.x == null || simNode.y == null) return attributes;
						return { ...attributes, x: simNode.x, y: simNode.y };
					},
					{ attributes: ['x', 'y'] },
				);
			});

		let draggedNode: string | null = null;

		const handleDownNode = ({ node }: { node: string }) => {
			draggedNode = node;
			const simNode = simNodesById.get(node);
			if (simNode) {
				simNode.fx = simNode.x;
				simNode.fy = simNode.y;
			}
			// Reheats the simulation (like d3's standard drag pattern) so the
			// dragged node's neighbours keep reacting live for as long as it's
			// held, the same way Neo4j Browser's physics behaves, instead of
			// staying frozen at whatever alpha the initial layout settled to.
			simulation.alphaTarget(0.3).restart();
		};
		const stopDrag = () => {
			if (draggedNode != null) {
				const simNode = simNodesById.get(draggedNode);
				if (simNode) {
					simNode.fx = null;
					simNode.fy = null;
				}
			}
			draggedNode = null;
			simulation.alphaTarget(0);
		};
		const handleMoveBody = (coords: MouseCoords) => {
			if (draggedNode == null) return;
			const simNode = simNodesById.get(draggedNode);
			if (!simNode) return;
			const position = renderer.viewportToGraph({ x: coords.x, y: coords.y });
			simNode.fx = position.x;
			simNode.fy = position.y;
			coords.preventSigmaDefault();
		};

		const handleClickStage = () => onSelectNode(null);
		const handleClickNode = ({ node }: { node: string }) => onSelectNode(node);
		const handleClickEdge = ({ edge }: { edge: string }) => onSelectEdge(edge);
		const handleEnterNode = ({ node, event }: { node: string; event: MouseCoords }) => {
			hoveredNodeIdRef.current = node;
			const displayData = renderer.getNodeDisplayData(node);
			const radius = displayData ? renderer.scaleSize(displayData.size) : 0;
			onHoverNode({ id: node, x: event.x + radius + 8, y: event.y + radius + 8 });
		};
		const handleLeaveNode = () => {
			hoveredNodeIdRef.current = null;
			onHoverNode(null);
		};
		const handleEnterEdge = ({ edge }: { edge: string }) => {
			hoveredEdgeIdRef.current = edge;
		};
		const handleLeaveEdge = () => {
			hoveredEdgeIdRef.current = null;
		};
		const handleCameraUpdated = () => onHoverNode(null);

		renderer.on('downNode', handleDownNode);
		renderer.on('clickStage', handleClickStage);
		renderer.on('clickNode', handleClickNode);
		renderer.on('clickEdge', handleClickEdge);
		renderer.on('enterNode', handleEnterNode);
		renderer.on('leaveNode', handleLeaveNode);
		renderer.on('enterEdge', handleEnterEdge);
		renderer.on('leaveEdge', handleLeaveEdge);
		renderer.getMouseCaptor().on('mousemovebody', handleMoveBody);
		renderer.getMouseCaptor().on('mouseup', stopDrag);
		renderer.getMouseCaptor().on('mouseleave', stopDrag);
		renderer.getCamera().on('updated', handleCameraUpdated);

		// The graph starts from a random scatter (see `buildGraphologyGraph`
		// above), so for the first moment every edge is stretched across a
		// huge, chaotic area rather than connecting its two settled
		// endpoints. Those long, criss-crossing, mostly-empty-space edges
		// read as faint/washed-out at the same alpha that looks like a solid
		// brand green once the layout has actually settled — not a wrong
		// color, just a much sparser one. Hiding the canvas for that one
		// unsettled beat (instead of trying to make the color read correctly
		// while mid-scatter) means the very first thing anyone sees is
		// already-settled, correctly-colored edges.
		containerRef.current.style.transition = 'none';
		containerRef.current.style.opacity = '0';

		// Give the layout a moment to settle from its random scatter, then
		// fit/center the view once, mirroring the previous "layout stop" reset.
		const centerTimeout = window.setTimeout(() => {
			renderer.refresh();
			void renderer.getCamera().animatedReset();
			if (containerRef.current) {
				containerRef.current.style.transition = 'opacity 300ms ease-out';
				containerRef.current.style.opacity = '1';
			}
		}, 1200);

		// The OS color scheme can change without remounting this component
		// (e.g. the system switching themes at sunset); react to it live
		// instead of leaving stale light/dark colors until the next navigation.
		const colorSchemeQuery = window.matchMedia('(prefers-color-scheme: dark)');
		const handleColorSchemeChange = (event: MediaQueryListEvent) => {
			isDark = event.matches;
			edgeColors = getEdgeColors(isDark);
			const nextLayerColor = isDark ? LAYER_COLOR_DARK : LAYER_COLOR_LIGHT;
			graphology.forEachNode((node, attributes) => {
				graphology.setNodeAttribute(node, 'color', nextLayerColor[attributes.layer]);
			});
			renderer.setSetting('labelColor', {
				color: isDark ? LABEL_COLOR_DARK : LABEL_COLOR_LIGHT,
			});
			renderer.refresh();
		};
		colorSchemeQuery.addEventListener('change', handleColorSchemeChange);

		const resizeObserver = new ResizeObserver((entries) => {
			// ResizeObserver fires immediately on observe() and again during
			// layer/route transitions where the flex layout can momentarily
			// collapse the container to 0×0; skip those frames instead of
			// resizing into (and rendering at) an invalid size.
			const { width, height } = entries[0]?.contentRect ?? { width: 0, height: 0 };
			if (width === 0 || height === 0) return;
			renderer.resize();
		});
		resizeObserver.observe(containerRef.current);

		onControllerChange(renderer);

		return () => {
			window.clearTimeout(centerTimeout);
			colorSchemeQuery.removeEventListener('change', handleColorSchemeChange);
			resizeObserver.disconnect();
			onControllerChange(null);
			simulation.stop();
			renderer.kill();
		};
	}, [graph, onControllerChange, onHoverNode, onSelectEdge, onSelectNode]);

	return <div ref={containerRef} className="h-full w-full" aria-label="Exploration graph" />;
};
