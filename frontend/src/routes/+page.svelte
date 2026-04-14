<script lang="ts">
	import * as Plot from '@observablehq/plot';
	import { partyColor } from '$lib/partyColors';

	export let data;

	const { expenditureAreas, availableYears, budgetByYear } = data;

	// --- state ---
	let selectedYear: number = availableYears[availableYears.length - 1];
	let displayMode: 'msek' | 'pct' = 'msek';
	let chartContainer: HTMLDivElement;
	let selectedParties: string[] = [];

	// --- derived ---
	$: budget = budgetByYear[selectedYear];
	$: parties = budget ? Object.keys(budget.parties).sort() : [];
	$: selectedParties = [...parties]; // reset when year/parties change
	$: chartData = buildChartData(budget, displayMode);
	$: filteredRows = chartData.filter((d) => selectedParties.includes(d.party));
	$: if (chartContainer && filteredRows) renderChart(filteredRows, displayMode, selectedParties);

	function toggleParty(party: string) {
		if (selectedParties.includes(party)) {
			selectedParties = selectedParties.filter((p) => p !== party);
		} else {
			selectedParties = [...selectedParties, party];
		}
	}

	function buildChartData(
		budget: (typeof budgetByYear)[number] | undefined,
		mode: 'msek' | 'pct'
	) {
		if (!budget) return [];

		const areaMap = new Map(expenditureAreas.map((a) => [a.id, a.name]));
		const govMap = new Map(budget.government.map((g) => [g.area_id, g.amount_ksek]));

		const rows: {
			area_id: number;
			area_name: string;
			party: string;
			value: number | null;
			noData: boolean;
		}[] = [];

		for (const [party, deviations] of Object.entries(budget.parties)) {
			for (const dev of deviations) {
				const govKsek = govMap.get(dev.area_id) ?? null;
				let value: number | null = null;

				if (dev.delta_ksek !== null) {
					if (mode === 'msek') {
						value = dev.delta_ksek / 1000;
					} else if (govKsek && govKsek !== 0) {
						value = (dev.delta_ksek / govKsek) * 100;
					}
				}

				rows.push({
					area_id: dev.area_id,
					area_name: `${dev.area_id}. ${areaMap.get(dev.area_id) ?? `UO ${dev.area_id}`}`,
					party,
					value,
					noData: dev.delta_ksek === null
				});
			}
		}

		return rows;
	}

	function renderChart(
		rows: ReturnType<typeof buildChartData>,
		mode: 'msek' | 'pct',
		parties: string[]
	) {
		const marginLeft = 270;
		const truncate = (s: string, max = 36) =>
			s.length > max ? s.slice(0, max - 1) + '…' : s;

		// y-axis domain: areas in fixed order 1–27
		const yDomain = expenditureAreas.map((a) => `${a.id}. ${a.name}`);
		const xLabel = mode === 'msek' ? 'Delta från regeringen (MSEK)' : 'Delta från regeringen (%)';

		const validRows = rows.filter((d) => d.value !== null);
		const noDataRows = rows.filter((d) => d.noData);

		// Compute x domain from actual data so stripe marks don't skew it
		const allValues = validRows.map((d) => d.value as number);
		const xMin = allValues.length ? Math.min(0, ...allValues) : -1;
		const xMax = allValues.length ? Math.max(0, ...allValues) : 1;

		// All rows get an explicit background so margins look consistent
		const allRows = yDomain.map((area, i) => ({ area, i }));

		const rowHeight = 42;
		const marginTop = 30;
		const marginBottom = 40;

		const plot = Plot.plot({
			marginLeft,
			marginRight: 20,
			marginTop,
			marginBottom,
			height: yDomain.length * rowHeight + marginTop + marginBottom,
			width: chartContainer.clientWidth || 900,
			x: { label: xLabel, domain: [xMin, xMax] },
			y: { domain: yDomain, label: null, axis: null, paddingInner: 0, paddingOuter: 0 },
			color: {
				legend: true,
				domain: parties,
				range: parties.map(partyColor)
			},
			marks: [
				// Row backgrounds — clip:false lets them extend into the label margin
				Plot.barX(allRows, {
					x1: -1e9,
					x2: 1e9,
					y: 'area',
					fill: (d: { i: number }) => (d.i % 2 === 0 ? '#f5f5f5' : '#ffffff'),
					clip: false
				}),
				// Grid lines drawn after backgrounds so they remain visible
				Plot.gridX(),
				// Left-aligned, truncated y-axis labels
				Plot.axisY({
					tickSize: 0,
					tickFormat: (d: string) => truncate(d),
					textAnchor: 'start',
					dx: -(marginLeft - 8),
					clip: false,
					anchor: 'left',
					fontSize: 13
				}),
				Plot.link(validRows, {
					x1: 0,
					x2: 'value',
					y: 'area_name',
					stroke: 'party',
					strokeWidth: 3,
					strokeLinecap: 'round'
				}),
				Plot.dot(validRows, {
					x: 'value',
					y: 'area_name',
					fill: 'party',
					r: 6,
					tip: true,
					title: (d) => {
						const sign = (d.value ?? 0) > 0 ? '+' : '';
						const formatted =
							mode === 'msek'
								? `${sign}${(d.value ?? 0).toLocaleString('sv-SE', { maximumFractionDigits: 0 })} MSEK`
								: `${sign}${(d.value ?? 0).toLocaleString('sv-SE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
						return `${d.party}: ${formatted}`;
					}
				}),
				...(noDataRows.length > 0
					? [
							Plot.tickX(noDataRows, {
								x: 0,
								y: 'area_name',
								stroke: '#cccccc',
								strokeWidth: 8,
								title: (d: (typeof noDataRows)[0]) => `${d.party}: Ingen uppgift`
							})
						]
					: []),
				Plot.ruleX([0])
			]
		});

		chartContainer.replaceChildren(plot);
	}
</script>

<div class="controls">
	<div class="control-group">
		<label for="year-select">År</label>
		<select id="year-select" bind:value={selectedYear}>
			{#each availableYears as year}
				<option value={year}>{year}</option>
			{/each}
		</select>
	</div>

	<div class="control-group">
		<span class="label">Partier</span>
		<div class="party-chips">
			{#each parties as party}
				<button
					class="chip"
					class:active={selectedParties.includes(party)}
					style="--party-color: {partyColor(party)}"
					on:click={() => toggleParty(party)}
				>
					{party}
				</button>
			{/each}
		</div>
	</div>

	<div class="control-group">
		<span class="label">Visa</span>
		<div class="toggle">
			<button
				class:active={displayMode === 'msek'}
				on:click={() => (displayMode = 'msek')}
			>
				MSEK
			</button>
			<button
				class:active={displayMode === 'pct'}
				on:click={() => (displayMode = 'pct')}
			>
				%
			</button>
		</div>
	</div>
</div>

<div class="chart-wrapper" bind:this={chartContainer}></div>

<style>
	.controls {
		display: flex;
		align-items: center;
		gap: 1.5rem;
		margin-bottom: 1.25rem;
		flex-wrap: wrap;
	}

	.control-group {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	label,
	.label {
		font-size: 0.875rem;
		color: var(--color-text-muted);
		font-weight: 700;
	}

	select {
		font-size: 0.9rem;
		padding: 0.3rem 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		background: var(--color-surface);
		cursor: pointer;
	}

	.toggle {
		display: flex;
		border: 1px solid var(--color-border);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.toggle button {
		font-size: 0.875rem;
		padding: 0.3rem 0.75rem;
		border: none;
		background: var(--color-surface);
		cursor: pointer;
		color: var(--color-text-muted);
	}

	.toggle button:first-child {
		border-right: 1px solid var(--color-border);
	}

	.toggle button.active {
		background: var(--color-accent);
		color: #fff;
	}

	.party-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.375rem;
	}

	.chip {
		font-size: 0.8rem;
		padding: 0.25rem 0.6rem;
		border-radius: 999px;
		border: 2px solid var(--party-color, #999);
		background: transparent;
		color: var(--color-text-muted);
		cursor: pointer;
		transition: background 0.1s, color 0.1s;
	}

	.chip.active {
		background: var(--party-color, #999);
		color: #fff;
	}

	.chart-wrapper {
		overflow-x: auto;
		-webkit-overflow-scrolling: touch;
	}

	/* Observable Plot SVG fills the wrapper */
	.chart-wrapper :global(svg) {
		display: block;
		max-width: 100%;
	}

	/* Legend sits above the chart */
	.chart-wrapper :global(figure) {
		margin: 0;
	}
</style>
