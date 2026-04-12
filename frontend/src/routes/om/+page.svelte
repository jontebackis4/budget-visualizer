<svelte:head>
	<title>Om – Budgetjämförelsen</title>
</svelte:head>

<h1>Om Budgetjämförelsen</h1>

<section>
	<h2>Vad är det här?</h2>
	<p>
		Budgetjämförelsen visar hur riksdagspartiernas budgetmotioner skiljer sig från regeringens
		budgetproposition, uppdelat på de 27 officiella utgiftsområdena (UO 1–27).
	</p>
	<p>
		Staplarna visar <em>delta</em> – hur mycket mer eller mindre ett parti föreslår att anslå
		jämfört med regeringens förslag för samma år.
	</p>
</section>

<section>
	<h2>Källor</h2>
	<p>
		Beloppen är hämtade från sammanfattningstabellerna i respektive dokument publicerat på
		<a href="https://riksdagen.se" target="_blank" rel="noopener noreferrer">riksdagen.se</a>.
		Dokumenten pekas ut manuellt i källmanifestet och extraheras med hjälp av Claude API.
	</p>
</section>

<section>
	<h2>Enheter och avrundning</h2>
	<p>
		Beloppen lagras i tusental kronor (KSEK) och visas i miljoner kronor (MSEK). Partierna avrundar
		ibland till närmaste 10 eller 100 MSEK i sina motioner – dessa siffror redovisas
		som de rapporteras i källa.
	</p>
</section>

<section>
	<h2>Begränsningar</h2>
	<ul>
		<li>Endast utgiftsområdestotaler – ingen anslagsnivå.</li>
		<li>Enbart budgetmotioner (höstbudget), inte vårproposition.</li>
		<li>ESV-utfall (faktiskt utfall) ingår inte.</li>
	</ul>
</section>

<style>
	h1 {
		font-size: 1.5rem;
		margin-bottom: 1.5rem;
	}

	h2 {
		font-size: 1.1rem;
		margin-top: 2rem;
		margin-bottom: 0.5rem;
	}

	section {
		max-width: 640px;
	}

	p,
	li {
		line-height: 1.65;
		color: #333;
	}

	ul {
		padding-left: 1.25rem;
	}
</style>
