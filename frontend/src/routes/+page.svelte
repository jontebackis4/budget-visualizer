<script lang="ts">
	import * as Plot from '@observablehq/plot';
	import { partyColor } from '$lib/partyColors';

	export let data;

	const { expenditureAreas, availableYears, budgetByYear } = data;

	// --- state ---
	let selectedYear: number = availableYears[availableYears.length - 1];
	let displayMode: 'msek' | 'pct' = 'msek';
	let chartContainer: HTMLDivElement;

	// --- derived ---
	$: budget = budgetByYear[selectedYear];
	$: parties = budget ? Object.keys(budget.parties).sort() : [];
	$: chartData = buildChartData(budget, displayMode);
	$: if (chartContainer && chartData) renderChart(chartData, displayMode, parties);

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
					area_name: areaMap.get(dev.area_id) ?? `UO ${dev.area_id}`,
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
		// y-axis domain: areas in fixed order 1–27
		const yDomain = expenditureAreas.map((a) => a.name);
		const xLabel = mode === 'msek' ? 'Delta från regeringen (MSEK)' : 'Delta från regeringen (%)';

		const validRows = rows.filter((d) => d.value !== null);
		const noDataRows = rows.filter((d) => d.noData);

		const plot = Plot.plot({
			marginLeft: 220,
			marginRight: 20,
			marginTop: 30,
			marginBottom: 40,
			width: chartContainer.clientWidth || 900,
			x: { label: xLabel, grid: true },
			y: { domain: yDomain, label: null },
			color: {
				legend: true,
				domain: parties,
				range: parties.map(partyColor)
			},
			marks: [
				Plot.barX(validRows, {
					x: 'value',
					y: 'area_name',
					fill: 'party',
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
		font-weight: 500;
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
