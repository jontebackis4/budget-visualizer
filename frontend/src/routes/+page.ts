import expenditureAreas from '$lib/data/expenditure_areas.json';
import availableYears from '$lib/data/available_years.json';
import { budgetByYear } from '$lib/data/index';

export function load() {
	return { expenditureAreas, availableYears, budgetByYear };
}
