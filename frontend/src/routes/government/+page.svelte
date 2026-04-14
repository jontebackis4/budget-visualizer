<script lang="ts">
	import * as Plot from '@observablehq/plot';

	export let data;

	const { expenditureAreas, availableYears, budgetByYear } = data;

	let selectedYear: number = availableYears[availableYears.length - 1];
	let chartContainer: HTMLDivElement;

	$: budget = budgetByYear[selectedYear];
	$: chartRows = buildChartRows(budget);
	$: if (chartContainer && chartRows) renderChart(chartRows);

	function buildChartRows(budget: (typeof budgetByYear)[number] | undefined) {
		if (!budget) return [];

		const areaMap = new Map(expenditureAreas.map((a) => [a.id, a.name]));

		return budget.government
			.filter((g) => g.amount_ksek !== null)
			.map((g) => ({
				area_id: g.area_id,
				area_name: `${g.area_id}. ${areaMap.get(g.area_id) ?? `UO ${g.area_id}`}`,
				msek: (g.amount_ksek as number) / 1000
			}))
			.sort((a, b) => a.area_id - b.area_id);
	}

	function renderChart(rows: ReturnType<typeof buildChartRows>) {
		const marginLeft = 290;
		const truncate = (s: string, max = 38) =>
			s.length > max ? s.slice(0, max - 1) + '…' : s;

		const yDomain = rows.map((r) => r.area_name);
		const rowHeight = 42;
		const marginTop = 30;
		const marginBottom = 40;
		const allRowBg = yDomain.map((area, i) => ({ area, i }));

		const plot = Plot.plot({
			marginLeft,
			marginRight: 20,
			marginTop,
			marginBottom,
			height: yDomain.length * rowHeight + marginTop + marginBottom,
			width: chartContainer.clientWidth || 900,
			x: { label: 'Anslag (MSEK)', tickFormat: (d: number) => d.toLocaleString('sv-SE') },
			y: { domain: yDomain, label: null, axis: null, paddingInner: 0, paddingOuter: 0 },
			marks: [
				// Alternating row backgrounds
				Plot.barX(allRowBg, {
					x1: 0,
					x2: Math.max(...rows.map((r) => r.msek)) * 1.05,
					y: 'area',
					fill: (d: { i: number }) => (d.i % 2 === 0 ? '#f5f5f5' : '#ffffff'),
					clip: false
				}),
				Plot.gridX(),
				// Y-axis labels
				Plot.axisY({
					tickSize: 0,
					tickFormat: (d: string) => truncate(d),
					textAnchor: 'start',
					dx: -(marginLeft - 8),
					clip: false,
					anchor: 'left',
					fontSize: 13
				}),
				// Government budget bars
				Plot.barX(rows, {
					x: 'msek',
					y: 'area_name',
					fill: '#1a5fa8',
					insetTop: 9,
					insetBottom: 9,
					tip: true,
					title: (d) =>
						`${d.area_name}\n${d.msek.toLocaleString('sv-SE', { maximumFractionDigits: 0 })} MSEK`
				})
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
</div>

<div class="chart-wrapper" bind:this={chartContainer}></div>

<style>
	.controls {
		display: flex;
		align-items: center;
		gap: 1.5rem;
		margin-bottom: 1.25rem;
	}

	.control-group {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	label {
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

	.chart-wrapper {
		overflow-x: auto;
		-webkit-overflow-scrolling: touch;
	}

	.chart-wrapper :global(svg) {
		display: block;
		max-width: 100%;
	}

	.chart-wrapper :global(figure) {
		margin: 0;
	}
</style>
