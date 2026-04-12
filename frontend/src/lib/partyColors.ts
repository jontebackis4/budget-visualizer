export const partyColors: Record<string, string> = {
	S: '#E8112d',   // Socialdemokraterna
	V: '#AF1E2D',   // Vänsterpartiet
	MP: '#83C441',  // Miljöpartiet
	C: '#009933',   // Centerpartiet
	SD: '#DDDD00',  // Sverigedemokraterna
	KD: '#231977',  // Kristdemokraterna
	L: '#6BB7EC',   // Liberalerna
	M: '#52BDEC',   // Moderaterna
};

export function partyColor(party: string): string {
	return partyColors[party] ?? '#888888';
}
