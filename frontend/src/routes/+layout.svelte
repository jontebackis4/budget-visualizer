<script>
	import '../app.css';
	import { page } from '$app/stores';
</script>

<nav>
	<a href="/" class="site-title">Budgetjämförelsen</a>
	<ul class="nav-links">
		<li><a href="/" class:active={$page.url.pathname === '/'}>Jämför</a></li>
		<li><a href="/om" class:active={$page.url.pathname === '/om'}>Om</a></li>
	</ul>
</nav>

<main>
	<slot />
</main>
